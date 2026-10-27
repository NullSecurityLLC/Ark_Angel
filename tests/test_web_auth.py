import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")

from web_helpers import PASSWORD, login, post, site, token  # noqa: E402,F401  (site is a fixture)

from ark_angel.cases.store import FileCaseStore  # noqa: E402
from ark_angel.web import db as site_db  # noqa: E402
from ark_angel.web.app import FORM_COOKIE, SESSION_COOKIE, create_app, safe_next  # noqa: E402
from ark_angel.web.db import SiteDB  # noqa: E402


# Accounts and the database


def test_accounts_validate_and_hash(tmp_path: Path) -> None:
    db = SiteDB(tmp_path / "a.db")
    with pytest.raises(ValueError, match="at least 12"):
        db.create_user("someone", "short")
    with pytest.raises(ValueError, match="username"):
        db.create_user("x", PASSWORD)
    db.create_user("Someone", PASSWORD)
    with pytest.raises(ValueError, match="already exists"):
        db.create_user("someone", PASSWORD)
    row = sqlite3.connect(tmp_path / "a.db").execute("SELECT username, password_hash, role, status FROM users").fetchone()
    assert row[0] == "someone" and row[1].startswith("scrypt:") and PASSWORD not in row[1]
    assert row[2:] == ("member", "pending")  # sign-ups start locked


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_database_file_is_owner_only(tmp_path: Path) -> None:
    SiteDB(tmp_path / "a.db")
    assert stat.S_IMODE(os.stat(tmp_path / "a.db").st_mode) == 0o600


def test_session_tokens_are_stored_hashed(site) -> None:
    client = site.client()
    login(client, "boss")
    cookie = client.get_cookie(SESSION_COOKIE).value
    rows = sqlite3.connect(site.db.path).execute("SELECT token_hash FROM sessions").fetchall()
    assert rows and all(cookie not in row[0] for row in rows)


# Sign-in


def test_private_pages_need_a_session(site) -> None:
    client = site.client()
    for path in ("/app/", "/app/account", "/admin/", "/admin/users"):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=")
    assert client.post("/app/logout").status_code == 403
    assert client.post("/app/cases/new").status_code == 403


def test_login_sets_strict_cookie(site) -> None:
    client = site.client()
    resp = login(client, "  BOSS ")
    assert resp.status_code == 302 and resp.headers["Location"] == "/app/"
    cookie = next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith(SESSION_COOKIE))
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert client.get("/app/").status_code == 200


def test_public_forms_need_their_csrf_token(site) -> None:
    client = site.client()
    resp = client.get("/login")
    cookie = next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith(FORM_COOKIE))
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert client.post("/login", data={"username": "boss", "password": PASSWORD}).status_code == 400
    assert client.post("/login", data={"csrf_token": "forged", "username": "boss",
                                       "password": PASSWORD}).status_code == 400
    # A page that has no form doesn't need to hand out a token at all.
    assert not any(h.startswith(FORM_COOKIE) for h in site.client().get("/privacy").headers.getlist("Set-Cookie"))


def test_wrong_password_and_unknown_user_look_the_same(site) -> None:
    client = site.client()
    wrong = login(client, "boss", "not the password")
    nobody = login(client, "nobody")
    assert wrong.status_code == nobody.status_code == 401
    message = "Wrong username or password."
    assert message in wrong.get_data(as_text=True) and message in nobody.get_data(as_text=True)
    assert [e["outcome"] for e in site.db.recent_events()][:2] == ["unknown_user", "bad_password"]


def test_pending_and_disabled_accounts_only_learn_it_with_the_right_password(site) -> None:
    site.db.create_user("waiting", PASSWORD)
    client = site.client()
    assert "Wrong username or password" in login(client, "waiting", "nope nope nope").get_data(as_text=True)
    assert "waiting for an administrator" in login(client, "waiting").get_data(as_text=True)
    site.db.set_status("waiting", "disabled")
    assert "disabled" in login(client, "waiting").get_data(as_text=True)


def test_lockout_after_repeated_failures(site) -> None:
    client = site.client()
    for _ in range(site_db.MAX_FAILURES_PER_USER):
        assert login(client, "boss", "nope nope nope").status_code == 401
    locked = login(client, "boss")  # right password, still locked
    assert locked.status_code == 401 and "Too many failed sign-ins" in locked.get_data(as_text=True)
    assert site.db.recent_events()[0]["outcome"] == "locked"
    for _ in range(site_db.MAX_FAILURES_PER_USER):  # unknown usernames lock the same way
        login(client, "ghost", "nope nope nope")
    assert "Too many failed sign-ins" in login(client, "ghost").get_data(as_text=True)
    site.clock.now += site_db.LOCKOUT_WINDOW_S + 1
    assert login(client, "boss").status_code == 302


