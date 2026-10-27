"""SQLite store for the website: accounts, sessions, case ownership, public profiles and rate limits.

Case evidence itself stays in the case store (``FileCaseStore``). This file
holds everything about *people*: who has an account and whether it is
approved, which cases each person owns or was shared, what a case shows the
public, and the sign-in log.

Sessions live here, not in a signed cookie, so signing out or changing a
password really ends them. The cookie only carries a random token, and the
database only keeps that token's SHA-256. Passwords are hashed with scrypt.

A public profile has two copies: the owner's draft and the version an admin
approved. Visitors only ever see the approved copy, so editing a live profile
can't slip anything past review.

Public forms are rate limited by a keyed hash of the visitor's address, never
the address itself, and those entries expire after a day: tipsters aren't
tracked. Every query is parameterized. The file is created owner-only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

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
PUBLIC_EVENT_RETENTION_S = 24 * 60 * 60

ROLES = ("admin", "member")
STATUSES = ("pending", "active", "disabled")
CASE_ROLES = ("owner", "editor", "viewer")
# What each case role may do, cumulatively.
CASE_RANK = {"viewer": 0, "editor": 1, "owner": 2}
PROFILE_STATES = ("draft", "requested", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'disabled')),
    signup_note TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    notify_tips INTEGER NOT NULL DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    draft TEXT NOT NULL DEFAULT '{}',
    draft_photo TEXT NOT NULL DEFAULT '',
    draft_state TEXT NOT NULL DEFAULT 'draft' CHECK (draft_state IN ('draft', 'requested', 'rejected')),
    draft_version INTEGER NOT NULL DEFAULT 0,
    review_note TEXT NOT NULL DEFAULT '',
    published TEXT,
    published_photo TEXT NOT NULL DEFAULT '',
    published_at REAL,
    slug TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS case_members (
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    PRIMARY KEY (case_id, user_id)
);
CREATE TABLE IF NOT EXISTS public_events (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    ip_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS public_events_key ON public_events (kind, ip_hash, at);
CREATE TABLE IF NOT EXISTS notifications (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    last_sent REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""

# Checked against when the username doesn't exist, so a miss takes as long as a wrong password.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))
FAILURE_OUTCOMES = ("bad_password", "unknown_user")
# Fixed queries, one per lockout key: nothing is ever formatted into SQL.
_FAILURE_SQL = {
    "ip": "SELECT COUNT(*) FROM login_events WHERE ip = ? AND at > ? AND outcome IN (?, ?)",
    "username": "SELECT COUNT(*) FROM login_events WHERE username = ? AND at > ? AND outcome IN (?, ?)",
}
_SESSION_SQL = ("SELECT s.*, u.username, u.role, u.status FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?")


class DraftChanged(Exception):
    """An approval for a draft version that is no longer the current request."""


class AuthError(Exception):
    """A sign-in that didn't succeed. The message is safe to show: it never says which part was wrong."""


@dataclass
class Session:
    token: str  # only known right after creation; empty when loaded back
    user_id: int
    username: str
    role: str
    csrf_token: str
    created_at: float
    last_seen: float
    ip: str
    user_agent: str
    token_hash: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    name = normalize_username(username)
    if not USERNAME_PATTERN.match(name):
        raise ValueError("username must be 3-32 characters: letters, digits, '.', '_' or '-'")
    return name


# Deliberately plain: one @, no spaces or control characters, a dot in the domain. It only has to stop junk
# and header tricks; the address is proven by mail actually arriving.
_EMAIL_PATTERN = re.compile(r"^[^@\s\x00-\x1f\x7f<>,;\"]{1,64}@[^@\s\x00-\x1f\x7f<>,;\"]{1,189}\.[^@\s\x00-\x1f\x7f<>,;\".]{2,63}$")


def validate_email(email: str) -> str:
    """A cleaned email address, or ``""`` for none. Raises ValueError for something that isn't one."""
    email = email.strip()
    if not email:
        return ""
    if len(email) > 254 or not _EMAIL_PATTERN.match(email):
        raise ValueError("that doesn't look like an email address")
    return email


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"password must be at most {MAX_PASSWORD} characters")


