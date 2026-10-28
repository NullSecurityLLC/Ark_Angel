import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from click.testing import CliRunner  # noqa: E402
from web_helpers import Site, new_case, photo, post, publish  # noqa: E402

from ark_angel.cli import cli  # noqa: E402
from ark_angel.web import backup  # noqa: E402
from ark_angel.web.backup import BackupConfig, BackupError  # noqa: E402

RECIPIENT = "age1" + "q" * 58
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="stub tools are POSIX scripts")


def _site_with_data(tmp_path: Path) -> Site:
    site = Site(tmp_path / "site")
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id, image=photo(tmp_path / "p.jpg"))
    post(site.client(), f"/missing/{slug}/tip", {"what": "Saw her by the station"}, form_page=f"/missing/{slug}",
         files={"photo": (photo(tmp_path / "t.jpg"), "t.jpg")})
    return site


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _rows(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 (fixed names)
                for t in ("users", "cases", "case_members", "sessions")}


@pytest.fixture
def tools(tmp_path: Path, monkeypatch):
    """Stand-in `age` and `rclone` that log their arguments; `age` copies input to output."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    script = f"""#!{sys.executable}
import json, os, shutil, sys
args = sys.argv[1:]
with open({str(log)!r}, "a") as f:
    f.write(json.dumps([os.path.basename(sys.argv[0]), args]) + "\\n")
if os.environ.get("STUB_FAIL") == os.path.basename(sys.argv[0]):
    print("simulated failure: no such bucket", file=sys.stderr)
    sys.exit(3)
if os.path.basename(sys.argv[0]) == "age":
    shutil.copyfile(args[-1], args[args.index("--output") + 1])
