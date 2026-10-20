"""SQLite store for the admin panel: accounts, server-side sessions and a sign-in log.

Sessions live here, not in a signed cookie, so signing out or changing a
password really ends them. The cookie only carries a random token, and the
database only keeps that token's SHA-256, so a copied database file can't be
replayed as a login. Passwords are hashed with scrypt (via Werkzeug).

Every query is parameterized. The file is created readable by its owner only.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
MIN_PASSWORD = 12
MAX_PASSWORD = 1024  # hashing cost guard
IDLE_TIMEOUT_S = 30 * 60
ABSOLUTE_TIMEOUT_S = 12 * 60 * 60
# Failed sign-ins inside the window before the username, or the address, is locked out.
LOCKOUT_WINDOW_S = 15 * 60
MAX_FAILURES_PER_USER = 5
MAX_FAILURES_PER_IP = 20
EVENT_RETENTION_S = 90 * 24 * 60 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    password_changed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_events (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    username TEXT NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS login_events_user ON login_events (username, at);
CREATE INDEX IF NOT EXISTS login_events_ip ON login_events (ip, at);
"""

# Checked against when the username doesn't exist, so a miss takes as long as a wrong password.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))
FAILURE_OUTCOMES = ("bad_password", "unknown_user")
# Fixed queries, one per lockout key: nothing is ever formatted into SQL.
_FAILURE_SQL = {
    "ip": "SELECT COUNT(*) FROM login_events WHERE ip = ? AND at > ? AND outcome IN (?, ?)",
    "username": "SELECT COUNT(*) FROM login_events WHERE username = ? AND at > ? AND outcome IN (?, ?)",
}


class AuthError(Exception):
    """A sign-in that didn't succeed. The message is safe to show: it never says which part was wrong."""


@dataclass
class Session:
    token: str  # only known right after creation; empty when loaded back
    user_id: int
    username: str
    csrf_token: str
    created_at: float
    last_seen: float
    ip: str
    user_agent: str
    token_hash: str = ""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"password must be at most {MAX_PASSWORD} characters")