def _profile(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "missing"


class SiteDB:
    """Accounts, sessions, case access, public profiles and rate limits in one SQLite file."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            # Create it owner-only before SQLite opens it (no-op permissions on Windows).
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(cases)")}
            if "draft_version" not in columns:  # databases made before approvals were version-bound
                db.execute("ALTER TABLE cases ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 0")
            user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            if "email" not in user_columns:  # databases made before email notifications
                db.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE users ADD COLUMN notify_tips INTEGER NOT NULL DEFAULT 1")
            db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('secret', ?)", (secrets.token_hex(32),))
            self._secret = db.execute("SELECT value FROM settings WHERE key = 'secret'").fetchone()[0].encode()

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

    def create_user(self, username: str, password: str, role: str = "member", status: str = "pending",
                    note: str = "", email: str = "") -> int:
        name = validate_username(username)
        validate_password(password)
        email = validate_email(email)
        if role not in ROLES or status not in STATUSES:
            raise ValueError("bad role or status")
        now = self.clock()
        try:
            with self._connect() as db:
                cur = db.execute(
                    "INSERT INTO users (username, password_hash, role, status, signup_note, email, created_at, "
                    "password_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, generate_password_hash(password), role, status, note[:500], email, now, now))
        except sqlite3.IntegrityError:
            raise ValueError(f"user {name!r} already exists") from None
        return int(cur.lastrowid)

    def user(self, username: str) -> Optional[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT id, username, role, status, signup_note, email, notify_tips, created_at FROM users "
                              "WHERE username = ?", (normalize_username(username),)).fetchone()

    def users(self, status: Optional[str] = None) -> list[sqlite3.Row]:
        with self._connect() as db:
            if status:
                return db.execute("SELECT id, username, role, status, signup_note, email, notify_tips, created_at FROM users "
                                  "WHERE status = ? ORDER BY created_at", (status,)).fetchall()
            return db.execute("SELECT id, username, role, status, signup_note, email, notify_tips, created_at FROM users "
                              "ORDER BY status = 'pending' DESC, username").fetchall()

    def usernames(self) -> list[str]:
        return [row["username"] for row in self.users()]

    def has_admin(self) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM users WHERE role = 'admin' AND status = 'active'").fetchone() is not None

    def user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT id, username, role, status, signup_note, email, notify_tips, created_at "
                              "FROM users WHERE id = ?", (user_id,)).fetchone()

    def set_email(self, user_id: int, email: str, notify_tips: bool = True) -> None:
        with self._connect() as db:
            db.execute("UPDATE users SET email = ?, notify_tips = ? WHERE id = ?",
                       (validate_email(email), int(bool(notify_tips)), user_id))

    def admin_recipients(self) -> list[sqlite3.Row]:
        """Active admins who gave an email address."""
        with self._connect() as db:
            return db.execute("SELECT id, username, email FROM users WHERE role = 'admin' AND status = 'active' "
                              "AND email != ''").fetchall()

    def tip_recipients(self, case_id: str) -> list[sqlite3.Row]:
        """Active members of a case who gave an email address and want tip emails."""
        with self._connect() as db:
            return db.execute("SELECT u.id, u.username, u.email FROM case_members m JOIN users u ON u.id = m.user_id "
                              "WHERE m.case_id = ? AND u.status = 'active' AND u.email != '' AND u.notify_tips = 1",
                              (case_id,)).fetchall()

    def case_owner(self, case_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT u.id, u.username, u.email, u.status FROM case_members m "
                              "JOIN users u ON u.id = m.user_id WHERE m.case_id = ? AND m.role = 'owner'",
                              (case_id,)).fetchone()

    def claim_notification(self, user_id: int, key: str, window_s: float) -> bool:
        """True (and recorded) if ``user_id`` hasn't been sent ``key`` within the window: the email throttle."""
        now = self.clock()
        with self._connect() as db:
            row = db.execute("SELECT last_sent FROM notifications WHERE user_id = ? AND key = ?",
                             (user_id, key)).fetchone()
            if row is not None and now - row["last_sent"] < window_s:
                return False
            db.execute("INSERT INTO notifications (user_id, key, last_sent) VALUES (?, ?, ?) "
                       "ON CONFLICT (user_id, key) DO UPDATE SET last_sent = excluded.last_sent", (user_id, key, now))
        return True

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

    def check_password(self, user_id: int, password: str) -> bool:
        """For re-confirming a signed-in user (e.g. before a password change)."""
        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not password or len(password) > MAX_PASSWORD:
            return False
        return check_password_hash(row["password_hash"], password)

    def set_status(self, username: str, status: str) -> None:
        """Approve (``active``), or disable an account; disabling signs it out everywhere."""
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}")
        name = normalize_username(username)
        with self._connect() as db:
            if db.execute("UPDATE users SET status = ? WHERE username = ?", (status, name)).rowcount == 0:
                raise ValueError(f"no user {name!r}")
            if status != "active":
                db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (name,))

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"bad role {role!r}")
        name = normalize_username(username)
        with self._connect() as db:
            if db.execute("UPDATE users SET role = ? WHERE username = ?", (role, name)).rowcount == 0:
                raise ValueError(f"no user {name!r}")

    def delete_user(self, username: str) -> bool:
        """Delete an account. Refused while it still owns cases, so no case is left without an owner."""
        name = normalize_username(username)
        with self._connect() as db:
            owns = db.execute("SELECT COUNT(*) FROM case_members m JOIN users u ON u.id = m.user_id "
                              "WHERE u.username = ? AND m.role = 'owner'", (name,)).fetchone()[0]
            if owns:
                raise ValueError(f"{name!r} still owns {owns} case(s); transfer or delete them first")
            return db.execute("DELETE FROM users WHERE username = ?", (name,)).rowcount > 0

    # Sign-in

    def _failures(self, db: sqlite3.Connection, column: str, value: str, since: float) -> int:
        return int(db.execute(_FAILURE_SQL[column], (value, since, *FAILURE_OUTCOMES)).fetchone()[0])

    def _log(self, db: sqlite3.Connection, username: str, ip: str, user_agent: str, outcome: str) -> None:
        db.execute("INSERT INTO login_events (at, username, ip, user_agent, outcome) VALUES (?, ?, ?, ?, ?)",
                   (self.clock(), username[:64], ip[:64], user_agent[:200], outcome))

    def authenticate(self, username: str, password: str, ip: str, user_agent: str = "") -> Session:
        """Check a sign-in and open a session for it.

        Raises:
            AuthError: wrong username or password (same message for both), locked out, or an
                account that isn't approved (said only after the right password).
        """
        name = normalize_username(username)[:64]
        now = self.clock()
        since = now - LOCKOUT_WINDOW_S
        # Failures are logged and committed before raising: raising inside the transaction would roll the
        # log entry back, and the lockout counts those entries.
        refusal = ""
        with self._connect() as db:
            row = db.execute("SELECT id, password_hash, role, status FROM users WHERE username = ?",
                             (name,)).fetchone()
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
            elif row["status"] == "pending":
                outcome, refusal = "pending", "Your account is waiting for an administrator to approve it."
            elif row["status"] != "active":
                outcome, refusal = "disabled", "This account has been disabled."
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
        return Session(token, int(row["id"]), name, row["role"], csrf, now, now, ip[:64], user_agent[:200],
                       _hash_token(token))

    # Sessions

    def session(self, token: str) -> Optional[Session]:
        """The live session for a cookie token, refreshing its idle timer; None if missing, expired or disabled."""
        if not token or len(token) > 128:
            return None
        token_hash = _hash_token(token)
        now = self.clock()
        with self._connect() as db:
            row = db.execute(_SESSION_SQL, (token_hash,)).fetchone()
            if row is None:
                return None
            if (now - row["last_seen"] > IDLE_TIMEOUT_S or now - row["created_at"] > ABSOLUTE_TIMEOUT_S
                    or row["status"] != "active"):
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
        return Session("", int(row["user_id"]), row["username"], row["role"], row["csrf_token"], row["created_at"],
                       now, row["ip"], row["user_agent"], token_hash)

    def end_session(self, token: str, ip: str = "", user_agent: str = "") -> None:
        with self._connect() as db:
            row = db.execute(_SESSION_SQL, (_hash_token(token),)).fetchone()
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
            rows = db.execute("SELECT s.*, u.username, u.role FROM sessions s JOIN users u ON u.id = s.user_id "
                              "WHERE s.user_id = ? AND s.last_seen >= ? ORDER BY s.last_seen DESC",
                              (user_id, cutoff)).fetchall()
        return [Session("", int(r["user_id"]), r["username"], r["role"], "", r["created_at"], r["last_seen"],
                        r["ip"], r["user_agent"], r["token_hash"]) for r in rows]

    def recent_events(self, limit: int = 50, username: Optional[str] = None) -> list[sqlite3.Row]:
        with self._connect() as db:
            if username is not None:
                return db.execute("SELECT * FROM login_events WHERE username = ? ORDER BY at DESC, id DESC LIMIT ?",
                                  (normalize_username(username), limit)).fetchall()
            return db.execute("SELECT * FROM login_events ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()

    # Public rate limits

    def ip_key(self, ip: str) -> str:
        """A keyed hash of an address: enough to rate limit, useless for finding the person."""
        return hmac.new(self._secret, ip.encode(), hashlib.sha256).hexdigest()

    def allow(self, kind: str, ip: str, limit: int, window_s: float) -> bool:
        """Record one public action (a tip, a sign-up) and say whether it is within ``limit`` per window."""
        now = self.clock()
        key = self.ip_key(ip)
        with self._connect() as db:
            db.execute("DELETE FROM public_events WHERE at < ?", (now - PUBLIC_EVENT_RETENTION_S,))
            count = db.execute("SELECT COUNT(*) FROM public_events WHERE kind = ? AND ip_hash = ? AND at > ?",
                               (kind, key, now - window_s)).fetchone()[0]
            if count >= limit:
                return False
            db.execute("INSERT INTO public_events (at, kind, ip_hash) VALUES (?, ?, ?)", (now, kind, key))
        return True

    # Cases and access

    def register_case(self, case_id: str, owner: str) -> None:
        """Record a case and its owner (the case data itself lives in the case store)."""
        user = self.user(owner)
        if user is None:
            raise ValueError(f"no user {normalize_username(owner)!r}")
        try:
            with self._connect() as db:
                db.execute("INSERT INTO cases (case_id, created_at) VALUES (?, ?)", (case_id, self.clock()))
                db.execute("INSERT INTO case_members (case_id, user_id, role) VALUES (?, ?, 'owner')",
                           (case_id, user["id"]))
        except sqlite3.IntegrityError:
            raise ValueError(f"case {case_id!r} is already on the site") from None

    def forget_case(self, case_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))

    def case_role(self, case_id: str, user_id: int) -> Optional[str]:
        with self._connect() as db:
            row = db.execute("SELECT role FROM case_members WHERE case_id = ? AND user_id = ?",
                             (case_id, user_id)).fetchone()
        return row["role"] if row else None

    def cases_for(self, user_id: int) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT c.*, m.role FROM cases c JOIN case_members m ON m.case_id = c.case_id "
                              "WHERE m.user_id = ? ORDER BY c.created_at DESC", (user_id,)).fetchall()

    def all_cases(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT c.*, u.username AS owner FROM cases c "
                              "LEFT JOIN case_members m ON m.case_id = c.case_id AND m.role = 'owner' "
                              "LEFT JOIN users u ON u.id = m.user_id ORDER BY c.created_at DESC").fetchall()

    def members(self, case_id: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT u.username, m.role FROM case_members m JOIN users u ON u.id = m.user_id "
                              "WHERE m.case_id = ? ORDER BY m.role = 'owner' DESC, u.username", (case_id,)).fetchall()

    def set_member(self, case_id: str, username: str, role: str) -> None:
        """Share a case with an approved user as editor or viewer (owners are set at creation)."""
        if role not in ("editor", "viewer"):
            raise ValueError("role must be editor or viewer")
        user = self.user(username)
        if user is None or user["status"] != "active":
            raise ValueError(f"no approved user {normalize_username(username)!r}")
        with self._connect() as db:
            current = db.execute("SELECT role FROM case_members WHERE case_id = ? AND user_id = ?",
                                 (case_id, user["id"])).fetchone()
            if current and current["role"] == "owner":
                raise ValueError("that user owns this case")
            db.execute("INSERT INTO case_members (case_id, user_id, role) VALUES (?, ?, ?) "
                       "ON CONFLICT (case_id, user_id) DO UPDATE SET role = excluded.role",
                       (case_id, user["id"], role))

    def remove_member(self, case_id: str, username: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM case_members WHERE case_id = ? AND role != 'owner' AND "
                       "user_id = (SELECT id FROM users WHERE username = ?)", (case_id, normalize_username(username)))

    # Public profiles

    def case(self, case_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return self._case_dict(row) if row else None

    def _case_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["draft"] = _profile(row["draft"])
        data["published"] = _profile(row["published"]) if row["published"] else None
        return data

    def save_draft(self, case_id: str, profile: dict[str, Any], photo: Optional[str] = None) -> None:
        """Save the owner's draft as a new version. Saving withdraws any pending request.

        Each save bumps ``draft_version``; approval names the version the admin
        reviewed, so a draft edited while an admin is looking at it can't go live
        unseen (see :meth:`approve_publication`).
        """
        with self._connect() as db:
            db.execute("UPDATE cases SET draft = ?, draft_photo = COALESCE(?, draft_photo), draft_state = 'draft', "
                       "draft_version = draft_version + 1 WHERE case_id = ?", (json.dumps(profile), photo, case_id))

    def request_publication(self, case_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE cases SET draft_state = 'requested', review_note = '' WHERE case_id = ?", (case_id,))

    def withdraw_request(self, case_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE cases SET draft_state = 'draft' WHERE case_id = ? AND draft_state = 'requested'",
                       (case_id,))

    def publication_requests(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT c.*, u.username AS owner FROM cases c "
                              "JOIN case_members m ON m.case_id = c.case_id AND m.role = 'owner' "
                              "JOIN users u ON u.id = m.user_id WHERE c.draft_state = 'requested' "
                              "ORDER BY c.created_at").fetchall()
        return [{**self._case_dict(row), "owner": row["owner"]} for row in rows]

    def approve_publication(self, case_id: str, version: int) -> str:
        """Make draft ``version`` the public version. Returns the profile's public slug.

        Raises:
            DraftChanged: the draft is no longer ``version`` (edited since the admin looked) or isn't requested.
            ValueError: the draft lacks a name, police report or attestation.
        """
        with self._connect() as db:
            row = db.execute("SELECT * FROM cases WHERE case_id = ? AND draft_state = 'requested'",
                             (case_id,)).fetchone()
            if row is None or row["draft_version"] != version:
                raise DraftChanged("this profile changed after you opened it, or its request was withdrawn")
            draft = _profile(row["draft"])
            if not (draft.get("name") and draft.get("police_report") and draft.get("attest")):
                raise ValueError("a profile needs a name, a police report and the owner's attestation")
            slug = row["slug"] or f"{_slugify(draft['name'])}-{secrets.token_hex(3)}"
            # The version is checked again in the UPDATE, so an edit landing between the read and the write loses.
            cur = db.execute("UPDATE cases SET published = draft, published_photo = draft_photo, published_at = ?, "
                             "draft_state = 'draft', review_note = '', slug = ? "
                             "WHERE case_id = ? AND draft_state = 'requested' AND draft_version = ?",
                             (self.clock(), slug, case_id, version))
            if cur.rowcount != 1:
                raise DraftChanged("this profile changed after you opened it")
        return slug

    def reject_publication(self, case_id: str, note: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE cases SET draft_state = 'rejected', review_note = ? WHERE case_id = ? "
                       "AND draft_state = 'requested'", (note[:500], case_id))

    def unpublish(self, case_id: str) -> None:
        """Take the public profile down right away (found, or no longer wanted). No approval needed."""
        with self._connect() as db:
            db.execute("UPDATE cases SET published = NULL, published_photo = '', published_at = NULL "
                       "WHERE case_id = ?", (case_id,))

    def published_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cases WHERE published IS NOT NULL "
                              "ORDER BY published_at DESC").fetchall()
        return [self._case_dict(row) for row in rows]

    def profile_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        if len(slug) > 64:
            return None
        with self._connect() as db:
            row = db.execute("SELECT * FROM cases WHERE slug = ? AND published IS NOT NULL", (slug,)).fetchone()
        return self._case_dict(row) if row else None

    def photo_in_use(self, filename: str) -> Optional[str]:
        """``published`` if the photo is on a live profile, ``draft`` if only in a draft, else None."""
        with self._connect() as db:
            if db.execute("SELECT 1 FROM cases WHERE published_photo = ? AND published IS NOT NULL",
                          (filename,)).fetchone():
                return "published"
            if db.execute("SELECT 1 FROM cases WHERE draft_photo = ?", (filename,)).fetchone():
                return "draft"
        return None


def tokens_match(a: str, b: str) -> bool:
    """Constant-time comparison for CSRF tokens."""
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())