"""
    for name in ("age", "rclone"):
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# Snapshot and restore


def test_snapshot_and_restore_round_trip(tmp_path: Path) -> None:
    site = _site_with_data(tmp_path)
    base = site.store.data_dir.parent
    before = _tree(base)
    archive = backup.snapshot(site.store.data_dir, site.db.path, tmp_path / "b.tar.gz")
    assert _tree(base) == before  # the snapshot never writes to the site's data
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(archive).st_mode) == 0o600
    with tarfile.open(archive) as tar:
        tops = {name.split("/")[0] for name in tar.getnames()}
    assert tops == {"web.db", "cases", "uploads", "media"}

    fresh = tmp_path / "fresh"
    assert backup.restore(archive, fresh / "cases", fresh / "web.db") is None
    for folder in ("cases", "uploads", "media"):
        assert _tree(fresh / folder) == _tree(base / folder)
    assert _rows(fresh / "web.db") == _rows(site.db.path)


def _hostile(tmp_path: Path, name: str, kind: bytes = tarfile.REGTYPE, link: str = "", with_db: bool = True) -> Path:
    path = tmp_path / f"hostile-{abs(hash(name))}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        if with_db:
            info = tarfile.TarInfo("web.db")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"abc"))
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = link
        info.size = 0 if kind != tarfile.REGTYPE else 4
        tar.addfile(info, io.BytesIO(b"evil") if kind == tarfile.REGTYPE else None)
    return path


def test_restore_refuses_hostile_archives_without_touching_the_site(tmp_path: Path) -> None:
    site = _site_with_data(tmp_path)
    base = site.store.data_dir.parent
    before = _tree(base)
    cases = [
        _hostile(tmp_path, "../escaped.txt"),
        _hostile(tmp_path, "cases/../../escaped.txt"),
        _hostile(tmp_path, "/etc/cron.d/evil"),
        _hostile(tmp_path, "cases/link", tarfile.SYMTYPE, "/etc/passwd"),
        _hostile(tmp_path, "cases/hard", tarfile.LNKTYPE, "web.db"),
        _hostile(tmp_path, "somewhere-else/file.txt"),
        _hostile(tmp_path, "cases/x.json", with_db=False),
    ]
    for archive in cases:
        with pytest.raises(BackupError):
            backup.restore(archive, site.store.data_dir, site.db.path, force=True)
        assert _tree(base) == before, archive  # refused before anything moved
    assert not (tmp_path / "escaped.txt").exists()
    with pytest.raises(BackupError, match="couldn't read"):
        (tmp_path / "junk.tar.gz").write_bytes(b"not a tarball")
        backup.restore(tmp_path / "junk.tar.gz", site.store.data_dir, site.db.path, force=True)
    assert _tree(base) == before


def test_member_check_on_its_own() -> None:
    """The path check must hold even where Python's extraction filter isn't there to back it up."""
    def member(name: str, kind: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type = kind
        return info

    for name in ("cases/../../escaped.txt", "uploads/../../../etc/x", "../web.db", "/etc/passwd", "\\\\host\\share",
                 "C:/evil", "cases\\..\\..\\x", "other/file"):
        with pytest.raises(BackupError):
            backup._check_member(member(name))
    for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
        with pytest.raises(BackupError, match="link or special"):
            backup._check_member(member("cases/x", kind))
    for name in ("web.db", "cases", "cases/c1.json", "uploads/c1/photo-ab.jpg", "media/x.jpg"):
        backup._check_member(member(name))


def test_restore_keeps_existing_data_aside(tmp_path: Path) -> None:
    site = _site_with_data(tmp_path)
    archive = backup.snapshot(site.store.data_dir, site.db.path, tmp_path / "b.tar.gz")
    with pytest.raises(BackupError, match="--force"):
        backup.restore(archive, site.store.data_dir, site.db.path)
    stamp = datetime(2026, 10, 27, 3, 15, tzinfo=timezone.utc)
    moved = backup.restore(archive, site.store.data_dir, site.db.path, force=True, now=lambda: stamp)
    assert moved.name == "pre-restore-20261027-031500" and (moved / "web.db").exists() and (moved / "cases").is_dir()
    assert site.db.path.exists() and site.store.list_cases()


# Configuration and scheduling


def test_backup_config_from_env(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="age public key"):
        BackupConfig.from_env({})
    with pytest.raises(BackupError, match="age public key"):
        BackupConfig.from_env({"ARK_ANGEL_BACKUP_RECIPIENT": "AGE-SECRET-KEY-1ABC"})  # pasted the wrong half
    config = BackupConfig.from_env({"ARK_ANGEL_BACKUP_RECIPIENT": RECIPIENT, "ARK_ANGEL_BACKUP_DEST": "b2:bucket/x/"})
    assert (config.dest, config.at, config.keep_days, config.local_keep) == ("b2:bucket/x", "03:15", 30, 7)
    for bad in ({"ARK_ANGEL_BACKUP_AT": "3am"}, {"ARK_ANGEL_BACKUP_AT": "24:00"},
                {"ARK_ANGEL_BACKUP_PING_URL": "http://hc.example/1"}, {"ARK_ANGEL_BACKUP_KEEP_DAYS": "0"},
                {"ARK_ANGEL_BACKUP_LOCAL_KEEP": "many"}):
        with pytest.raises(BackupError):
            BackupConfig.from_env({"ARK_ANGEL_BACKUP_RECIPIENT": RECIPIENT, **bad})


def test_next_run() -> None:
    now = datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)
    assert backup.next_run(now, "03:15") == datetime(2026, 10, 27, 3, 15, tzinfo=timezone.utc)
    assert backup.next_run(now.replace(hour=3, minute=15), "03:15") == datetime(2026, 10, 28, 3, 15, tzinfo=timezone.utc)
    assert backup.next_run(now.replace(hour=23), "03:15") == datetime(2026, 10, 28, 3, 15, tzinfo=timezone.utc)


# A full nightly run (stand-in tools)


@posix_only
def test_run_once_encrypts_uploads_and_prunes(tmp_path: Path, tools) -> None:
    site = _site_with_data(tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    for day in range(1, 10):  # nine old copies already on the server
        (local / f"arkangel-202601{day:02d}-031500.tar.gz.age").write_bytes(b"old")
    config = BackupConfig(recipient=RECIPIENT, dest="offsite:bucket/arkangel", local_dir=local)
    stamp = datetime(2026, 10, 27, 3, 15, tzinfo=timezone.utc)
    out = backup.run_once(config, site.store.data_dir, site.db.path, now=lambda: stamp)
    assert out.name == "arkangel-20261027-031500.tar.gz.age"
    assert not (local / "arkangel-20261027-031500.tar.gz").exists()  # no plaintext left behind
    assert len(list(local.iterdir())) == 7 and out.exists()
    calls = tools()
    assert calls[0] == ["age", ["--encrypt", "--recipient", RECIPIENT, "--output", str(out),
                                str(local / "arkangel-20261027-031500.tar.gz")]]
    assert calls[1] == ["rclone", ["copyto", str(out), f"offsite:bucket/arkangel/{out.name}"]]
    assert calls[2] == ["rclone", ["delete", "offsite:bucket/arkangel", "--min-age", "30d", "--include",
                                   "arkangel-*.tar.gz.age"]]


@posix_only
def test_failures_are_reported_with_the_tools_own_words(tmp_path: Path, tools, monkeypatch) -> None:
    site = _site_with_data(tmp_path)
    config = BackupConfig(recipient=RECIPIENT, dest="offsite:bucket", local_dir=tmp_path / "local")
    monkeypatch.setenv("STUB_FAIL", "rclone")
    with pytest.raises(BackupError, match="rclone failed: simulated failure: no such bucket"):
        backup.run_once(config, site.store.data_dir, site.db.path)
    monkeypatch.setenv("STUB_FAIL", "age")
    with pytest.raises(BackupError, match="age failed"):
        backup.run_once(config, site.store.data_dir, site.db.path)
    assert not list((tmp_path / "local").glob("*.tar.gz"))  # a failed encryption never leaves plaintext
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(BackupError, match="isn't installed"):
        backup.run_once(config, site.store.data_dir, site.db.path)


# CLI


def _cli_env(site: Site, tmp_path: Path, **extra) -> dict:
    return {"ARK_ANGEL_BACKUP_RECIPIENT": RECIPIENT, "ARK_ANGEL_BACKUP_DEST": "offsite:bucket",
            "ARK_ANGEL_BACKUP_DIR": str(tmp_path / "local"), "ARK_ANGEL_SMTP_HOST": "smtp.example.org",
            "ARK_ANGEL_SMTP_FROM": "alerts@example.org", "ARK_ANGEL_ADMIN_EMAIL": "ops@example.org", **extra}


@posix_only
def test_cli_nightly_once_pings_on_success_and_alerts_on_failure(tmp_path: Path, tools, monkeypatch) -> None:
    site = _site_with_data(tmp_path)
    site.db.set_email(site.db.user("boss")["id"], "boss@example.org")
    sent, pinged = [], []
    monkeypatch.setattr("ark_angel.web.notify.deliver", lambda config, msgs: sent.extend(msgs))
    monkeypatch.setattr("ark_angel.web.backup.ping", pinged.append)
    base = ["--data-dir", str(site.store.data_dir), "web", "backup-nightly", "--once"]
    env = _cli_env(site, tmp_path, ARK_ANGEL_BACKUP_PING_URL="https://hc.example/abc")

    result = CliRunner().invoke(cli, base, env=env)
    assert result.exit_code == 0 and "backup ok" in result.output and not sent
    assert pinged == ["https://hc.example/abc"]

    result = CliRunner().invoke(cli, base, env={**env, "STUB_FAIL": "rclone"})
    assert result.exit_code == 1 and "backup FAILED" in result.output
    assert sorted(m["To"] for m in sent) == ["boss@example.org", "ops@example.org"]
    assert sent[0]["Subject"] == "[ArkAngel] Nightly backup FAILED" and "no such bucket" in sent[0].get_content()
    assert pinged == ["https://hc.example/abc"]  # no ping for a failed run

    def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    sent.clear()
    monkeypatch.setattr("ark_angel.web.backup.run_once", explode)
    result = CliRunner().invoke(cli, base, env=env)  # an unexpected error is still a reported failure
    assert result.exit_code == 1 and "RuntimeError: disk on fire" in result.output and len(sent) == 2


def test_cli_nightly_nags_when_not_configured(tmp_path: Path, monkeypatch) -> None:
    site = Site(tmp_path / "site")
    sent = []
    monkeypatch.setattr("ark_angel.web.notify.deliver", lambda config, msgs: sent.extend(msgs))

    class Stop(Exception):
        pass

    def sleep(seconds):
        raise Stop()

    monkeypatch.setattr("time.sleep", sleep)
    env = _cli_env(site, tmp_path, ARK_ANGEL_BACKUP_RECIPIENT="")
    result = CliRunner().invoke(cli, ["--data-dir", str(site.store.data_dir), "web", "backup-nightly"], env=env)
    assert isinstance(result.exception, Stop)  # it waits (and nags daily) instead of crash-looping
    assert "BACKUPS ARE NOT RUNNING" in result.output
    assert sent and sent[0]["Subject"] == "[ArkAngel] Backups are not set up"
    once = CliRunner().invoke(cli, ["--data-dir", str(site.store.data_dir), "web", "backup-nightly", "--once"], env=env)
    assert once.exit_code != 0 and "age public key" in once.output


def test_cli_restore_wants_a_decrypted_archive(tmp_path: Path) -> None:
    site = _site_with_data(tmp_path)
    (tmp_path / "x.tar.gz.age").write_bytes(b"age-encryption.org/v1")
    result = CliRunner().invoke(cli, ["--data-dir", str(site.store.data_dir), "web", "restore",
                                      str(tmp_path / "x.tar.gz.age")])
    assert result.exit_code != 0 and "age -d -i" in result.output
    archive = backup.snapshot(site.store.data_dir, site.db.path, tmp_path / "b.tar.gz")
    result = CliRunner().invoke(cli, ["--data-dir", str(site.store.data_dir), "web", "restore", str(archive)])
    assert result.exit_code != 0 and "--force" in result.output

    fresh = tmp_path / "fresh" / "cases"  # piped in, as from `age -d ... | ssh server ... restore -`
    result = CliRunner().invoke(cli, ["--data-dir", str(fresh), "web", "restore", "-"], input=archive.read_bytes())
    assert result.exit_code == 0, result.output
    assert _rows(fresh.parent / "web.db") == _rows(site.db.path)
    assert not list(fresh.parent.glob(".restore-*"))  # the spooled copy is gone


# The real tools, when installed


@pytest.mark.skipif(not (shutil.which("age") and shutil.which("age-keygen") and shutil.which("rclone")),
                    reason="needs age and rclone")
def test_real_age_and_rclone_round_trip(tmp_path: Path, monkeypatch) -> None:
    site = _site_with_data(tmp_path)
    key = tmp_path / "key.txt"
    out = subprocess.run(["age-keygen", "-o", str(key)], capture_output=True, text=True, check=True)
    recipient = next(word for word in out.stderr.split() if word.startswith("age1"))
    monkeypatch.setenv("RCLONE_CONFIG_OFFSITE_TYPE", "local")
    monkeypatch.setenv("RCLONE_CONFIG", str(tmp_path / "none.conf"))
    remote = tmp_path / "remote"
    config = BackupConfig(recipient=recipient, dest=f"offsite:{remote}", local_dir=tmp_path / "local")
    encrypted = backup.run_once(config, site.store.data_dir, site.db.path)
    assert (remote / encrypted.name).read_bytes() == encrypted.read_bytes()
    assert encrypted.read_bytes().startswith(b"age-encryption.org/v1")
    plain = tmp_path / "restore.tar.gz"
    subprocess.run(["age", "--decrypt", "--identity", str(key), "--output", str(plain), str(encrypted)], check=True)
    fresh = tmp_path / "fresh"
    backup.restore(plain, fresh / "cases", fresh / "web.db")
    assert _rows(fresh / "web.db") == _rows(site.db.path)
    assert _tree(fresh / "cases") == _tree(site.store.data_dir)
