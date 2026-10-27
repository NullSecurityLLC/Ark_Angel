from pathlib import Path

import pytest

pytest.importorskip("flask")

from click.testing import CliRunner  # noqa: E402
from web_helpers import PASSWORD, Site, new_case, post, publish  # noqa: E402

from ark_angel.cli import cli  # noqa: E402
from ark_angel.web import notify  # noqa: E402
from ark_angel.web.app import create_app  # noqa: E402
from ark_angel.web.notify import SmtpConfig, build_message  # noqa: E402

CONFIG = SmtpConfig(host="smtp.example.org", port=587, sender="alerts@example.org", user="u", password="hunter2hunter2",
                    admin_emails=("ops@example.org", "oncall@example.org"))


class FakeMailer:
    enabled = True

    def __init__(self, config: SmtpConfig = CONFIG) -> None:
        self.config = config
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def to(self, address: str) -> list[tuple[str, str, str]]:
        return [m for m in self.sent if m[0] == address]


@pytest.fixture
def mailed(tmp_path: Path):
    site = Site(tmp_path)
    site.mailer = FakeMailer()
    site.app = create_app(site.store, site.db, mailer=site.mailer)
    site.app.config["TESTING"] = True
    site.db.set_email(site.db.user("boss")["id"], "boss@example.org")
    return site


def _signup(client, username: str, email: str = ""):
    return post(client, "/signup", {"username": username, "password": PASSWORD, "password2": PASSWORD,
                                    "note": "Volunteer with the county search team", "email": email},
                form_page="/signup")


# Configuration


def test_smtp_config_from_env() -> None:
    assert SmtpConfig.from_env({}) is None
    env = {"ARK_ANGEL_SMTP_HOST": "smtp.example.org", "ARK_ANGEL_SMTP_FROM": "alerts@example.org",
           "ARK_ANGEL_SMTP_PASSWORD": "s3cret-pass", "ARK_ANGEL_ADMIN_EMAIL": "a@example.org, b@example.org"}
    config = SmtpConfig.from_env(env)
    assert (config.port, config.security, config.admin_emails) == (587, "starttls", ("a@example.org", "b@example.org"))
    assert "s3cret-pass" not in repr(config)  # never ends up in a log line or traceback
    assert SmtpConfig.from_env({**env, "ARK_ANGEL_SMTP_SECURITY": "ssl"}).port == 465
    with pytest.raises(ValueError, match="only allowed for a mail relay on localhost"):
        SmtpConfig.from_env({**env, "ARK_ANGEL_SMTP_SECURITY": "none"})
    assert SmtpConfig.from_env({**env, "ARK_ANGEL_SMTP_HOST": "localhost", "ARK_ANGEL_SMTP_SECURITY": "none"}).port == 25
    with pytest.raises(ValueError, match="SECURITY"):
        SmtpConfig.from_env({**env, "ARK_ANGEL_SMTP_SECURITY": "tls-ish"})
    with pytest.raises(ValueError, match="SMTP_FROM"):
        SmtpConfig.from_env({"ARK_ANGEL_SMTP_HOST": "smtp.example.org"})
    with pytest.raises(ValueError, match="email address"):
        SmtpConfig.from_env({**env, "ARK_ANGEL_ADMIN_EMAIL": "not an address"})


def test_messages_cannot_carry_injected_headers() -> None:
    msg = build_message(CONFIG, "pat@example.org", "Subject", "Body")
    assert msg["To"] == "pat@example.org" and msg["Auto-Submitted"] == "auto-generated"
    with pytest.raises(ValueError):
        build_message(CONFIG, "pat@example.org\nBcc: everyone@example.org", "Subject", "Body")


def test_delivery_uses_verified_starttls_and_login(monkeypatch) -> None:
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append(("quit",))

        def starttls(self, context):
            calls.append(("starttls", context.verify_mode.name, context.check_hostname))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, msg):
            calls.append(("send", msg["To"]))

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    mailer = notify.Mailer(CONFIG)
    mailer.send("pat@example.org", "Hi", "Body")
    mailer._queue.join()  # the background thread has delivered it
    assert calls == [("connect", "smtp.example.org", 587), ("starttls", "CERT_REQUIRED", True),
                     ("login", "u", "hunter2hunter2"), ("send", "pat@example.org"), ("quit",)]


