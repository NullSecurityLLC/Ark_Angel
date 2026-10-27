"""Backups: a consistent snapshot of the site, encrypted, copied off the server, and restorable.

- **Snapshot.** The site database is copied with SQLite's online backup, read-only, so it is consistent
  even while the site is writing. Case files, uploads and profile photos go into the same ``.tar.gz``.
- **Encrypt.** With ``age`` and a *public* key (``age1...``). The private key stays with you, not the
  server, so neither the storage provider nor someone who breaks into the server can read old backups.
  Lose the private key and the backups can't be opened by anyone, including you.
- **Off-site.** With ``rclone`` to any remote it supports (Backblaze B2, S3, R2, SFTP, Drive...),
  configured through its ``RCLONE_CONFIG_<NAME>_*`` environment variables. Old copies are pruned.
- **Loud failure.** A failed run emails the admins; an optional ping URL (healthchecks.io and the like)
  is hit only on success, so it raises the alarm if backups stop happening at all.
- **Restore.** :func:`restore` refuses archives with paths that escape the data folder, links or devices,
  and moves existing data aside instead of overwriting it.

External tools are run with argument lists, never a shell.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404: runs age and rclone with fixed argument lists, no shell
import tarfile
import tempfile
import time
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

log = logging.getLogger("ark_angel.web.backup")

ARCHIVE_PREFIX = "arkangel-"
ARCHIVE_SUFFIX = ".tar.gz"
ENCRYPTED_SUFFIX = ARCHIVE_SUFFIX + ".age"
DB_NAME = "web.db"
CASES_DIR = "cases"
FOLDERS = ("uploads", "media")
_TOOL_TIMEOUT_S = 6 * 60 * 60
_AGE_RECIPIENT = re.compile(r"^age1[0-9a-z]{58}$")
_AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class BackupError(ValueError):
    """A backup or restore step failed; the message says which and why."""


@dataclass(frozen=True)
class BackupConfig:
    recipient: str  # age public key
    dest: str = ""  # rclone remote:path; empty means local copies only
    at: str = "03:15"  # daily, UTC
    keep_days: int = 30  # off-site
    local_keep: int = 7  # encrypted copies kept on the server
    local_dir: Path = Path("/backups")
    ping_url: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "BackupConfig":
        """Settings from ``ARK_ANGEL_BACKUP_*``.

        Raises:
            BackupError: missing or unusable settings.
        """
        recipient = env.get("ARK_ANGEL_BACKUP_RECIPIENT", "").strip()
        if not _AGE_RECIPIENT.match(recipient):
            raise BackupError("set ARK_ANGEL_BACKUP_RECIPIENT to your age public key (age1..., from age-keygen)")
        at = env.get("ARK_ANGEL_BACKUP_AT", "").strip() or "03:15"
        if not _AT.match(at):
            raise BackupError("ARK_ANGEL_BACKUP_AT must be a 24-hour time like 03:15 (UTC)")
        ping = env.get("ARK_ANGEL_BACKUP_PING_URL", "").strip()
        if ping and not ping.startswith("https://"):
            raise BackupError("ARK_ANGEL_BACKUP_PING_URL must be an https:// URL")
        try:
            keep_days = int(env.get("ARK_ANGEL_BACKUP_KEEP_DAYS", "") or 30)
            local_keep = int(env.get("ARK_ANGEL_BACKUP_LOCAL_KEEP", "") or 7)
        except ValueError:
            raise BackupError("ARK_ANGEL_BACKUP_KEEP_DAYS and _LOCAL_KEEP must be whole numbers") from None
        if keep_days < 1 or local_keep < 1:
            raise BackupError("keep at least one day off-site and one copy locally")
        return cls(recipient=recipient, dest=env.get("ARK_ANGEL_BACKUP_DEST", "").strip().rstrip("/"), at=at,
                   keep_days=keep_days, local_keep=local_keep,
                   local_dir=Path(env.get("ARK_ANGEL_BACKUP_DIR", "") or "/backups"), ping_url=ping)


def archive_name(now: datetime) -> str:
    return f"{ARCHIVE_PREFIX}{now:%Y%m%d-%H%M%S}{ARCHIVE_SUFFIX}"


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def snapshot(data_dir: Path, db_path: Path, dest: Path) -> Path:
    """Write a ``.tar.gz`` of the site to ``dest`` (owner-only). Needs only read access to the data."""
    base = data_dir.parent
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        copy_path = Path(tmp) / DB_NAME
        for attempt in range(5):  # the site may be mid-write; the online backup simply retries
            try:
                # closing(): sqlite3's own context manager only commits, and Windows can't delete an open file.
                with closing(_read_only(db_path)) as live, closing(sqlite3.connect(copy_path)) as copy:
                    live.backup(copy)
                break
            except sqlite3.OperationalError:
                if attempt == 4:
                    raise
                time.sleep(2)
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out, tarfile.open(fileobj=out, mode="w:gz") as tar:
            tar.add(copy_path, arcname=DB_NAME)
            if data_dir.exists():
                tar.add(data_dir, arcname=CASES_DIR)
            for name in FOLDERS:
                if (base / name).exists():
                    tar.add(base / name, arcname=name)
    return dest


def _run(tool: str, args: list[str]) -> str:
    exe = shutil.which(tool)
    if exe is None:
        raise BackupError(f"`{tool}` isn't installed (the Docker image includes it)")
    try:
        done = subprocess.run([exe, *args], capture_output=True, text=True,  # nosec B603: no shell, fixed args
                              timeout=_TOOL_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise BackupError(f"{tool} took too long") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
        raise BackupError(f"{tool} failed: {detail[0][:300]}")
    return done.stdout


def encrypt(src: Path, recipient: str) -> Path:
    """``src`` -> ``src.age`` for ``recipient``; the plaintext is removed."""
    out = src.with_name(src.name + ".age")
    try:
        _run("age", ["--encrypt", "--recipient", recipient, "--output", str(out), str(src)])
        os.chmod(out, 0o600)
    finally:
        src.unlink(missing_ok=True)
    return out


def upload(path: Path, dest: str, keep_days: int) -> None:
    """Copy one encrypted backup to ``dest`` (an rclone remote) and delete remote ones older than ``keep_days``."""
    _run("rclone", ["copyto", str(path), f"{dest}/{path.name}"])
    _run("rclone", ["delete", dest, "--min-age", f"{keep_days}d", "--include", f"{ARCHIVE_PREFIX}*{ENCRYPTED_SUFFIX}"])


def prune_local(folder: Path, keep: int) -> list[Path]:
    """Keep only the ``keep`` newest encrypted backups in ``folder``. Returns what was removed."""
    backups = sorted(folder.glob(f"{ARCHIVE_PREFIX}*{ENCRYPTED_SUFFIX}"), key=lambda p: p.name, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
    return backups[keep:]


def run_once(config: BackupConfig, data_dir: Path, db_path: Path,
             now: Optional[Callable[[], datetime]] = None) -> Path:
    """Snapshot, encrypt, upload (if a destination is set) and prune. Returns the encrypted file.

    Raises:
        BackupError: any step failing (partial files are cleaned up).
    """
    stamp = (now or (lambda: datetime.now(timezone.utc)))()
    config.local_dir.mkdir(parents=True, exist_ok=True)
    plain = config.local_dir / archive_name(stamp)
    try:
        snapshot(data_dir, db_path, plain)
    except (OSError, sqlite3.Error, tarfile.TarError) as exc:
        plain.unlink(missing_ok=True)
        raise BackupError(f"snapshot failed: {exc.__class__.__name__}: {exc}") from exc
    encrypted = encrypt(plain, config.recipient)
    if config.dest:
        upload(encrypted, config.dest, config.keep_days)
    prune_local(config.local_dir, config.local_keep)
    return encrypted


def ping(url: str) -> None:
    """Tell an uptime monitor the backup worked. Failures here are logged, not fatal."""
    if not url:
        return
    try:
        with urllib.request.urlopen(url, timeout=15):  # nosec B310: https enforced in BackupConfig
            pass
    except OSError as exc:
        log.warning("backup ping failed: %s", exc.__class__.__name__)


def next_run(now: datetime, at: str) -> datetime:
    """The next ``HH:MM`` UTC after ``now``."""
    hour, minute = (int(part) for part in at.split(":"))
    candidate = now.astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def admin_emails(db_path: Path, extra: tuple[str, ...] = ()) -> list[str]:
    """Active admins' addresses, read without writing (the backup container mounts the data read-only)."""
    addresses = list(extra)
    try:
        with closing(_read_only(db_path)) as db:
            rows = db.execute("SELECT email FROM users WHERE role = 'admin' AND status = 'active' AND email != ''")
            addresses += [row[0] for row in rows if row[0] not in addresses]
    except sqlite3.Error:
        pass  # the alert still goes to ARK_ANGEL_ADMIN_EMAIL
    return addresses