class AdminDB:
    """Accounts, sessions and the sign-in log in one SQLite file."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            # Create it owner-only before SQLite opens it (no-op permissions on Windows).
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    # Accounts

    def create_user(self, username: str, password: str) -> int:
        name = normalize_username(username)
        if not USERNAME_PATTERN.match(name):
            raise ValueError("username must be 3-32 characters: letters, digits, '.', '_' or '-'")
        validate_password(password)
        now = self.clock()
        try:
            with self._connect() as db:
                cur = db.execute("INSERT INTO users (username, password_hash, created_at, password_changed_at) "
                                 "VALUES (?, ?, ?, ?)", (name, generate_password_hash(password), now, now))
        except sqlite3.IntegrityError:
            raise ValueError(f"user {name!r} already exists") from None
        return int(cur.lastrowid)

    def set_password(self, username: str, password: str) -> None:
        """Change a password and end every session that user has."""
        validate_password(password)
        name = normalize_username(username)
        with self._connect() as db:
            cur = db.execute("UPDATE users SET password_hash = ?, password_changed_at = ? WHERE username = ?",
                             (generate_password_hash(password), self.clock(), name))
            if cur.rowcount == 0:
                raise ValueError(f"no user {name!r}")
            db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (name,))

    def delete_user(self, username: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM users WHERE username = ?", (normalize_username(username),)).rowcount > 0

    def usernames(self) -> list[str]:
        with self._connect() as db:
            return [row["username"] for row in db.execute("SELECT username FROM users ORDER BY username")]

    # Sign-in

    def _failures(self, db: sqlite3.Connection, column: str, value: str, since: float) -> int:
        return int(db.execute(_FAILURE_SQL[column], (value, since, *FAILURE_OUTCOMES)).fetchone()[0])

    def _log(self, db: sqlite3.Connection, username: str, ip: str, user_agent: str, outcome: str) -> None:
        db.execute("INSERT INTO login_events (at, username, ip, user_agent, outcome) VALUES (?, ?, ?, ?, ?)",
                   (self.clock(), username[:64], ip[:64], user_agent[:200], outcome))

    def authenticate(self, username: str, password: str, ip: str, user_agent: str = "") -> Session:
        """Check a sign-in and open a session for it.

        Raises:
            AuthError: wrong username or password (same message for both), or locked out.
        """
        name = normalize_username(username)[:64]
        now = self.clock()
        since = now - LOCKOUT_WINDOW_S
        # Failures are logged and committed before raising: raising inside the transaction would roll the
        # log entry back, and the lockout counts those entries.
        refusal = ""
        with self._connect() as db:
            row = db.execute("SELECT id, password_hash FROM users WHERE username = ?", (name,)).fetchone()
            candidate = password if len(password) <= MAX_PASSWORD else ""
            if self._failures(db, "ip", ip, since) >= MAX_FAILURES_PER_IP:
                outcome, refusal = "ip_blocked", "Too many failed sign-ins from this address. Try again later."
            elif self._failures(db, "username", name, since) >= MAX_FAILURES_PER_USER:
                outcome, refusal = "locked", "Too many failed sign-ins for this account. Try again later."
            elif row is None:
                check_password_hash(_DUMMY_HASH, candidate)
                outcome, refusal = "unknown_user", "Wrong username or password."
            elif not candidate or not check_password_hash(row["password_hash"], candidate):
                outcome, refusal = "bad_password", "Wrong username or password."
            else:
                outcome = "success"
            self._log(db, name, ip, user_agent, outcome)
        if refusal:
            raise AuthError(refusal)
        with self._connect() as db:
            db.execute("DELETE FROM login_events WHERE at < ?", (now - EVENT_RETENTION_S,))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, last_seen, ip, user_agent) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (_hash_token(token), row["id"], csrf, now, now, ip[:64], user_agent[:200]))
        return Session(token, int(row["id"]), name, csrf, now, now, ip[:64], user_agent[:200], _hash_token(token))

    # Sessions

    def session(self, token: str) -> Optional[Session]:
        """The live session for a cookie token, refreshing its idle timer; None if missing or expired."""
        if not token or len(token) > 128:
            return None
        token_hash = _hash_token(token)
        now = self.clock()
        with self._connect() as db:
            row = db.execute("SELECT s.*, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                             "WHERE s.token_hash = ?", (token_hash,)).fetchone()
            if row is None:
                return None
            if now - row["last_seen"] > IDLE_TIMEOUT_S or now - row["created_at"] > ABSOLUTE_TIMEOUT_S:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
        return Session("", int(row["user_id"]), row["username"], row["csrf_token"], row["created_at"], now,
                       row["ip"], row["user_agent"], token_hash)

    def end_session(self, token: str, ip: str = "", user_agent: str = "") -> None:
        with self._connect() as db:
            row = db.execute("SELECT u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                             "WHERE s.token_hash = ?", (_hash_token(token),)).fetchone()
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
            if row is not None:
                self._log(db, row["username"], ip, user_agent, "logout")

    def end_other_sessions(self, current: Session) -> int:
        with self._connect() as db:
            return db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                              (current.user_id, current.token_hash)).rowcount

    def sessions_for(self, user_id: int) -> list[Session]:
        cutoff = self.clock() - IDLE_TIMEOUT_S
        with self._connect() as db:
            rows = db.execute("SELECT s.*, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                              "WHERE s.user_id = ? AND s.last_seen >= ? ORDER BY s.last_seen DESC",
                              (user_id, cutoff)).fetchall()
        return [Session("", int(r["user_id"]), r["username"], "", r["created_at"], r["last_seen"], r["ip"],
                        r["user_agent"], r["token_hash"]) for r in rows]

    def recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT * FROM login_events ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()


def tokens_match(a: str, b: str) -> bool:
    """Constant-time comparison for CSRF tokens."""
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())
