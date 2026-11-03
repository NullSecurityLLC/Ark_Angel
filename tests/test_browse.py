"""Browsing capture: the Recorder receiver, history import and the investigator's trail."""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from ark_angel import pipeline
from ark_angel.analysis.correlate import analyze_case
from ark_angel.analysis.trail import build_trail
from ark_angel.browse_server import Recorder, RecorderServer, load_token
from ark_angel.cases.store import FileCaseStore
from ark_angel.cli import cli
from ark_angel.ingest.browse_source import (
    Capture, capture_to_evidence, page_snippets, parse_capture, profile_handle, search_query,
)
from ark_angel.ingest.history_source import load_history
from ark_angel.models import Case, Evidence

TIP = Evidence("tip.txt:1", "text", "Riley texted from 303-555-0147", {"source": "tips"})
AD = ("Room for rent. Quiet, cash only. Text Sam at (303) 555-0147. Also on insta @rt_demo_owl. "
      "Unrelated office line 720-555-0100.")


def _at(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 2, 14, minute, second)


def _web(url: str, minute: int, text: str = "", kind: str = "visit", title: str = "", selection: str = "",
         session: str = "s1", second: int = 0) -> Evidence:
    return capture_to_evidence(Capture(url=url, when=_at(minute, second), kind=kind, title=title, text=text,
                                       selection=selection, session=session))


# URLs


@pytest.mark.parametrize("url, expected", [
    ("https://www.google.com/search?q=303-555-0147&hl=en", ("Google", "303-555-0147")),
    ("https://www.google.co.uk/search?q=riley+tamsin", ("Google", "riley tamsin")),
    ("https://www.bing.com/search?q=%22rt_demo_owl%22", ("Bing", '"rt_demo_owl"')),
    ("https://duckduckgo.com/?q=ABC1234&ia=web", ("DuckDuckGo", "ABC1234")),
    ("https://www.youtube.com/results?search_query=union+station", ("YouTube", "union station")),
    ("https://x.com/search?q=%40rt_demo_owl&src=typed", ("X", "@rt_demo_owl")),
    ("https://www.google.com/maps/place/Union+Station", None),
    ("https://www.google.com/search", None),
    ("https://example.com/search?q=x", None),
])
def test_search_query(url: str, expected) -> None:
    assert search_query(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://www.instagram.com/rt_demo_owl/", ("Instagram", "rt_demo_owl")),
    ("https://www.tiktok.com/@riley.t", ("TikTok", "riley.t")),
    ("https://old.reddit.com/user/Some_Body/", ("Reddit", "some_body")),
    ("https://x.com/home", None),
    ("https://www.instagram.com/explore/", None),
    ("https://www.instagram.com/p/Cx123/", None),
    ("https://github.com/NullSecurityLLC/Ark_Angel", None),
])
def test_profile_handle(url: str, expected) -> None:
    assert profile_handle(url) == expected


def test_page_snippets_catch_entities_and_watched_phrases_but_not_the_profile_itself() -> None:
    text = "x" * 500 + " Text Sam at (303) 555-0147. " + "y" * 500 + " staying near Union Station " + "z" * 500
    snippets = page_snippets(text, watch=["Union Station"])
    assert len(snippets) == 2
    assert "(303) 555-0147" in snippets[0] and len(snippets[0]) < 200
    assert "Union Station" in snippets[1]
    assert page_snippets("my handle @rt_demo_owl", skip=[("handle", "rt_demo_owl")]) == []


def test_parse_capture_validates() -> None:
    assert parse_capture({"url": "https://a.example/x", "when": "2026-11-02T14:03:09"}).when == _at(3, 9)
    for bad, message in (({"url": "file:///etc/passwd"}, "http"), ({"url": "javascript:alert(1)"}, "http"),
                         ({"url": "https://a.example", "kind": "delete"}, "kind"),
                         ({"url": "https://a.example", "kind": "clip"}, "selected text")):
        with pytest.raises(ValueError, match=message):
            parse_capture(bad)
    assert parse_capture({"url": "https://a.example", "session": "../../x;rm"}).session == "x" + "rm"


