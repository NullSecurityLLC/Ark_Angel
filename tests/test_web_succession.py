"""Succession: if an operator goes down, a trusted successor takes over, but only after a waiting period."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("flask")

from web_helpers import PASSWORD, Site, login, new_case, post  # noqa: E402

from ark_angel.web import totp  # noqa: E402
from ark_angel.web.db import SiteDB  # noqa: E402
from ark_angel.web.succession import sweep_succession  # noqa: E402

DAY = 86400
BASE = "https://arkangel.example.org"


class FakeMailer:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def to(self, address: str) -> list[str]:
        return [subject for who, subject, _ in self.sent if who == address]


def _with_2fa(site: Site, username: str) -> str:
    user_id = site.db.user(username)["id"]
    secret = site.db.begin_totp(user_id)
    site.db.confirm_totp(user_id, totp.code_at(secret, totp.step_at(site.clock())))
    site.clock.now += totp.STEP_S
    return secret


@pytest.fixture
def setup(tmp_path):
    site = Site(tmp_path)
    mailer = FakeMailer()
    site.app.extensions["ark_site"].mailer = mailer
    graves = site.member("graves")
    site.db.set_role("graves", "admin")
    site.db.set_email(site.db.user("graves")["id"], "graves@example.org")
    case_a, case_b = new_case(graves, "Riley Tamsin"), new_case(graves, "Dana Whitlock")
    site.member("trusted")
    site.db.set_email(site.db.user("trusted")["id"], "trusted@example.org")
    secret = _with_2fa(site, "trusted")
    return site, mailer, graves, (case_a, case_b), secret


def _name_successor(site, client, successor="trusted", inactive="30", wait="7"):
    return post(client, "/app/account/succession", {"action": "set", "successor": successor, "inactive_days": inactive,
                                                    "wait_days": wait, "password": PASSWORD}, form_page="/app/account")


def test_naming_a_successor_is_guarded(setup) -> None:
    site, mailer, graves, _, _ = setup
    site.member("no2fa")
    assert _name_successor(site, graves, "no2fa").status_code == 400  # a successor needs two-factor on
    assert _name_successor(site, graves, "graves").status_code == 400  # not yourself
    assert _name_successor(site, graves, "nobody").status_code == 400
    assert _name_successor(site, graves, inactive="1").status_code == 400  # only the offered timings
    bad = post(graves, "/app/account/succession", {"action": "set", "successor": "trusted", "inactive_days": "30",
                                                   "wait_days": "7", "password": "wrong password!"},
               form_page="/app/account")
    assert bad.status_code == 400 and site.db.succession(site.db.user("graves")["id"]) is None
    assert _name_successor(site, graves).status_code == 302
    row = site.db.succession(site.db.user("graves")["id"])
    assert row["successor_name"] == "trusted" and row["state"] == "designated"
    assert mailer.to("trusted@example.org") == ["[ArkAngel] graves named you their successor"]


def test_inactivity_starts_a_handover_that_completes_after_the_wait(setup) -> None:
    site, mailer, graves, cases, _ = setup
    _name_successor(site, graves)
    state = site.app.extensions["ark_site"]
    site.clock.now += 29 * DAY
    assert sweep_succession(state, BASE) == {"started": [], "completed": []}
    site.clock.now += 2 * DAY  # 31 days without a sign-in
    assert sweep_succession(state, BASE) == {"started": ["graves"], "completed": []}
    assert "Your cases will be handed over unless you act" in mailer.to("graves@example.org")[-1]
    assert site.db.case_role(cases[0], site.db.user("trusted")["id"]) is None  # nothing moves during the wait
    site.clock.now += 6 * DAY
    assert sweep_succession(state, BASE)["completed"] == []
    site.clock.now += 1 * DAY + 1
    assert sweep_succession(state, BASE) == {"started": [], "completed": ["graves"]}
    graves_id, trusted_id = site.db.user("graves")["id"], site.db.user("trusted")["id"]
    for case_id in cases:
        assert site.db.case_role(case_id, trusted_id) == "owner"
        assert site.db.case_role(case_id, graves_id) == "editor"  # they keep their work
    assert site.db.user("trusted")["role"] == "admin"  # so the site isn't left without an admin
    assert any("You now own graves's cases" in s for s in mailer.to("trusted@example.org"))
    outcomes = [e["outcome"] for e in site.db.recent_events(10, "graves")]
    assert "handover_requested" in outcomes and "handover_completed" in outcomes


def test_any_activity_cancels_a_pending_handover(setup) -> None:
    site, mailer, graves, cases, _ = setup
    _name_successor(site, graves)
    state = site.app.extensions["ark_site"]
    site.clock.now += 31 * DAY
    sweep_succession(state, BASE)
    assert site.db.succession(site.db.user("graves")["id"])["state"] == "pending"
    back = site.client()
    login(back, "graves")  # signing in is the answer to "are they gone?"
    assert site.db.succession(site.db.user("graves")["id"])["state"] == "designated"
    assert any("canceled" in s for s in mailer.to("trusted@example.org"))
    site.clock.now += 31 * DAY
    assert sweep_succession(state, BASE)["started"] == ["graves"]  # inactive again: a fresh handover starts
    login(site.client(), "graves")
    assert site.db.succession(site.db.user("graves")["id"])["state"] == "designated"
    assert sum("canceled" in s for s in mailer.to("trusted@example.org")) == 2
    assert site.db.case_role(cases[0], site.db.user("trusted")["id"]) is None


def test_a_page_load_cancels_it_and_says_so(setup) -> None:
    site, mailer, graves, _, _ = setup
    _name_successor(site, graves)
    site.clock.now += 31 * DAY
    sweep_succession(site.app.extensions["ark_site"], BASE)
    # Simulate a session that's still alive (e.g. a long-open tab): its very next page load cancels it.
    with site.db._connect() as db:
        db.execute("UPDATE sessions SET created_at = ?, last_seen = ?", (site.clock(), site.clock()))
    page = graves.get("/app/account").get_data(as_text=True)
    assert "You being here canceled it" in page
    assert site.db.succession(site.db.user("graves")["id"])["state"] == "designated"


def test_a_successor_can_ask_early_but_only_with_two_factor(setup) -> None:
    site, mailer, graves, cases, secret = setup
    _name_successor(site, graves)
    from web_helpers import UA, token

    trusted = site.client()  # trusted has two-factor on: password, then code
    trusted.post("/login", data={"csrf_token": token(trusted, "/login"), "username": "trusted",
                                 "password": PASSWORD}, headers=UA)
    trusted.post("/login/code", data={"csrf_token": token(trusted, "/login/code"),
                                      "code": totp.code_at(secret, totp.step_at(site.clock()))}, headers=UA)
    page = trusted.get("/app/account").get_data(as_text=True)
    assert "You're the successor for" in page and "graves" in page
    path = "/app/account/succession/graves"
    no_code = post(trusted, path, {"action": "request", "password": PASSWORD}, form_page="/app/account")
    assert no_code.status_code == 400 and site.db.succession(site.db.user("graves")["id"])["state"] == "designated"
    site.clock.now += totp.STEP_S
    ok = post(trusted, path, {"action": "request", "password": PASSWORD,
                              "code": totp.code_at(secret, totp.step_at(site.clock()))}, form_page="/app/account")
    assert ok.status_code == 302
    row = site.db.succession(site.db.user("graves")["id"])
    assert row["state"] == "pending" and row["requested_by"] == "trusted"
    assert row["due_at"] == pytest.approx(site.clock() + 7 * DAY, abs=60)
    warning = [b for to, s, b in mailer.sent if to == "graves@example.org" and "handed over unless" in s][0]
    assert "trusted asked for it" in warning and "just sign in" in warning

    outsider = site.member("outsider")
    assert post(outsider, path, {"action": "request", "password": PASSWORD}, form_page="/app/account").status_code == 404


def test_successor_can_step_down_and_completion_needs_an_active_successor(setup) -> None:
    site, mailer, graves, cases, _ = setup
    _name_successor(site, graves)
    graves_id = site.db.user("graves")["id"]
    site.clock.now += 31 * DAY
    sweep_succession(site.app.extensions["ark_site"], BASE)
    site.db.set_status("trusted", "disabled")
    site.clock.now += 8 * DAY
    assert sweep_succession(site.app.extensions["ark_site"], BASE)["completed"] == []
    assert site.db.case_role(cases[0], site.db.user("trusted")["id"]) is None
    assert site.db.decline_succession(graves_id, site.db.user("trusted")["id"])
    assert site.db.succession(graves_id) is None


def test_emails_name_people_but_never_cases(setup) -> None:
    site, mailer, graves, cases, _ = setup
    _name_successor(site, graves)
    state = site.app.extensions["ark_site"]
    site.clock.now += 31 * DAY
    sweep_succession(state, BASE)
    site.clock.now += 8 * DAY
    sweep_succession(state, BASE)
    assert len(mailer.sent) >= 5
    for _, subject, body in mailer.sent:
        for secret in ("Riley", "Tamsin", "Dana", "Whitlock", *cases):
            assert secret not in subject + body
        assert "/app/account#succession" in body


def test_older_databases_gain_last_active(tmp_path) -> None:
    path = tmp_path / "old.db"
    db = SiteDB(path)
    db.create_user("someone", PASSWORD, status="active")
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE users DROP COLUMN last_active_at")
    con.commit()
    con.close()
    reopened = SiteDB(path)
    assert reopened.user("someone") is not None
    assert sqlite3.connect(path).execute("SELECT last_active_at FROM users").fetchone()[0] is not None
