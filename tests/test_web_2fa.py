"""Two-factor sign-in: setup, the two-step login, replay and brute-force limits, recovery codes, enforcement."""
from __future__ import annotations

import base64
import re
import sqlite3

import pytest

pytest.importorskip("flask")

from click.testing import CliRunner  # noqa: E402

from web_helpers import PASSWORD, UA, Site, login, post, token  # noqa: E402

from ark_angel.web import totp  # noqa: E402
from ark_angel.web.app import SESSION_COOKIE, create_app  # noqa: E402


def _site(tmp_path, policy: str = "admins") -> Site:
    site = Site(tmp_path)
    site.app = create_app(site.store, site.db, require_2fa=policy)
    site.app.config["TESTING"] = True
    return site


def _code(site: Site, secret: str, offset_steps: int = 0) -> str:
    return totp.code_at(secret, totp.step_at(site.clock()) + offset_steps)


def _enroll(site: Site, client, username: str) -> tuple[str, list[str]]:
    """Turn 2FA on through the account page. Returns the secret and the recovery codes shown."""
    resp = post(client, "/app/account/2fa/start", {"password": PASSWORD})
    assert resp.status_code == 200 and "data:image/svg+xml" in resp.get_data(as_text=True)
    secret = site.db.pending_totp(site.db.user(username)["id"])
    resp = post(client, "/app/account/2fa/confirm", {"code": _code(site, secret)})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    codes = re.findall(r"\b[a-z2-9]{5}-[a-z2-9]{5}\b", resp.get_data(as_text=True))
    return secret, codes


def _password_step(client, username: str):
    return client.post("/login", data={"csrf_token": token(client, "/login"), "username": username,
                                       "password": PASSWORD}, headers=UA)


def _code_step(client, code: str):
    return client.post("/login/code", data={"csrf_token": token(client, "/login/code"), "code": code}, headers=UA)


def _member_with_2fa(site: Site, name: str = "riley"):
    client = site.member(name)
    secret, codes = _enroll(site, client, name)
    post(client, "/app/logout", {})
    site.clock.now += totp.STEP_S  # the setup code's step is spent
    return secret, codes