def test_delivery_failures_are_logged_without_details(monkeypatch, caplog) -> None:
    import smtplib

    class Broken:
        def __init__(self, *a, **k):
            raise smtplib.SMTPAuthenticationError(535, b"bad creds for u / hunter2hunter2")

    monkeypatch.setattr("smtplib.SMTP", Broken)
    mailer = notify.Mailer(CONFIG)
    with caplog.at_level("ERROR", logger="ark_angel.web.mail"):
        mailer.send("pat@example.org", "Hi", "Body")
        mailer._queue.join()
    assert "SMTPAuthenticationError" in caplog.text
    assert "hunter2" not in caplog.text and "pat@example.org" not in caplog.text


# Admin alerts


def test_signup_alerts_admins_throttled(mailed) -> None:
    resp = _signup(mailed.client(), "volunteer1", "vol@example.org")
    assert resp.status_code == 200
    assert mailed.db.user("volunteer1")["email"] == "vol@example.org"
    boss = mailed.mailer.to("boss@example.org")
    assert len(boss) == 1 and "New account request" in boss[0][1] and "/admin/users" in boss[0][2]
    assert "volunteer1" not in boss[0][2] and "county search" not in boss[0][2]  # no details in email
    # Every ARK_ANGEL_ADMIN_EMAIL address gets them too, each throttled on its own.
    assert len(mailed.mailer.to("ops@example.org")) == len(mailed.mailer.to("oncall@example.org")) == 1

    _signup(mailed.client(), "volunteer2")
    assert len(mailed.mailer.to("boss@example.org")) == 1  # within 10 minutes: no second email
    mailed.clock.now += notify.ADMIN_WINDOW_S + 1
    _signup(mailed.client(), "volunteer3")
    assert len(mailed.mailer.to("boss@example.org")) == 2


def test_bad_signup_email_is_refused(mailed) -> None:
    resp = _signup(mailed.client(), "volunteer1", "nope@@example")
    assert resp.status_code == 400 and "look like an email address" in resp.get_data(as_text=True)
    assert mailed.db.user("volunteer1") is None and not mailed.mailer.sent


def test_approval_emails_the_new_member(mailed) -> None:
    _signup(mailed.client(), "volunteer1", "vol@example.org")
    admin = mailed.admin()
    post(admin, "/admin/users/volunteer1", {"action": "approve"}, form_page="/admin/users")
    mail = mailed.mailer.to("vol@example.org")
    assert len(mail) == 1 and "approved" in mail[0][1] and "/login" in mail[0][2]
    post(admin, "/admin/users/volunteer1", {"action": "disable"}, form_page="/admin/users")
    post(admin, "/admin/users/volunteer1", {"action": "enable"}, form_page="/admin/users")
    assert len(mailed.mailer.to("vol@example.org")) == 1  # re-enabling isn't a new approval


def test_profile_review_emails(mailed) -> None:
    owner = mailed.member("owner")
    mailed.db.set_email(mailed.db.user("owner")["id"], "owner@example.org")
    case_id = new_case(owner, "Riley Tamsin secret title")
    page = f"/app/cases/{case_id}/profile"
    post(owner, page, {"action": "request", "name": "Riley Tamsin", "police_report": "PD 1", "attest": "yes"},
         form_page=page)
    alert = mailed.mailer.to("boss@example.org")[-1]
    assert "waiting for review" in alert[1] and "/admin/publications" in alert[2] and "Riley" not in alert[2]

    admin = mailed.admin()
    post(admin, f"/admin/publications/{case_id}", {"decision": "reject", "note": "Report number doesn't match"},
         form_page="/admin/publications")
    sent_back = mailed.mailer.to("owner@example.org")[-1]
    assert "sent back" in sent_back[1] and page in sent_back[2] and "doesn't match" not in sent_back[2]

    post(owner, page, {"action": "request", "name": "Riley Tamsin", "police_report": "PD 2", "attest": "yes"},
         form_page=page)
    post(admin, f"/admin/publications/{case_id}",
         {"decision": "approve", "version": str(mailed.db.case(case_id)["draft_version"])}, form_page="/admin/publications")
    assert "now public" in mailed.mailer.to("owner@example.org")[-1][1]


# Tips


