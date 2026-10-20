import os
import re
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")

from click.testing import CliRunner  # noqa: E402

from ark_angel.admin import db as admin_db  # noqa: E402
from ark_angel.admin.app import LOGIN_CSRF_COOKIE, SESSION_COOKIE, create_app, safe_next  # noqa: E402
from ark_angel.admin.db import AdminDB, AuthError  # noqa: E402
from ark_angel.cases.store import FileCaseStore  # noqa: E402
from ark_angel.cli import cli  # noqa: E402
from ark_angel.models import Case, Evidence  # noqa: E402
from ark_angel.pipeline import enrich  # noqa: E402

PASSWORD = "correct horse battery"
UA = {"User-Agent": "pytest-browser"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def env(tmp_path: Path):
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("C1", "Missing <b>Riley</b>", evidence=[
        Evidence("tips:1", "text", "2026-10-01 call 303-555-0147", {"source": "tips"}),
        Evidence("notes:1", "text", "<script>alert(1)</script> 303.555.0147", {"source": "notes"}),
    ]))
    enrich(store, "C1")
    clock = Clock()
    db = AdminDB(tmp_path / "admin.db", clock=clock)
    db.create_user("Graves", PASSWORD)
    app = create_app(store, db)
    app.config["TESTING"] = True
    return app.test_client(), db, clock, store


def _login_csrf(client) -> str:
    page = client.get("/login").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def _login(client, username="graves", password=PASSWORD, next_url=None):
    data = {"csrf_token": _login_csrf(client), "username": username, "password": password}
    if next_url:
        data["next"] = next_url
    return client.post("/login", data=data, headers=UA)


def _csrf(client) -> str:
    page = client.get("/security").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


# Accounts and the database


def test_accounts_validate_and_hash(tmp_path: Path) -> None:
    db = AdminDB(tmp_path / "a.db")
    with pytest.raises(ValueError, match="at least 12"):
        db.create_user("someone", "short")
    with pytest.raises(ValueError, match="username"):
        db.create_user("x", PASSWORD)
    db.create_user("Someone", PASSWORD)
    with pytest.raises(ValueError, match="already exists"):
        db.create_user("someone", PASSWORD)
    stored = sqlite3.connect(tmp_path / "a.db").execute("SELECT username, password_hash FROM users").fetchone()
    assert stored[0] == "someone" and stored[1].startswith("scrypt:") and PASSWORD not in stored[1]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_database_file_is_owner_only(tmp_path: Path) -> None:
    AdminDB(tmp_path / "a.db")
    assert stat.S_IMODE(os.stat(tmp_path / "a.db").st_mode) == 0o600


def test_session_tokens_are_stored_hashed(env) -> None:
    client, db, _, _ = env
    _login(client)
    token = client.get_cookie(SESSION_COOKIE).value
    rows = sqlite3.connect(db.path).execute("SELECT token_hash FROM sessions").fetchall()
    assert rows and all(token not in row[0] for row in rows)


# Sign-in


def test_pages_need_a_session(env) -> None:
    client, *_ = env
    for path in ("/", "/cases/C1", "/cases/C1/report", "/cases/C1/map.kml", "/security"):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=")
    assert client.post("/logout").status_code == 403
    assert client.post("/security/sign-out-others").status_code == 403


def test_login_sets_strict_cookie_and_shows_cases(env) -> None:
    client, *_ = env
    resp = _login(client, username="  GRAVES ")
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    cookie = next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith(SESSION_COOKIE))
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    page = client.get("/").get_data(as_text=True)
    assert "Missing &lt;b&gt;Riley&lt;/b&gt;" in page and "<b>Riley</b>" not in page  # escaped


def test_login_needs_its_csrf_token(env) -> None:
    client, *_ = env
    resp = client.get("/login")
    cookie = next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith(LOGIN_CSRF_COOKIE))
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert client.post("/login", data={"username": "graves", "password": PASSWORD}).status_code == 400
    assert client.post("/login", data={"csrf_token": "forged", "username": "graves",
                                       "password": PASSWORD}).status_code == 400


def test_wrong_password_and_unknown_user_look_the_same(env) -> None:
    client, db, _, _ = env
    wrong = _login(client, password="not the password")
    nobody = _login(client, username="nobody", password=PASSWORD)
    assert wrong.status_code == nobody.status_code == 401
    message = "Wrong username or password."
    assert message in wrong.get_data(as_text=True) and message in nobody.get_data(as_text=True)
    assert [e["outcome"] for e in db.recent_events()][:2] == ["unknown_user", "bad_password"]