def test_ip_lockout(site) -> None:
    client = site.client()
    for n in range(site_db.MAX_FAILURES_PER_IP):
        login(client, f"user{n}", "nope nope nope")
    assert "from this address" in login(client, "boss").get_data(as_text=True)


def test_overlong_password_is_rejected(site) -> None:
    assert login(site.client(), "boss", "x" * 5000).status_code == 401


def test_next_cannot_redirect_off_site(site) -> None:
    for bad in ("//evil.example", "https://evil.example/", "/\\evil.example", "javascript:alert(1)", "", None):
        assert safe_next(bad) == "/app/"
    assert safe_next("/app/cases/c1?x=1") == "/app/cases/c1?x=1"
    assert login(site.client(), "boss", next_url="//evil.example/").headers["Location"] == "/app/"


# Sessions


def test_logout_needs_csrf_and_kills_the_session(site) -> None:
    client = site.client()
    login(client, "boss")
    cookie = client.get_cookie(SESSION_COOKIE).value
    assert client.post("/app/logout").status_code == 400
    assert client.get("/app/logout").status_code == 405
    resp = post(client, "/app/logout", {})
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    client.set_cookie(SESSION_COOKIE, cookie)  # replay the old cookie
    assert client.get("/app/").status_code == 302


def test_idle_and_absolute_timeouts(site) -> None:
    client = site.client()
    login(client, "boss")
    site.clock.now += site_db.IDLE_TIMEOUT_S - 5
    assert client.get("/app/").status_code == 200
    site.clock.now += site_db.IDLE_TIMEOUT_S + 1
    assert client.get("/app/").status_code == 302

    login(client, "boss")
    for _ in range(site_db.ABSOLUTE_TIMEOUT_S // (site_db.IDLE_TIMEOUT_S - 60) + 1):
        site.clock.now += site_db.IDLE_TIMEOUT_S - 60
        client.get("/app/")
    assert client.get("/app/").status_code == 302


def test_password_change_on_the_site(site) -> None:
    client = site.member("sam")
    other = site.client()
    login(other, "sam")
    wrong = post(client, "/app/account/password", {"current": "nope", "password": "a brand new phrase!",
                                                   "password2": "a brand new phrase!"})
    assert wrong.status_code == 400 and "current password is wrong" in wrong.get_data(as_text=True)
    resp = post(client, "/app/account/password", {"current": PASSWORD, "password": "a brand new phrase!",
                                                  "password2": "a brand new phrase!"})
    assert resp.status_code == 302 and "notice=reauth" in resp.headers["Location"]
    assert other.get("/app/").status_code == 302  # every session ended
    assert login(site.client(), "sam", "a brand new phrase!").status_code == 302


def test_sign_out_other_sessions(site) -> None:
    client, other = site.client(), site.client()
    login(client, "boss")
    login(other, "boss")
    assert "Sign out all other sessions" in client.get("/app/account").get_data(as_text=True)
    post(client, "/app/account/sign-out-others", {})
    assert other.get("/app/").status_code == 302 and client.get("/app/").status_code == 200


def test_disabling_an_account_ends_its_sessions(site) -> None:
    client = site.member("sam")
    site.db.set_status("sam", "disabled")
    assert client.get("/app/").status_code == 302


def test_session_cookie_garbage_is_ignored(site) -> None:
    client = site.client()
    for junk in ("", "x" * 4000, "' OR 1=1 --"):
        client.set_cookie(SESSION_COOKIE, junk)
        assert client.get("/app/").status_code == 302


# Requests and headers


def test_wrong_host_and_cross_origin_posts_are_refused(site) -> None:
    client = site.client()
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400  # DNS rebinding
    assert client.get("/", headers={"Host": "localhost:8765"}).status_code == 200
    assert client.get("/", headers={"Host": "[::1]:8765"}).status_code == 200
    login(client, "boss")
    csrf = token(client, "/app/account")
    resp = client.post("/app/logout", data={"csrf_token": csrf}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 400
    assert client.open("/", method="PUT").status_code == 405
    assert client.get("/no-such-page").status_code == 404


def test_security_headers(site) -> None:
    resp = site.client().get("/")
    h = resp.headers
    assert "script-src" not in h["Content-Security-Policy"] and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer" and "Strict-Transport-Security" not in h
    client = site.client()
    login(client, "boss")
    assert client.get("/app/").headers["Cache-Control"] == "no-store"
    assert client.get("/").headers["Cache-Control"] == "no-store"  # signed-in pages are never cached


def test_secure_cookies_and_hsts_behind_proxy(tmp_path: Path) -> None:
    db = SiteDB(tmp_path / "a.db")
    client = create_app(FileCaseStore(tmp_path / "cases"), db, secure_cookies=True).test_client()
    resp = client.get("/login", base_url="https://localhost")
    assert "Secure" in resp.headers["Set-Cookie"] and "max-age" in resp.headers["Strict-Transport-Security"]