def test_tip_emails_reach_members_without_tip_details(mailed, tmp_path: Path) -> None:
    owner = mailed.member("owner")
    mailed.member("viewer")
    mailed.member("quiet")
    mailed.member("noaddress")
    case_id = new_case(owner, "Riley Tamsin, Denver")
    for name in ("viewer", "quiet", "noaddress"):
        mailed.db.set_member(case_id, name, "viewer")
    mailed.db.set_email(mailed.db.user("owner")["id"], "owner@example.org")
    mailed.db.set_email(mailed.db.user("viewer")["id"], "viewer@example.org")
    mailed.db.set_email(mailed.db.user("quiet")["id"], "quiet@example.org", notify_tips=False)
    slug = publish(mailed, owner, case_id, name="Riley Tamsin")
    mailed.mailer.sent.clear()

    tip = {"what": "Saw Riley at the bus station, she had a black eye", "contact_name": "Pat Doe",
           "contact_phone": "303-555-0199", "where": "17th and Wynkoop"}
    post(mailed.client(), f"/missing/{slug}/tip", tip, form_page=f"/missing/{slug}")
    recipients = sorted(m[0] for m in mailed.mailer.sent)
    assert recipients == ["owner@example.org", "viewer@example.org"]  # opted-out and address-less get nothing
    for _, subject, body in mailed.mailer.sent:
        assert f"/app/cases/{case_id}/tips" in body
        for secret in ("Riley", "black eye", "Pat Doe", "555-0199", "Wynkoop", "Denver"):
            assert secret not in subject and secret not in body, secret

    post(mailed.client(), f"/missing/{slug}/tip", {"what": "Another sighting downtown"}, form_page=f"/missing/{slug}")
    assert len(mailed.mailer.sent) == 2  # throttled: one per case per 15 minutes
    mailed.clock.now += notify.TIP_WINDOW_S + 1
    post(mailed.client(), f"/missing/{slug}/tip", {"what": "Third sighting by the river"}, form_page=f"/missing/{slug}")
    assert len(mailed.mailer.sent) == 4

    mailed.db.set_status("viewer", "disabled")
    mailed.clock.now += notify.TIP_WINDOW_S + 1
    post(mailed.client(), f"/missing/{slug}/tip", {"what": "Fourth sighting at the park"}, form_page=f"/missing/{slug}")
    assert [m[0] for m in mailed.mailer.sent[4:]] == ["owner@example.org"]  # disabled accounts get nothing


def test_account_email_settings(mailed) -> None:
    member = mailed.member("sam")
    resp = post(member, "/app/account/email", {"email": "bad address"})
    assert resp.status_code == 400 and "look like an email address" in resp.get_data(as_text=True)
    post(member, "/app/account/email", {"email": "sam@example.org", "notify_tips": "yes"})
    user = mailed.db.user("sam")
    assert (user["email"], user["notify_tips"]) == ("sam@example.org", 1)
    post(member, "/app/account/email", {"email": "sam@example.org"})  # box unticked
    assert mailed.db.user("sam")["notify_tips"] == 0
    post(member, "/app/account/email", {"email": ""})
    assert mailed.db.user("sam")["email"] == ""
    assert post(mailed.client(), "/app/account/email", {"email": "x@example.org"}).status_code == 403


def test_no_mailer_means_no_email_and_nothing_breaks(tmp_path: Path) -> None:
    site = Site(tmp_path)  # default create_app: email off
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    resp = post(site.client(), f"/missing/{slug}/tip", {"what": "Saw her at the station"}, form_page=f"/missing/{slug}")
    assert resp.status_code == 302
    assert "hasn't set up outgoing email" in owner.get("/app/account").get_data(as_text=True)


# CLI


def test_cli_test_email_and_admin_email(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases"), "web"]
    result = runner.invoke(cli, [*base, "test-email", "me@example.org"], env={"ARK_ANGEL_SMTP_HOST": ""})
    assert result.exit_code != 0 and "isn't set up" in result.output

    sent = []
    monkeypatch.setattr("ark_angel.web.notify.deliver", lambda config, msgs: sent.extend(msgs))
    env = {"ARK_ANGEL_SMTP_HOST": "smtp.example.org", "ARK_ANGEL_SMTP_FROM": "alerts@example.org"}
    result = runner.invoke(cli, [*base, "test-email", "me@example.org"], env=env)
    assert result.exit_code == 0 and sent[0]["To"] == "me@example.org"

    result = runner.invoke(cli, [*base, "create-admin", "graves", "--email", "graves@example.org"],
                           input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0
    from ark_angel.web.db import SiteDB

    assert SiteDB(tmp_path / "web.db").user("graves")["email"] == "graves@example.org"