# What counts as corroboration


def test_only_what_pages_say_corroborates() -> None:
    evidence = [
        TIP,
        _web("https://www.google.com/search?q=303-555-0147", 1),           # you typed it: doesn't count
        _web("https://classifieds.example/ad/1", 2, AD, title="Room for rent"),  # the page printed it: counts
        _web("https://www.instagram.com/rt_demo_owl/", 3, "rt_demo_owl bio", title="@rt_demo_owl"),
    ]
    analysis = analyze_case(evidence)
    leads = {lead.identifier: lead for lead in analysis.leads}
    phone = leads["corr-phone-3035550147"]
    assert phone.evidence_ids == ["tip.txt:1", evidence[2].identifier]
    assert "browsing" in phone.reasons[0] and "tips" in phone.reasons[0]
    # The ad's handle and the profile you opened aren't two sightings of it.
    assert "corr-handle-rt_demo_owl" not in leads
    # The case timeline stays about the subject, not the investigator's clicks.
    assert [event.evidence_id for event in analysis.timeline] == []
    assert not any(lead.identifier.startswith("tag-web") for lead in pipeline.RuleBasedAnalyzer()
                   .analyze_evidence(Case("x", "x"), evidence))


def test_a_clip_counts_but_a_url_you_followed_does_not() -> None:
    tip = Evidence("t:1", "text", "check https://forum.example/thread/9 and plate ABC1234", {"source": "tips"})
    clip = _web("https://forum.example/thread/9", 5, kind="clip", selection="saw the plate ABC1234 again")
    visit = _web("https://forum.example/thread/9", 4, "a forum page", title="thread")
    leads = {lead.identifier for lead in analyze_case([tip, visit, clip]).leads}
    assert "corr-plate-ABC1234" in leads
    assert not any(lid.startswith("corr-url-") for lid in leads)


def test_browsing_finds_exposures_in_defender_cases(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "cases")
    pipeline.create_defender_case(store, "D1", "Dana Whitlock", "the principal",
                                  identifiers=[("phone", "303-555-0182")])
    store.add_evidence("D1", [
        _web("https://www.google.com/search?q=303-555-0182", 1),
        _web("https://peoplefinder.example/dana", 2, "Dana Whitlock, 34. Phone (303) 555-0182", title="Dana W"),
    ])
    leads, _ = pipeline.enrich(store, "D1")
    phone = {lead.identifier: lead for lead in leads}["exp-phone-3035550182"]
    assert len(phone.evidence_ids) == 1
    assert any("on https://peoplefinder.example/dana:" in reason for reason in phone.reasons)


# History import


def _chrome(path: Path, rows) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    con.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    for n, (url, title, when) in enumerate(rows, start=1):
        con.execute("INSERT INTO urls VALUES (?, ?, ?)", (n, url, title))
        micro = (when.astimezone(timezone.utc) - epoch) // timedelta(microseconds=1)
        con.execute("INSERT INTO visits VALUES (?, ?, ?)", (n, n, micro))
    con.commit()
    con.close()


def _firefox(path: Path, rows) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    con.execute("CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER)")
    for n, (url, title, when) in enumerate(rows, start=1):
        con.execute("INSERT INTO moz_places VALUES (?, ?, ?)", (n, url, title))
        con.execute("INSERT INTO moz_historyvisits VALUES (?, ?, ?)", (n, n, int(when.timestamp() * 1_000_000)))
    con.commit()
    con.close()


def _safari(path: Path, rows) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, visit_time REAL, "
                "title TEXT)")
    epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    for n, (url, title, when) in enumerate(rows, start=1):
        con.execute("INSERT INTO history_items VALUES (?, ?)", (n, url))
        con.execute("INSERT INTO history_visits VALUES (?, ?, ?, ?)",
                    (n, n, (when.astimezone(timezone.utc) - epoch).total_seconds(), title))
    con.commit()
    con.close()


HISTORY = [
    ("https://www.google.com/search?q=rt_demo_owl", "rt_demo_owl - Google Search", _at(1)),
    ("https://www.instagram.com/rt_demo_owl/", "Instagram", _at(2)),
    ("chrome://settings/", "Settings", _at(3)),
    ("https://news.example/old", "Before the window", _at(0) - timedelta(days=2)),
]