def test_lockout_after_repeated_failures(env) -> None:
    client, db, clock, _ = env
    for _ in range(admin_db.MAX_FAILURES_PER_USER):
        assert _login(client, password="nope nope nope").status_code == 401
    locked = _login(client)  # right password, still locked
    assert locked.status_code == 401 and "Too many failed sign-ins" in locked.get_data(as_text=True)
    assert db.recent_events()[0]["outcome"] == "locked"
    # Unknown usernames lock the same way, so the lockout doesn't reveal which accounts exist.
    for _ in range(admin_db.MAX_FAILURES_PER_USER):
        _login(client, username="ghost", password="nope nope nope")
    assert "Too many failed sign-ins" in _login(client, username="ghost").get_data(as_text=True)

    clock.now += admin_db.LOCKOUT_WINDOW_S + 1
    assert _login(client).status_code == 302


def test_ip_lockout(env) -> None:
    client, db, _, _ = env
    for n in range(admin_db.MAX_FAILURES_PER_IP):
        _login(client, username=f"user{n}", password="nope nope nope")
    assert "from this address" in _login(client).get_data(as_text=True)


def test_overlong_password_is_rejected_without_hashing(env) -> None:
    client, *_ = env
    assert _login(client, password="x" * 5000).status_code == 401


def test_next_cannot_redirect_off_site(env) -> None:
    for bad in ("//evil.example", "https://evil.example/", "/\\evil.example", "javascript:alert(1)", "", None):
        assert safe_next(bad) == "/"
    assert safe_next("/cases/C1?x=1") == "/cases/C1?x=1"
    client, *_ = env
    assert _login(client, next_url="//evil.example/").headers["Location"] == "/"


# Sessions


def test_logout_needs_csrf_and_kills_the_session(env) -> None:
    client, *_ = env
    _login(client)
    token = client.get_cookie(SESSION_COOKIE).value
    assert client.post("/logout").status_code == 400  # no CSRF token
    assert client.get("/logout").status_code == 405
    resp = client.post("/logout", data={"csrf_token": _csrf(client)})
    assert resp.status_code == 302 and resp.headers["Location"] == "/login"
    client.set_cookie(SESSION_COOKIE, token)  # replay the old cookie
    assert client.get("/").status_code == 302


