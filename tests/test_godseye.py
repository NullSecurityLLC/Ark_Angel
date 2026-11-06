"""God's Eye: the principal-defender case's standby emergency protocol."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from ark_angel import godseye, pipeline
from ark_angel.cases.store import FileCaseStore
from ark_angel.cli import cli
from ark_angel.defend_demo import build_defend_demo
from ark_angel.models import Case, Evidence
from ark_angel.report.godseye import render_godseye_markdown, render_godseye_text

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
TUESDAY_0630 = datetime(2026, 11, 10, 6, 30)  # local time, like evidence timestamps
FRIDAY_2000 = datetime(2026, 11, 13, 20, 0)


@pytest.fixture
def demo(tmp_path: Path):
    store = FileCaseStore(tmp_path / "cases")
    case = build_defend_demo(store, tmp_path / "inputs", log=lambda *_: None)
    return store, case


def _armed(case, hours: float = 24):
    godseye.arm(case, "blue-1234", "red-9999", hours, ("team@example.org",), "analyst", T0)
    return case


# The state machine


def test_arming_validates(demo) -> None:
    _, case = demo
    for kwargs, message in (({"code": "123"}, "4 to 128"), ({"code": "same1", "duress_code": "same1"}, "differ"),
                            ({"code": "abcd", "checkin_hours": -1}, "interval"),
                            ({"code": "abcd", "contacts": ("not an email",)}, "email")):
        with pytest.raises(ValueError, match=message):
            godseye.arm(case, **kwargs)
    with pytest.raises(godseye.ProtocolError, match="isn't a principal-defender case"):
        godseye.arm(Case("C", "x"), "abcd")


def test_codes_are_never_stored(demo) -> None:
    store, case = demo
    _armed(case)
    store.save_case(case)
    raw = (store.data_dir / f"{case.identifier}.json").read_text()
    assert "blue-1234" not in raw and "red-9999" not in raw and "scrypt$" in raw


def test_check_ins_duress_and_wrong_codes(demo) -> None:
    _, case = demo
    _armed(case)
    assert godseye.check_in(case, "blue-1234", "dana", T0 + timedelta(hours=3)) is False
    protocol = case.principal.protocol
    assert protocol.state == "standby" and godseye.deadline(protocol) == T0 + timedelta(hours=27)
    with pytest.raises(godseye.ProtocolError, match="isn't right"):
        godseye.check_in(case, "guess", "someone", T0)
    assert godseye.check_in(case, "red-9999", "dana", T0 + timedelta(hours=4)) is True
    assert protocol.state == "active" and protocol.trigger == "duress"
    assert [e["event"] for e in protocol.events] == ["armed", "checked in", "failed check-in", "activated"]


def test_a_missed_check_in_activates_it(demo) -> None:
    _, case = demo
    _armed(case, hours=24)
    assert not godseye.check_deadline(case, T0 + timedelta(hours=23))
    assert godseye.check_deadline(case, T0 + timedelta(hours=24, minutes=1))
    protocol = case.principal.protocol
    assert protocol.state == "active" and protocol.trigger == "missed check-in"
    assert protocol.snapshot_sha256 == godseye.fingerprint(case)
    assert not godseye.check_deadline(case, T0 + timedelta(days=5))  # activates once, not again


def test_no_deadline_means_manual_and_duress_only(demo) -> None:
    _, case = demo
    _armed(case, hours=0)
    assert godseye.deadline(case.principal.protocol) is None
    assert not godseye.check_deadline(case, T0 + timedelta(days=365))


def test_activation_needs_a_reason_and_stand_down_a_note(demo) -> None:
    _, case = demo
    _armed(case)
    with pytest.raises(godseye.ProtocolError, match="why"):
        godseye.activate(case, "manual", " ")
    godseye.activate(case, "manual", "unreachable since 18:00", "analyst", T0)
    with pytest.raises(godseye.ProtocolError, match="stand it down"):
        godseye.disarm(case)
    with pytest.raises(godseye.ProtocolError, match="why"):
        godseye.stand_down(case, "")
    godseye.stand_down(case, "reached in person, safe", "analyst", T0 + timedelta(hours=1))
    assert case.principal.protocol.state == "standby"
    assert godseye.deadline(case.principal.protocol) == T0 + timedelta(hours=25)  # the clock restarts


def test_the_fingerprint_seals_the_case_as_it_stood(demo) -> None:
    store, case = demo
    _armed(case)
    godseye.activate(case, "manual", "test", "analyst", T0)
    sealed = case.principal.protocol.snapshot_sha256
    assert godseye.fingerprint(case) == sealed  # the protocol's own log doesn't change it
    case.evidence.append(Evidence("late:1", "text", "a new tip", {"source": "tips"}))
    assert godseye.fingerprint(case) != sealed


# The briefing


def test_briefing_is_time_aware(demo) -> None:
    _, case = demo
    on_tuesday = godseye.briefing(case, TUESDAY_0630)
    first = on_tuesday["places"][0]
    assert first["priority"] == 1 and "around now" in first["why"] and "Tuesdays around 06:14" in first["why"]
    on_friday = godseye.briefing(case, FRIDAY_2000)
    assert on_friday["places"][0]["priority"] == 2  # last known location leads instead
    assert all(place["priority"] != 1 for place in on_friday["places"])


def test_briefing_ranks_places_a_threat_knows(demo) -> None:
    _, case = demo
    data = godseye.briefing(case, FRIDAY_2000)
    labels = [place["label"] for place in data["places"]]
    assert labels.index("Home") < labels.index("Work")
    home = next(place for place in data["places"] if place["label"] == "Home")
    assert home["priority"] == 3 and "Rook" in home["why"]
    rook = data["threats"][0]
    assert rook["name"] == "Rook" and rook["knows"] == ["Home"] and rook["escalating"]
    assert any("Give police everything on Rook" in action and "They know Home" in action for action in data["actions"])
    assert data["principal"]["phones"] == ["3035550182"]
    assert any("Sensitive place Home is exposed" in item for item in data["exposed"])


def test_briefing_renders_safely(demo) -> None:
    _, case = demo
    case.evidence.append(Evidence("x:1", "text", "I know where you live ![p](https://evil.example/px.png) \x1b]0;pwn\x07",
                                  {"source": "dms", "sender": "Mal\x1b[2J"}))
    data = godseye.briefing(case, FRIDAY_2000)
    text = render_godseye_text(data)
    markdown = render_godseye_markdown(data)
    assert "\x1b" not in text
    assert not re.search(r"(?<!\\)\]\(https://evil", markdown)  # escaped, so a preview loads nothing


# Watching and alerting


def test_sweep_activates_only_overdue_armed_defender_cases(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("MISSING", "a missing-person case"))
    for case_id, hours in (("LATE", 24), ("ON-TIME", 72), ("NO-DEADLINE", 0)):
        pipeline.create_defender_case(store, case_id, "Dana", "the principal")
        case = store.get_case(case_id)
        godseye.arm(case, "abcd1234", checkin_hours=hours, now=T0)
        store.save_case(case)
    pipeline.create_defender_case(store, "UNARMED", "Dana", "the principal")
    alerted = []
    assert godseye.sweep(store, store.list_cases(), alert=alerted.append, now=T0 + timedelta(hours=30)) == ["LATE"]
    assert [case.identifier for case in alerted] == ["LATE"]
    assert store.get_case("LATE").principal.protocol.state == "active"
    assert store.get_case("ON-TIME").principal.protocol.state == "standby"


def test_alert_email_carries_no_case_content(demo, monkeypatch) -> None:
    _, case = demo
    _armed(case)
    godseye.activate(case, "manual", "Dana unreachable near 1187 Demo Lane", "analyst", T0)
    assert godseye.alert_contacts(case, env={}) == (0, "email isn't set up (ARK_ANGEL_SMTP_*)")
    sent = []
    import ark_angel.web.notify as notify

    monkeypatch.setattr(notify, "alert_now", lambda config, to, subject, body: sent.append((to, subject, body)) or 1)
    env = {"ARK_ANGEL_SMTP_HOST": "smtp.example.org", "ARK_ANGEL_SMTP_FROM": "arkangel@example.org"}
    assert godseye.alert_contacts(case, env=env) == (1, "")
    to, subject, body = sent[0]
    assert to == ["team@example.org"] and "EMERGENCY" in subject
    for secret in ("Dana", "Whitlock", "Demo Lane", "3035550182", "Rook", "unreachable"):
        assert secret not in subject + body


# The command line


def _cli(tmp_path: Path, *args: str, stdin: str = ""):
    return CliRunner().invoke(cli, ["--no-color", "--data-dir", str(tmp_path / "cases"), *args], input=stdin)


def test_cli_duress_looks_exactly_like_a_check_in(demo, tmp_path: Path) -> None:
    armed = _cli(tmp_path, "defend", "godseye", "arm", "DEFEND-DEMO", "--every", "24",
                 stdin="blue-1234\nblue-1234\nred-9999\nred-9999\n")
    assert armed.exit_code == 0 and "on standby" in armed.output, armed.output
    normal = _cli(tmp_path, "defend", "godseye", "checkin", "DEFEND-DEMO", stdin="blue-1234\n")
    duress = _cli(tmp_path, "defend", "godseye", "checkin", "DEFEND-DEMO", stdin="red-9999\n")
    assert normal.exit_code == duress.exit_code == 0
    assert normal.output == duress.output  # same words, same deadline, nothing different on screen
    assert "Checked in." in duress.output and "ACTIVE" not in duress.output
    status = _cli(tmp_path, "defend", "godseye", "status", "DEFEND-DEMO").output
    assert "God's Eye ACTIVE" in status and "duress" in status
    wrong = _cli(tmp_path, "defend", "godseye", "checkin", "DEFEND-DEMO", stdin="nope\n")
    assert wrong.exit_code == 1 and "isn't right" in wrong.output


def test_cli_activate_brief_stand_down(demo, tmp_path: Path) -> None:
    _cli(tmp_path, "defend", "godseye", "arm", "DEFEND-DEMO", "--no-duress", stdin="blue-1234\nblue-1234\n")
    result = _cli(tmp_path, "defend", "godseye", "activate", "DEFEND-DEMO", "--reason", "unreachable since 18:00")
    assert result.exit_code == 0 and "GOD'S EYE ACTIVE" in result.output and "No email sent" in result.output
    brief = _cli(tmp_path, "defend", "godseye", "brief", "DEFEND-DEMO")
    assert "STATUS  ACTIVE" in brief.output and "WHERE TO LOOK FIRST" in brief.output and "Home" in brief.output
    data = json.loads(_cli(tmp_path, "defend", "godseye", "brief", "DEFEND-DEMO", "--format", "json").output)
    assert data["protocol"]["state"] == "active" and data["threats"][0]["name"] == "Rook"
    out = tmp_path / "briefing.md"
    assert _cli(tmp_path, "defend", "godseye", "brief", "DEFEND-DEMO", "--format", "md", "--out", str(out)).exit_code == 0
    assert out.read_text().startswith("# EMERGENCY BRIEFING")
    assert _cli(tmp_path, "defend", "godseye", "stand-down", "DEFEND-DEMO", "--note", "safe").exit_code == 0
    assert "STANDBY" in _cli(tmp_path, "defend", "godseye", "status", "DEFEND-DEMO").output


def test_cli_watch_once_fires_on_a_missed_check_in(demo, tmp_path: Path) -> None:
    store, case = demo
    godseye.arm(case, "blue-1234", checkin_hours=1, now=datetime.now(timezone.utc) - timedelta(hours=2))
    store.save_case(case)
    result = _cli(tmp_path, "defend", "godseye", "watch", "--once")
    assert result.exit_code == 0 and "GOD'S EYE ACTIVE for 'DEFEND-DEMO': missed check-in" in result.output
    assert store.get_case("DEFEND-DEMO").principal.protocol.trigger == "missed check-in"


# The website


class FakeMailer:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@pytest.fixture
def web(tmp_path: Path):
    pytest.importorskip("flask")
    from web_helpers import Site

    site = Site(tmp_path)
    owner = site.member("owner")
    site.db.set_email(site.db.user("owner")["id"], "owner@example.org")
    build_defend_demo(site.store, tmp_path / "inputs", log=lambda *_: None)
    site.db.register_case("DEFEND-DEMO", "owner")
    mailer = FakeMailer()
    site.app.extensions["ark_site"].mailer = mailer
    return site, owner, mailer


def _page(client, path: str = "/app/cases/DEFEND-DEMO/godseye"):
    return client.get(path).get_data(as_text=True)


def _post(client, data: dict):
    from web_helpers import post

    return post(client, "/app/cases/DEFEND-DEMO/godseye", data, form_page="/app/cases/DEFEND-DEMO/godseye")


def _arm(owner):
    return _post(owner, {"action": "arm", "code": "blue-1234", "code2": "blue-1234", "duress": "red-9999",
                         "duress2": "red-9999", "every": "24", "contacts": "team@example.org"})


def test_web_duress_check_in_page_is_identical(web) -> None:
    site, owner, mailer = web
    assert _arm(owner).status_code == 302
    assert "STANDBY" in _page(owner)
    normal = _post(owner, {"action": "checkin", "code": "blue-1234"})
    duress = _post(owner, {"action": "checkin", "code": "red-9999"})
    strip = lambda html: re.sub(r'name="csrf_token" value="[^"]+"', "", html)  # noqa: E731
    assert normal.status_code == duress.status_code == 200
    assert strip(normal.get_data(as_text=True)) == strip(duress.get_data(as_text=True))
    assert "Checked in" in duress.get_data(as_text=True) and "ACTIVE" not in duress.get_data(as_text=True)
    assert site.store.get_case("DEFEND-DEMO").principal.protocol.trigger == "duress"
    recipients = sorted(to for to, _, _ in mailer.sent)
    assert recipients == ["owner@example.org", "team@example.org"]
    for _, subject, body in mailer.sent:
        assert "/app/cases/DEFEND-DEMO/godseye" in body and "Dana" not in subject + body and "Rook" not in body


def test_web_manual_activation_and_roles(web) -> None:
    site, owner, mailer = web
    other = site.member("missingcase")
    from web_helpers import new_case

    plain = new_case(other)
    assert other.get(f"/app/cases/{plain}/godseye").status_code == 404  # only defender cases have one
    viewer = site.member("vee")
    site.db.set_member("DEFEND-DEMO", "vee", "viewer")
    _arm(owner)
    assert "Check in" not in _page(viewer) and _post(viewer, {"action": "activate", "reason": "x"}).status_code == 403
    resp = _post(owner, {"action": "activate", "reason": "unreachable since 18:00"})
    assert resp.status_code == 302 and "notice=ge_active" in resp.headers["Location"]
    page = _page(owner)
    assert "GOD'S EYE ACTIVE" in page and "Where to look first" in page and "Rook" in page
    assert "tag alarm" in _page(viewer, "/app/cases/DEFEND-DEMO")  # the tab shows it on every case page
    assert len(mailer.sent) == 2
    assert _post(owner, {"action": "stand_down", "note": ""}).status_code == 400
    assert _post(owner, {"action": "stand_down", "note": "safe"}).status_code == 302
    assert site.store.get_case("DEFEND-DEMO").principal.protocol.state == "standby"


def test_web_watcher_fires_on_a_missed_check_in(web) -> None:
    site, owner, mailer = web
    from ark_angel.web.godseye import sweep_site

    _arm(owner)
    state = site.app.extensions["ark_site"]
    assert sweep_site(state, "https://arkangel.example.org") == []
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    assert sweep_site(state, "https://arkangel.example.org", now=later) == ["DEFEND-DEMO"]
    assert site.store.get_case("DEFEND-DEMO").principal.protocol.trigger == "missed check-in"
    assert any("https://arkangel.example.org/app/cases/DEFEND-DEMO/godseye" in body for _, _, body in mailer.sent)