def test_totp_matches_rfc_6238() -> None:
    secret = base64.b32encode(b"12345678901234567890").decode()
    for when, expected in ((59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"),
                           (2000000000, "69279037")):
        assert totp.code_at(secret, totp.step_at(when), digits=8) == expected
    code = totp.code_at(secret, totp.step_at(1000))
    assert totp.verify(secret, code, 1000) == totp.step_at(1000)
    assert totp.verify(secret, code, 1000 + 30) is not None  # one step of clock drift
    assert totp.verify(secret, code, 1000 + 90) is None
    assert totp.verify(secret, code, 1000, last_step=totp.step_at(1000)) is None  # never twice
    assert totp.verify(secret, "12345", 1000) is None and totp.verify(secret, "abcdef", 1000) is None


def test_setup_needs_the_password_and_a_working_code(tmp_path) -> None:
    site = _site(tmp_path, "off")
    client = site.member("riley")
    other = site.client()
    login(other, "riley")  # a second session, e.g. someone who stole the password earlier

    assert post(client, "/app/account/2fa/start", {"password": "wrong password!!"}).status_code == 400
    post(client, "/app/account/2fa/start", {"password": PASSWORD})
    user_id = site.db.user("riley")["id"]
    secret = site.db.pending_totp(user_id)
    page = client.get("/app/account").get_data(as_text=True)
    assert "Set up two-factor" in page  # pending isn't on until a code proves the app has it
    bad = post(client, "/app/account/2fa/confirm", {"code": "000000"})
    assert bad.status_code == 400 and "didn" in bad.get_data(as_text=True)

    ok = post(client, "/app/account/2fa/confirm", {"code": _code(site, secret)})
    codes = re.findall(r"\b[a-z2-9]{5}-[a-z2-9]{5}\b", ok.get_data(as_text=True))
    assert ok.status_code == 200 and len(set(codes)) == 10
    assert site.db.user("riley")["mfa"] == 1
    stored = sqlite3.connect(site.db.path).execute("SELECT code_hash FROM recovery_codes").fetchall()
    assert len(stored) == 10 and not any(c.replace("-", "") in str(stored) for c in codes)
    assert "Save these recovery codes" not in client.get("/app/account").get_data(as_text=True)  # shown once
    assert other.get("/app/").status_code == 302  # the other session was signed out
    assert "2fa_on" in [e["outcome"] for e in site.db.recent_events(5, "riley")]


def test_the_password_alone_never_opens_a_session(tmp_path) -> None:
    site = _site(tmp_path)
    secret, _ = _member_with_2fa(site)
    client = site.client()
    resp = _password_step(client, "riley")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login/code")
    assert client.get_cookie(SESSION_COOKIE) is None
    cookie = client.get_cookie("ark_mfa", path="/login")
    assert cookie is not None and cookie.http_only and cookie.same_site == "Strict"
    assert client.get("/app/").status_code == 302  # still signed out
    assert "Signing in as riley" in client.get("/login/code").get_data(as_text=True)

    resp = _code_step(client, _code(site, secret))
    assert resp.status_code == 302 and client.get_cookie(SESSION_COOKIE) is not None
    assert client.get("/app/").status_code == 200
    assert client.get("/login/code").status_code == 302  # the challenge is gone


def test_codes_cant_be_replayed_or_brute_forced(tmp_path) -> None:
    site = _site(tmp_path)
    secret, _ = _member_with_2fa(site)
    used = _code(site, secret)
    first = site.client()
    _password_step(first, "riley")
    assert _code_step(first, used).status_code == 302

    second = site.client()  # someone watching over a shoulder, same 30 seconds
    _password_step(second, "riley")
    assert _code_step(second, used).status_code == 401
    assert second.get_cookie(SESSION_COOKIE) is None

    # Five wrong codes end the challenge, and they count toward the account lockout.
    for _ in range(3):
        _code_step(second, "123456")
    last = _code_step(second, "654321")
    assert last.status_code == 302 and "notice=restart" in last.headers["Location"]
    attacker = site.client()
    locked = _password_step(attacker, "riley")
    assert locked.status_code == 401 and "Too many failed sign-ins" in locked.get_data(as_text=True)


def test_a_challenge_expires(tmp_path) -> None:
    site = _site(tmp_path)
    secret, _ = _member_with_2fa(site)
    client = site.client()
    _password_step(client, "riley")
    site.clock.now += 5 * 60 + 1
    resp = client.get("/login/code")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")
    assert client.post("/login/code", data={"csrf_token": token(client, "/login"), "code": _code(site, secret)},
                       headers=UA).status_code == 302
    assert client.get_cookie(SESSION_COOKIE) is None


def test_recovery_codes_work_once(tmp_path) -> None:
    site = _site(tmp_path)
    _, codes = _member_with_2fa(site)
    client = site.client()
    _password_step(client, "riley")
    assert _code_step(client, codes[0].upper().replace("-", " ")).status_code == 302  # forgiving about format
    assert client.get_cookie(SESSION_COOKIE) is not None
    assert "9 recovery codes left" in client.get("/app/account").get_data(as_text=True)
    again = site.client()
    _password_step(again, "riley")
    assert _code_step(again, codes[0]).status_code == 401


def test_admins_must_set_it_up_before_doing_anything(tmp_path) -> None:
    site = _site(tmp_path, "admins")
    admin = site.client()
    login(admin, "boss")
    for path in ("/admin/", "/admin/users", "/app/", "/app/cases/new"):
        resp = admin.get(path)
        assert resp.status_code == 302, path
        assert resp.headers["Location"] == "/app/account?notice=2fa_required#two-factor"
    page = admin.get("/app/account?notice=2fa_required").get_data(as_text=True)
    assert "Required for your account" in page and "requires two-factor sign-in" in page
    site.db.create_user("newbie", PASSWORD)  # pending sign-up
    assert post(admin, "/admin/users/newbie", {"action": "approve"}).status_code == 403
    assert site.db.user("newbie")["status"] == "pending"

    member = site.member("riley")  # members aren't forced under "admins"
    assert member.get("/app/").status_code == 200

    _enroll(site, admin, "boss")
    assert admin.get("/admin/users").status_code == 200
    assert "2FA" in admin.get("/admin/users").get_data(as_text=True)


def test_everyone_policy_and_turning_it_off(tmp_path) -> None:
    site = _site(tmp_path, "everyone")
    member = site.member("riley")
    assert member.get("/app/").headers["Location"].startswith("/app/account")
    secret, _ = _enroll(site, member, "riley")
    site.clock.now += totp.STEP_S
    refused = post(member, "/app/account/2fa/disable", {"password": PASSWORD, "code": _code(site, secret)})
    assert refused.status_code == 400 and "can&#39;t be turned off" in refused.get_data(as_text=True)

    site.app = create_app(site.store, site.db, require_2fa="off")
    site.app.config["TESTING"] = True
    member = site.client()
    _password_step(member, "riley")
    _code_step(member, _code(site, secret))
    site.clock.now += totp.STEP_S
    assert post(member, "/app/account/2fa/disable", {"password": PASSWORD, "code": "000000"}).status_code == 400
    assert post(member, "/app/account/2fa/disable", {"password": "nope nope nope", "code": _code(site, secret)}
                ).status_code == 400
    ok = post(member, "/app/account/2fa/disable", {"password": PASSWORD, "code": _code(site, secret)})
    assert ok.status_code == 200 and site.db.user("riley")["mfa"] == 0
    assert site.db.recovery_codes_left(site.db.user("riley")["id"]) == 0


def test_new_recovery_codes_replace_the_old(tmp_path) -> None:
    site = _site(tmp_path, "off")
    member = site.member("riley")
    secret, old = _enroll(site, member, "riley")
    site.clock.now += totp.STEP_S
    resp = post(member, "/app/account/2fa/recovery", {"password": PASSWORD, "code": _code(site, secret)})
    new = re.findall(r"\b[a-z2-9]{5}-[a-z2-9]{5}\b", resp.get_data(as_text=True))
    assert len(new) == 10 and not set(new) & set(old)
    assert not site.db.check_second_factor(site.db.user("riley")["id"], old[0])
    assert site.db.check_second_factor(site.db.user("riley")["id"], new[0])


def test_cli_reset(tmp_path) -> None:
    from ark_angel.cli import cli

    site = _site(tmp_path)
    _member_with_2fa(site)
    client = site.client()
    _password_step(client, "riley")
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases"), "web", "--db", str(site.db.path)]
    result = runner.invoke(cli, [*base, "reset-2fa", "riley", "--yes"])
    assert result.exit_code == 0, result.output
    assert site.db.user("riley")["mfa"] == 0
    assert client.get("/login/code").status_code == 302  # its pending sign-in is gone too
    assert "2fa_reset" in [e["outcome"] for e in site.db.recent_events(5, "riley")]
    fresh = site.client()
    assert _password_step(fresh, "riley").headers["Location"] == "/app/"  # straight in: set it up again
