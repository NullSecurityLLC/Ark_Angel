"""Recording browsing into a website case: tokens, the API's guard rails, and the Trail page."""
from __future__ import annotations

import json
import re

import pytest

from ark_angel.models import Evidence

pytest.importorskip("flask")

from web_helpers import new_case, post, site  # noqa: E402,F401  (site is a fixture)

TIP = Evidence("tips:1", "text", "Riley texted from 303-555-0147", {"source": "tips"})
AD = {"url": "https://classifieds.example/ad/1", "title": "Room for rent", "when": "2026-11-02T14:02:00",
      "text": "Quiet room. Text Sam at (303) 555-0147.", "session": "s1"}
EXT = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}


def _make_token(client, case_id: str, label: str = "ownerbox", days: str = "30") -> str:
    page = f"/app/cases/{case_id}/recorder"
    resp = post(client, page, {"action": "create", "label": label, "days": days}, form_page=page)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return re.search(r"(arkrec_[A-Za-z0-9_-]+)", resp.get_data(as_text=True)).group(1)


def _api(site, path: str, token: str = "", body=None, headers: dict | None = None):
    client = site.client()
    hdrs = {**EXT, **(headers or {})}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if body is None:
        return client.get(f"/api/recorder{path}", headers=hdrs)
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.post(f"/api/recorder{path}", data=data, content_type="application/json", headers=hdrs)


@pytest.fixture
def case(site):
    owner = site.member("owner")
    case_id = new_case(owner)
    site.store.add_evidence(case_id, [TIP])
    return owner, case_id


def test_member_records_into_a_case(site, case) -> None:
    owner, case_id = case
    token = _make_token(owner, case_id)
    # Shown once: the database keeps only a hash, and the page never shows it again.
    assert token not in site.db.path.read_bytes().decode("latin-1")
    assert token not in owner.get(f"/app/cases/{case_id}/recorder").get_data(as_text=True)

    status = _api(site, "/status", token)
    assert status.status_code == 200
    assert status.get_json() == {"case": case_id, "title": "Riley Tamsin", "user": "owner", "captured": 0}
    assert status.headers["Access-Control-Allow-Origin"] == EXT["Origin"]

    resp = _api(site, "/capture", token, AD)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["hits"] == [{"kind": "phone", "value": "3035550147", "seen_in": ["tips (tips:1)"]}]
    stored = site.store.get_case(case_id)
    assert stored.evidence[-1].metadata["recorded_by"] == "owner"
    # A matching page re-runs the analysis, and only what the page said counts.
    lead = {lead.identifier: lead for lead in stored.leads}["corr-phone-3035550147"]
    assert lead.evidence_ids == ["tips:1", body["stored"]]
    search = _api(site, "/capture", token, {"url": "https://www.google.com/search?q=303-555-0147"})
    assert search.get_json()["kind"] == "search" and search.get_json()["hits"] == []
    assert _api(site, "/status", token).get_json()["captured"] == 2


def test_api_ignores_the_session_cookie(site, case) -> None:
    owner, case_id = case
    # Signed in, but no token: a page can't ride the member's cookie into the API.
    resp = owner.post("/api/recorder/capture", data=json.dumps(AD), content_type="application/json")
    assert resp.status_code == 401
    assert resp.is_json
    assert len(site.store.get_case(case_id).evidence) == 1