def test_idle_and_absolute_timeouts(env) -> None:
    client, _, clock, _ = env
    _login(client)
    clock.now += admin_db.IDLE_TIMEOUT_S - 5
    assert client.get("/").status_code == 200  # activity refreshes the idle timer
    clock.now += admin_db.IDLE_TIMEOUT_S + 1
    assert client.get("/").status_code == 302

    _login(client)
    for _ in range(admin_db.ABSOLUTE_TIMEOUT_S // (admin_db.IDLE_TIMEOUT_S - 60) + 1):
        clock.now += admin_db.IDLE_TIMEOUT_S - 60
        client.get("/")
    assert client.get("/").status_code == 302


def test_password_change_and_sign_out_others(env) -> None:
    client, db, _, _ = env
    other = client.application.test_client()
    _login(client)
    _login(other)
    page = client.get("/security").get_data(as_text=True)
    assert "Sign out all other sessions" in page and "pytest-browser" in page
    client.post("/security/sign-out-others", data={"csrf_token": _csrf(client)})
    assert other.get("/").status_code == 302 and client.get("/").status_code == 200

    db.set_password("graves", "an entirely new passphrase")
    assert client.get("/").status_code == 302


def test_session_cookie_garbage_is_ignored(env) -> None:
    client, *_ = env
    for junk in ("", "x" * 4000, "' OR 1=1 --"):
        client.set_cookie(SESSION_COOKIE, junk)
        assert client.get("/").status_code == 302


# Requests


def test_wrong_host_and_cross_origin_posts_are_refused(env) -> None:
    client, *_ = env
    assert client.get("/login", headers={"Host": "evil.example"}).status_code == 400  # DNS rebinding
    assert client.get("/login", headers={"Host": "localhost:8765"}).status_code == 200
    assert client.get("/login", headers={"Host": "[::1]:8765"}).status_code == 200
    _login(client)
    csrf = _csrf(client)
    resp = client.post("/logout", data={"csrf_token": csrf}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 400
    assert client.open("/", method="PUT").status_code == 405


def test_security_headers(env) -> None:
    client, *_ = env
    resp = client.get("/login")
    h = resp.headers
    assert "script-src" not in h["Content-Security-Policy"] and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer" and h["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in h
    assert "inline" not in client.get("/static/admin.css").headers["Content-Security-Policy"]


def test_secure_cookies_and_hsts_behind_proxy(tmp_path: Path) -> None:
    db = AdminDB(tmp_path / "a.db")
    db.create_user("graves", PASSWORD)
    client = create_app(FileCaseStore(tmp_path / "cases"), db, secure_cookies=True).test_client()
    resp = client.get("/login", base_url="https://localhost")
    assert "Secure" in resp.headers["Set-Cookie"] and "max-age" in resp.headers["Strict-Transport-Security"]


def test_case_pages_report_and_maps(env) -> None:
    client, *_ = env
    _login(client)
    page = client.get("/cases/C1").get_data(as_text=True)
    assert "Phone 3035550147" in page and "<script>alert(1)</script>" not in page

    report = client.get("/cases/C1/report")
    assert report.status_code == 200 and "<script>alert(1)</script>" not in report.get_data(as_text=True)
    csp = report.headers["Content-Security-Policy"]
    assert "'sha256-" in csp and "'unsafe-inline'" not in csp.split("script-src")[1]

    kml = client.get("/cases/C1/map.kml")
    assert kml.status_code == 200 and kml.headers["Content-Disposition"] == 'attachment; filename="C1.kml"'
    assert client.get("/cases/C1/map.exe").status_code == 404


def test_case_ids_cannot_escape_the_store(env, tmp_path: Path) -> None:
    client, *_ = env
    (tmp_path / "secret.json").write_text("{}")
    _login(client)
    for path in ("/cases/..%2fsecret", "/cases/../secret", "/cases/%2e%2e", "/cases/nope", "/cases/C1%00"):
        assert client.get(path).status_code == 404, path


def test_broken_case_file_does_not_take_the_dashboard_down(env) -> None:
    client, _, _, store = env
    (store.data_dir / "broken.json").write_text("{not json")
    _login(client)
    page = client.get("/")
    assert page.status_code == 200 and "C1" in page.get_data(as_text=True)


# CLI


def test_cli_accounts_and_serve_guards(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases"), "admin", "--db", str(tmp_path / "admin.db")]

    result = runner.invoke(cli, [*base, "serve"])
    assert result.exit_code != 0 and "create an account first" in result.output

    result = runner.invoke(cli, [*base, "create-user", "graves"], input="short\nshort\n")
    assert result.exit_code != 0 and "at least 12" in result.output
    result = runner.invoke(cli, [*base, "create-user", "graves"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert runner.invoke(cli, [*base, "users"]).output.strip() == "graves"

    calls = []
    monkeypatch.setattr("waitress.serve", lambda app, **kw: calls.append(kw))
    result = runner.invoke(cli, [*base, "serve", "--host", "0.0.0.0"])
    assert result.exit_code != 0 and "plain HTTP" in result.output
    result = runner.invoke(cli, [*base, "serve", "--host", "0.0.0.0", "--behind-proxy"])
    assert result.exit_code != 0 and "--allow-host" in result.output
    assert not calls

    assert runner.invoke(cli, [*base, "serve"]).exit_code == 0
    assert calls[-1]["host"] == "127.0.0.1" and "trusted_proxy" not in calls[-1]
    result = runner.invoke(cli, [*base, "serve", "--host", "0.0.0.0", "--behind-proxy",
                                 "--allow-host", "arkangel.example.org"])
    assert result.exit_code == 0, result.output
    assert calls[-1]["trusted_proxy"] == "127.0.0.1"

    result = runner.invoke(cli, [*base, "passwd", "graves"], input="a brand new passphrase\na brand new passphrase\n")
    assert result.exit_code == 0 and "signed out" in result.output
    with pytest.raises(AuthError):
        AdminDB(tmp_path / "admin.db").authenticate("graves", PASSWORD, "127.0.0.1")
    assert runner.invoke(cli, [*base, "delete-user", "graves", "--yes"]).exit_code == 0