@pytest.mark.parametrize("builder, fmt", [(_chrome, "chrome"), (_firefox, "firefox"), (_safari, "safari")])
def test_history_import_reads_a_window(tmp_path: Path, builder, fmt: str) -> None:
    path = tmp_path / "History"
    builder(path, HISTORY)
    result = load_history(path, since=_at(0), until=_at(30))
    assert result.format == fmt and result.skipped == 1
    assert [e.metadata["kind"] for e in result.evidence] == ["search", "visit"]
    assert result.evidence[0].metadata["query"] == "rt_demo_owl"
    assert result.evidence[0].metadata["timestamp"] == "2026-11-02 14:01:00"
    assert result.evidence[1].metadata["profile"] == "rt_demo_owl"
    assert all("page_excerpt" not in e.metadata for e in result.evidence)
    with pytest.raises(ValueError, match="not firefox" if fmt != "firefox" else "not chrome"):
        load_history(path, since=_at(0), fmt="chrome" if fmt == "firefox" else "firefox")


def test_history_import_refuses_other_files(tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("not a database")
    with pytest.raises(ValueError):
        load_history(junk, since=_at(0))
    with pytest.raises(ValueError, match="after"):
        load_history(junk, since=_at(5), until=_at(1))


def test_history_cli(tmp_path: Path) -> None:
    path = tmp_path / "History"
    _chrome(path, HISTORY)
    runner = CliRunner()
    base = ["--no-color", "--data-dir", str(tmp_path / "cases")]
    runner.invoke(cli, [*base, "case", "create", "C1", "t"])
    assert runner.invoke(cli, [*base, "ingest-history", "C1", str(path)]).exit_code != 0  # --since is required
    dry = runner.invoke(cli, [*base, "ingest-history", "C1", str(path), "--since", "2026-11-02 14:00",
                              "--until", "2026-11-02 15:00", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "Would import 2 visit(s) from chrome history" in dry.output and "1 search(es), 1 profile" in dry.output
    assert len(FileCaseStore(tmp_path / "cases").get_case("C1").evidence) == 0
    done = runner.invoke(cli, [*base, "ingest-history", "C1", str(path), "--since", "2026-11-02"])
    assert done.exit_code == 0 and "Imported 2 visit(s)" in done.output


# The trail


def test_trail_pivots_threads_and_loose_ends() -> None:
    evidence = [
        TIP,
        _web("https://www.google.com/search?q=303-555-0147", 1),
        _web("https://classifieds.example/ad/1", 2, AD, title="Room for rent"),
        _web("https://www.instagram.com/rt_demo_owl/", 6, "bio: night owl", title="rt_demo_owl"),
        _web("https://forum.example/t/9", 9, kind="clip", selection="plate ABC1234 parked out back"),
        _web("https://news.example/later", 59, "nothing", title="Much later", session="s1"),  # gap: new session
    ]
    trail = build_trail(evidence)
    assert len(trail.sessions) == 2
    first = trail.sessions[0].steps
    assert [s.kind for s in first] == ["search", "visit", "visit", "clip"]
    assert first[0].dwell == timedelta(minutes=1) and first[2].dwell == timedelta(minutes=3)
    search, ad, profile, clip = first
    assert search.pivots[("phone", "3035550147")].evidence_id == "tip.txt:1"
    assert ad.hits == {("phone", "3035550147"): ["tip.txt:1"]}
    assert profile.pivots[("handle", "rt_demo_owl")].evidence_id == ad.evidence_id  # you saw it on the ad first
    assert [key for key, _ in trail.threads] == [("phone", "3035550147"), ("handle", "rt_demo_owl")]
    # The plate you clipped was never looked up; the phone you searched isn't a loose end.
    assert [(key, step.kind) for key, step, _ in trail.loose_ends] == [(("plate", "ABC1234"), "clip")]


def test_trail_cli_formats(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("C1", "t"))
    runner = CliRunner()
    base = ["--no-color", "--data-dir", str(tmp_path / "cases")]
    assert "No browsing recorded" in runner.invoke(cli, [*base, "trail", "C1"]).output
    store.add_evidence("C1", [TIP, _web("https://www.google.com/search?q=303-555-0147", 1),
                              _web("https://classifieds.example/ad/1", 2, AD, title="Room for rent")])
    text = runner.invoke(cli, [*base, "trail", "C1"]).output
    assert "pivot: phone 3035550147, which you'd seen in tips (tip.txt:1)" in text
    assert "MATCH: phone 3035550147 is also in tip.txt:1" in text
    data = json.loads(runner.invoke(cli, [*base, "trail", "C1", "--format", "json"]).output)
    assert data["sessions"][0]["steps"][1]["hits"][0]["value"] == "3035550147"
    assert "| 14:02 | page: [Room for rent](https://classifieds.example/ad/1)" in runner.invoke(
        cli, [*base, "trail", "C1", "--format", "md"]).output


# The receiver


@pytest.fixture
def receiver(tmp_path: Path):
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("C1", "Riley"))
    store.add_evidence("C1", [TIP])
    lines: list[str] = []
    server = RecorderServer(Recorder(store, "C1", log=lines.append), "t" * 32, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, store, lines
    server.shutdown()
    server.server_close()


def _call(server, path: str, body=None, token: str = "t" * 32, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", data=data,
                                     method="POST" if data is not None else "GET")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_receiver_stores_captures_and_flags_matches(receiver) -> None:
    server, store, lines = receiver
    code, body = _call(server, "/capture", {"url": "https://classifieds.example/ad/1", "title": "Room for rent",
                                            "text": AD, "when": "2026-11-02T14:02:00", "session": "abc"},
                       headers={"Origin": "chrome-extension://abcdefghijklmnop"})
    assert code == 200, body
    assert body["hits"] == [{"kind": "phone", "value": "3035550147", "seen_in": ["tips (tip.txt:1)"]}]
    assert any("MATCH phone 3035550147" in line for line in lines)
    stored = store.get_case("C1").evidence[-1]
    assert stored.identifier == body["stored"] and stored.metadata["session"] == "abc"

    code, body = _call(server, "/capture", {"url": "https://other.example/x", "text": "call 303 555 0147"})
    assert body["hits"][0]["seen_in"] == ["tips (tip.txt:1)", "a page on classifieds.example"]
    code, status = _call(server, "/status")
    assert code == 200 and status == {"case": "C1", "title": "Riley", "captured": 2, "matched": 2}


def _oversized(server) -> int:
    """Claim a too-big body; the receiver must refuse it from the header, before reading any of it."""
    import http.client

    con = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    con.putrequest("POST", "/capture")
    con.putheader("Authorization", "Bearer " + "t" * 32)
    con.putheader("Content-Length", str(50 * 1024 * 1024))
    con.endheaders()
    code = con.getresponse().status
    con.close()
    return code


def test_receiver_refuses_strangers(receiver) -> None:
    server, store, _ = receiver
    capture = {"url": "https://a.example/"}
    assert _call(server, "/capture", capture, token="")[0] == 401
    assert _call(server, "/capture", capture, token="wrong" * 8)[0] == 401
    # A page you visit can't write into the case, even if it guessed the token.
    assert _call(server, "/capture", capture, headers={"Origin": "https://evil.example"})[0] == 403
    # DNS rebinding: a hostile name pointed at 127.0.0.1 is refused.
    assert _call(server, "/status", headers={"Host": f"evil.example:{server.port}"})[0] == 421
    assert _call(server, "/capture", b"not json")[0] == 400
    assert _call(server, "/capture", {"url": "file:///etc/passwd"})[0] == 400
    assert _oversized(server) == 413
    assert _call(server, "/elsewhere")[0] == 404
    assert len(store.get_case("C1").evidence) == 1


def test_token_is_kept_and_rotated(tmp_path: Path) -> None:
    path = tmp_path / "recorder_token"
    first = load_token(path)
    assert len(first) >= 24 and load_token(path) == first
    assert load_token(path, rotate=True) != first