def test_api_refuses_web_pages_and_junk(site, case, monkeypatch) -> None:
    owner, case_id = case
    token = _make_token(owner, case_id)
    assert _api(site, "/capture", token, AD, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _api(site, "/status", "arkrec_" + "x" * 43).status_code == 401
    assert _api(site, "/status", "not-even-a-token").status_code == 401
    assert _api(site, "/capture", token, b"{not json").status_code == 400
    assert _api(site, "/capture", token, {"url": "javascript:alert(1)"}).status_code == 400
    assert _api(site, "/capture", token, {"url": "https://a.example", "text": "x" * (2 * 1024 * 1024)}).status_code == 413
    missing = _api(site, "/nope", token)
    assert missing.status_code == 404 and missing.is_json
    # A preflight from the extension is answered; one from a web page isn't.
    client = site.client()
    assert client.open("/api/recorder/capture", method="OPTIONS", headers=EXT).status_code == 204
    assert client.open("/api/recorder/capture", method="OPTIONS",
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert len(site.store.get_case(case_id).evidence) == 1

    import ark_angel.web.recorder as recorder

    monkeypatch.setattr(recorder, "CAPTURES_PER_HOUR", 2)
    fresh = _make_token(owner, case_id, "fresh")  # the junk above counted against the first token's quota
    codes = [_api(site, "/capture", fresh, {"url": f"https://a.example/{n}"}).status_code for n in range(3)]
    assert codes == [200, 200, 429]


def test_access_is_rechecked_on_every_capture(site, case) -> None:
    owner, case_id = case
    editor = site.member("eddie")
    site.db.set_member(case_id, "eddie", "editor")
    token = _make_token(editor, case_id)
    assert _api(site, "/status", token).status_code == 200

    site.db.set_member(case_id, "eddie", "viewer")  # demoted
    assert _api(site, "/capture", token, AD).status_code == 401
    site.db.set_member(case_id, "eddie", "editor")
    assert _api(site, "/status", token).status_code == 200
    site.db.remove_member(case_id, "eddie")  # removed from the case
    assert _api(site, "/status", token).status_code == 401
    site.db.set_member(case_id, "eddie", "editor")
    site.db.set_status("eddie", "disabled")  # account disabled
    assert _api(site, "/status", token).status_code == 401
    site.db.set_status("eddie", "active")
    assert _api(site, "/status", token).status_code == 200
    site.clock.now += 31 * 86400  # expired
    assert _api(site, "/status", token).status_code == 401


def test_tokens_are_scoped_revocable_and_capped(site, case) -> None:
    owner, case_id = case
    editor = site.member("eddie")
    site.db.set_member(case_id, "eddie", "editor")
    viewer = site.member("vee")
    site.db.set_member(case_id, "vee", "viewer")
    assert viewer.get(f"/app/cases/{case_id}/recorder").status_code == 403

    mine, theirs = _make_token(owner, case_id), _make_token(editor, case_id, "eddie's")
    page = f"/app/cases/{case_id}/recorder"
    # The editor sees only their own token and can't revoke the owner's.
    editor_page = editor.get(page).get_data(as_text=True)
    assert "eddie&#39;s" in editor_page and "ownerbox" not in editor_page
    owner_token_id = site.db.recorder_tokens(case_id, site.db.user("owner")["id"])[0]["id"]
    assert post(editor, page, {"action": "revoke", "token": owner_token_id}, form_page=page).status_code == 404
    assert _api(site, "/status", mine).status_code == 200
    # The owner sees everyone's, and can cut off the editor's.
    assert "eddie" in owner.get(page).get_data(as_text=True)
    editor_token_id = site.db.recorder_tokens(case_id, site.db.user("eddie")["id"])[0]["id"]
    resp = post(owner, page, {"action": "revoke", "token": editor_token_id}, form_page=page)
    assert resp.status_code == 302
    assert _api(site, "/status", theirs).status_code == 401

    # A token is for one case only.
    other = new_case(owner, "Another case")
    assert _api(site, "/status", mine).get_json()["case"] == case_id
    assert other != case_id

    for n in range(4):
        _make_token(owner, case_id, f"extra {n}")
    resp = post(owner, page, {"action": "create", "label": "one too many", "days": "30"}, form_page=page)
    assert resp.status_code == 400 and "revoke one first" in resp.get_data(as_text=True)
    resp = post(owner, page, {"action": "create", "days": "3650"}, form_page=page)
    assert resp.status_code == 400
    # The page form needs its CSRF token like every other form.
    assert owner.post(page, data={"action": "create", "days": "30"}).status_code == 400


def test_deleting_the_case_kills_its_tokens(site, case) -> None:
    owner, case_id = case
    token = _make_token(owner, case_id)
    post(owner, f"/app/cases/{case_id}/delete", {"confirm": case_id}, form_page=f"/app/cases/{case_id}")
    assert _api(site, "/status", token).status_code == 401


def test_trail_page(site, case) -> None:
    owner, case_id = case
    editor = site.member("eddie")
    site.db.set_member(case_id, "eddie", "editor")
    _api(site, "/capture", _make_token(owner, case_id), {"url": "https://www.google.com/search?q=303-555-0147",
                                                        "when": "2026-11-02T14:01:00"})
    _api(site, "/capture", _make_token(editor, case_id), AD)

    page = owner.get(f"/app/cases/{case_id}/trail").get_data(as_text=True)
    assert "Trail" in page and "Recorder" in page
    assert "search: <b>“303-555-0147”</b>" in page
    assert "pivot: phone 3035550147, which you&#39;d seen in tips (tips:1)" in page
    assert "MATCH: phone 3035550147 is also in tips:1" in page
    assert 'href="https://classifieds.example/ad/1" rel="noopener noreferrer nofollow"' in page

    only_eddie = owner.get(f"/app/cases/{case_id}/trail?by=eddie").get_data(as_text=True)
    assert "Room for rent" in only_eddie and "303-555-0147”" not in only_eddie
    assert "Room for rent" in owner.get(f"/app/cases/{case_id}/trail?by=<script>").get_data(as_text=True)

    viewer = site.member("vee")
    site.db.set_member(case_id, "vee", "viewer")
    viewer_page = viewer.get(f"/app/cases/{case_id}/trail").get_data(as_text=True)
    assert "Room for rent" in viewer_page and ">Recorder<" not in viewer_page