# Restore


def _check_member(member: tarfile.TarInfo) -> None:
    parts = Path(member.name).parts
    if (member.name.startswith(("/", "\\")) or ".." in parts or not parts or ":" in parts[0]
            or "\\" in member.name):
        raise BackupError(f"refusing a path that leaves the data folder: {member.name!r}")
    if parts[0] not in (DB_NAME, CASES_DIR, *FOLDERS):
        raise BackupError(f"unexpected entry in the backup: {member.name!r}")
    if not (member.isfile() or member.isdir()):
        raise BackupError(f"refusing a link or special file in the backup: {member.name!r}")


def restore(archive: Path, data_dir: Path, db_path: Path, force: bool = False,
            now: Optional[Callable[[], datetime]] = None) -> Optional[Path]:
    """Unpack a (decrypted) backup over the site data. Stop the site first.

    The archive is checked and fully unpacked to a temporary folder before anything live is touched,
    so a corrupt or hostile archive leaves the site as it was. Existing data is then moved to
    ``pre-restore-<time>/`` next to it rather than deleted; without ``force`` a non-empty site is
    refused. Returns that folder, or None if there was nothing to move.
    """
    base = data_dir.parent
    targets = {DB_NAME: db_path, CASES_DIR: data_dir, **{name: base / name for name in FOLDERS}}
    existing = [p for p in targets.values() if p.exists()]
    if existing and not force:
        raise BackupError("the site already has data; re-run with --force to move it aside and restore")
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix=".restore-") as tmp:
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                for member in members:
                    _check_member(member)
                if DB_NAME not in {m.name for m in members}:
                    raise BackupError("that archive has no site database in it; is it an ArkAngel backup?")
                extra = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                tar.extractall(tmp, members=members, **extra)  # nosec B202: every member checked above
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise BackupError(f"couldn't read that backup: {exc.__class__.__name__}: {exc}") from exc
        moved_to = None
        if existing:
            stamp = (now or (lambda: datetime.now(timezone.utc)))()
            moved_to = base / f"pre-restore-{stamp:%Y%m%d-%H%M%S}"
            moved_to.mkdir(parents=True)
            for path in existing:
                shutil.move(str(path), str(moved_to / path.name))
        for name, target in targets.items():
            if (Path(tmp) / name).exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(Path(tmp) / name), str(target))
    os.chmod(db_path, 0o600)
    return moved_to
