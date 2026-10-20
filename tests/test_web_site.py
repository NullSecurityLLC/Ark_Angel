import io
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("flask")

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402
from web_helpers import PASSWORD, login, new_case, photo, post, publish, site, token  # noqa: E402,F401

from ark_angel.cli import cli  # noqa: E402
from ark_angel.web.db import SiteDB  # noqa: E402
from ark_angel.web.public import TIP_SOURCE  # noqa: E402
from ark_angel.web.site import MAX_IMAGE_PIXELS, UploadError, read_image  # noqa: E402


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (width, height)).save(buffer, "PNG")
    return buffer.getvalue()


def _tip(client, slug: str, what: str = "I saw her at the station with a red backpack", **extra):
    files = extra.pop("files", None)
    return post(client, f"/missing/{slug}/tip", {"what": what, **extra}, form_page=f"/missing/{slug}", files=files)


# Sign-up and approval


def test_signup_is_pending_until_an_admin_approves(site) -> None:
    client = site.client()
    data = {"username": "sister", "password": PASSWORD, "password2": PASSWORD,
            "note": "I'm Riley's sister; Denver PD report 26-1234"}
    resp = post(client, "/signup", data, form_page="/signup")
    assert resp.status_code == 200 and "Thanks, we'll take a look" in resp.get_data(as_text=True)
    assert site.db.user("sister")["status"] == "pending"
    assert login(client, "sister").status_code == 401

    admin = site.admin()
    page = admin.get("/admin/users").get_data(as_text=True)
    assert "Riley&#39;s sister" in page
    post(admin, "/admin/users/sister", {"action": "approve"}, form_page="/admin/users")
    assert login(client, "sister").status_code == 302


def test_signup_validation_honeypot_and_rate_limit(site) -> None:
    client = site.client()
    short = post(client, "/signup", {"username": "aaa", "password": PASSWORD, "password2": PASSWORD, "note": "hi"},
                 form_page="/signup")
    assert short.status_code == 400 and "who you are" in short.get_data(as_text=True)
    mismatch = post(client, "/signup", {"username": "aaa", "password": PASSWORD, "password2": "other password!",
                                        "note": "x" * 30}, form_page="/signup")
    assert "don&#39;t match" in mismatch.get_data(as_text=True)
    taken = post(client, "/signup", {"username": "boss", "password": PASSWORD, "password2": PASSWORD,
                                     "note": "x" * 30}, form_page="/signup")
    assert "That username is taken." in taken.get_data(as_text=True)

    bot = post(client, "/signup", {"username": "botbot", "password": PASSWORD, "password2": PASSWORD,
                                   "note": "x" * 30, "website": "http://spam.example"}, form_page="/signup")
    assert bot.status_code == 200 and site.db.user("botbot") is None  # looks accepted, nothing saved

    for n in range(3):
        post(client, "/signup", {"username": f"user{n}", "password": PASSWORD, "password2": PASSWORD,
                                 "note": "x" * 30}, form_page="/signup")
    over = post(client, "/signup", {"username": "user9", "password": PASSWORD, "password2": PASSWORD,
                                    "note": "x" * 30}, form_page="/signup")
    assert over.status_code == 429 and site.db.user("user9") is None


def test_admin_console_is_hidden_from_members(site) -> None:
    member = site.member("sam")
    for path in ("/admin/", "/admin/users", "/admin/publications", "/admin/cases", "/admin/activity"):
        assert member.get(path).status_code == 404, path
    assert post(member, "/admin/users/sam", {"action": "make_admin"}).status_code == 404
    assert site.db.user("sam")["role"] == "member"
    admin = site.admin()
    for path in ("/admin/", "/admin/users", "/admin/publications", "/admin/cases", "/admin/activity"):
        assert admin.get(path).status_code == 200, path


def test_admin_cannot_lock_themself_out(site) -> None:
    admin = site.admin()
    for action in ("disable", "make_member"):
        resp = post(admin, "/admin/users/boss", {"action": action}, form_page="/admin/users")
        assert resp.status_code == 400
    assert site.db.user("boss")["status"] == "active" and site.db.user("boss")["role"] == "admin"


def test_admin_user_actions(site) -> None:
    sam = site.member("sam")
    site.db.create_user("pending1", PASSWORD)
    admin = site.admin()
    post(admin, "/admin/users/pending1", {"action": "reject"}, form_page="/admin/users")
    assert site.db.user("pending1") is None
    post(admin, "/admin/users/sam", {"action": "disable"}, form_page="/admin/users")
    assert sam.get("/app/").status_code == 302  # signed out
    post(admin, "/admin/users/sam", {"action": "enable"}, form_page="/admin/users")
    post(admin, "/admin/users/sam", {"action": "make_admin"}, form_page="/admin/users")
    assert site.db.user("sam")["role"] == "admin"


# Case access


def test_case_roles_are_enforced_on_every_route(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    viewer, editor, stranger = site.member("viewer"), site.member("editor"), site.member("stranger")
    site.db.set_member(case_id, "viewer", "viewer")
    site.db.set_member(case_id, "editor", "editor")

    reads = ["", "/tips", "/report", "/map.kml", "/map.geojson"]
    for path in reads:
        assert stranger.get(f"/app/cases/{case_id}{path}").status_code == 404, path
        assert viewer.get(f"/app/cases/{case_id}{path}").status_code == 200, path
    for path in ("/upload", "/profile", "/members"):
        assert stranger.get(f"/app/cases/{case_id}{path}").status_code == 404
        assert viewer.get(f"/app/cases/{case_id}{path}").status_code == 403
    assert editor.get(f"/app/cases/{case_id}/upload").status_code == 200
    assert editor.get(f"/app/cases/{case_id}/profile").status_code == 403
    assert editor.get(f"/app/cases/{case_id}/members").status_code == 403
    for client in (viewer, stranger):
        assert post(client, f"/app/cases/{case_id}/enrich", {}).status_code in (403, 404)
        assert post(client, f"/app/cases/{case_id}/delete", {"confirm": case_id}).status_code in (403, 404)
    assert post(editor, f"/app/cases/{case_id}/delete", {"confirm": case_id}).status_code == 403
    assert site.store.get_case(case_id)

    assert stranger.get("/app/cases/cdoesnotexist").status_code == 404
    assert stranger.get("/app/cases/..%2fweb.db").status_code == 404
    admin = site.admin()
    page = admin.get(f"/app/cases/{case_id}").get_data(as_text=True)
    assert "site admin access" in page


def test_sharing_rules(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    site.db.create_user("pending1", PASSWORD)
    page = f"/app/cases/{case_id}/members"
    resp = post(owner, page, {"username": "pending1", "role": "editor"}, form_page=page)
    assert resp.status_code == 400 and "No approved user" in resp.get_data(as_text=True)
    resp = post(owner, page, {"username": "owner", "role": "viewer"}, form_page=page)
    assert "owns this case" in resp.get_data(as_text=True)
    assert post(owner, page, {"username": "boss", "role": "admin"}, form_page=page).status_code == 400
    site.member("sam")
    post(owner, page, {"username": "sam", "role": "viewer"}, form_page=page)
    assert site.db.case_role(case_id, site.db.user("sam")["id"]) == "viewer"
    post(owner, page, {"username": "sam", "action": "remove"}, form_page=page)
    assert site.db.case_role(case_id, site.db.user("sam")["id"]) is None
    with pytest.raises(ValueError, match="still owns"):
        site.db.delete_user("owner")


def test_delete_case_needs_the_typed_id_and_removes_files(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    publish(site, owner, case_id, image=photo(tmp_path / "p.jpg"))
    uploads = site.base / "uploads" / case_id
    _tip(site.client(), site.db.case(case_id)["slug"], files={"photo": (photo(tmp_path / "t.jpg"), "t.jpg")})
    assert uploads.exists() and any((site.base / "media").iterdir())
    resp = post(owner, f"/app/cases/{case_id}/delete", {"confirm": "nope"}, form_page=f"/app/cases/{case_id}")
    assert resp.status_code == 400 and site.store.get_case(case_id)
    post(owner, f"/app/cases/{case_id}/delete", {"confirm": case_id}, form_page=f"/app/cases/{case_id}")
    assert site.db.case(case_id) is None and case_id not in site.store.list_cases()
    assert not uploads.exists() and not any((site.base / "media").iterdir())


# Public profiles


def test_profile_goes_public_only_after_approval(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    page = f"/app/cases/{case_id}/profile"
    missing = post(owner, page, {"action": "request", "name": "Riley Tamsin"}, form_page=page)
    assert missing.status_code == 400 and "police report" in missing.get_data(as_text=True)
    assert site.db.case(case_id)["draft_state"] == "draft"

    post(owner, page, {"action": "request", "name": "Riley Tamsin", "police_report": "PD 1", "attest": "yes"},
         form_page=page)
    anon = site.client()
    assert "Riley Tamsin" not in anon.get("/").get_data(as_text=True)
    assert "Riley Tamsin" in site.admin().get("/admin/publications").get_data(as_text=True)

    admin = site.admin()
    post(admin, f"/admin/publications/{case_id}", {"decision": "approve"}, form_page="/admin/publications")
    slug = site.db.case(case_id)["slug"]
    assert slug.startswith("riley-tamsin-")
    assert "Riley Tamsin" in anon.get("/").get_data(as_text=True)
    profile = anon.get(f"/missing/{slug}").get_data(as_text=True)
    assert "Riley Tamsin" in profile and "PD 1" not in profile  # the police reference is for the reviewer only


def test_edits_to_a_live_profile_wait_for_review(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    page = f"/app/cases/{case_id}/profile"
    post(owner, page, {"action": "request", "name": "Someone Else Entirely", "police_report": "PD 1",
                       "attest": "yes"}, form_page=page)
    public = site.client().get(f"/missing/{slug}").get_data(as_text=True)
    assert "Riley Tamsin" in public and "Someone Else Entirely" not in public

    admin = site.admin()
    reject = post(admin, f"/admin/publications/{case_id}", {"decision": "reject"}, form_page="/admin/publications")
    assert reject.status_code == 400  # needs a note
    post(admin, f"/admin/publications/{case_id}", {"decision": "reject", "note": "Name doesn't match the report"},
         form_page="/admin/publications")
    assert "Name doesn&#39;t match the report" in owner.get(page).get_data(as_text=True)
    assert "Riley Tamsin" in site.client().get(f"/missing/{slug}").get_data(as_text=True)


def test_unpublish_is_immediate(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    post(owner, f"/app/cases/{case_id}/profile", {"action": "unpublish"}, form_page=f"/app/cases/{case_id}/profile")
    assert site.client().get(f"/missing/{slug}").status_code == 404
    slug = publish(site, owner, case_id)
    post(site.admin(), f"/admin/cases/{case_id}/unpublish", {}, form_page="/admin/cases")
    assert site.client().get(f"/missing/{slug}").status_code == 404


def test_profile_photos_are_stripped_and_only_served_when_live(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    original = photo(tmp_path / "p.jpg")
    assert Image.open(io.BytesIO(original)).getexif()  # the upload has GPS EXIF
    page = f"/app/cases/{case_id}/profile"
    post(owner, page, {"action": "save", "name": "Riley"}, form_page=page, files={"photo": (original, "p.jpg")})
    draft = site.db.case(case_id)["draft_photo"]
    anon = site.client()
    assert anon.get(f"/media/{draft}").status_code == 404  # drafts never public
    assert owner.get(f"/app/cases/{case_id}/profile/photo").status_code == 200

    publish(site, owner, case_id)
    served = anon.get(f"/media/{draft}")
    assert served.status_code == 200 and served.mimetype == "image/jpeg"
    assert not dict(Image.open(io.BytesIO(served.data)).getexif())  # no GPS, no camera, nothing
    for bad in ("../web.db", "..%2fweb.db", "nope.jpg"):
        assert anon.get(f"/media/{bad}").status_code == 404


def test_profile_photo_rejects_non_images(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    page = f"/app/cases/{case_id}/profile"
    resp = post(owner, page, {"action": "save", "name": "Riley"}, form_page=page,
                files={"photo": (b"<html><script>alert(1)</script>", "evil.jpg")})
    assert resp.status_code == 400 and "readable photo" in resp.get_data(as_text=True)


def test_public_pages_escape_everything(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner, "<script>alert('title')</script>")
    slug = publish(site, owner, case_id, name="<script>alert(1)</script>")
    for path in ("/", f"/missing/{slug}"):
        page = site.client().get(path).get_data(as_text=True)
        assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<script>alert('title')</script>" not in owner.get(f"/app/cases/{case_id}").get_data(as_text=True)


# Tips


def test_tip_lands_in_the_case_with_contact_kept_private(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    anon = site.client()
    resp = _tip(anon, slug, "Saw her near Union Station, call 303-555-0147 <script>x</script>", when="Saturday 9pm",
                where="Union Station", contact_name="Pat", contact_phone="555-0199",
                files={"photo": (photo(tmp_path / "t.jpg"), "t.jpg")})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/thanks")

    case = site.store.get_case(case_id)
    tip = next(item for item in case.evidence if item.type == "text")
    image = next(item for item in case.evidence if item.type == "image")
    assert tip.identifier.startswith(f"{TIP_SOURCE}:tip-0001-")
    assert "555-0199" not in tip.content and tip.metadata["contact"] == {"name": "Pat", "phone": "555-0199"}
    assert image.metadata["source"] == TIP_SOURCE and image.metadata["tip"] == tip.identifier
    assert abs(image.metadata["latitude"] - 39.7527) < 1e-4  # the photo's GPS feeds the analysis
    assert "/" not in image.metadata["path"]  # no server paths in case data
    assert any(lead.identifier == "corr-phone-3035550147" or "3035550147" in lead.summary for lead in case.leads)

    inbox = owner.get(f"/app/cases/{case_id}/tips").get_data(as_text=True)
    assert "555-0199" in inbox and "<script>x</script>" not in inbox
    assert anon.get(f"/app/cases/{case_id}/tips").status_code == 302
    assert "555-0199" not in anon.get(f"/missing/{slug}").get_data(as_text=True)
    assert not any("ip" in key for key in tip.metadata)  # tipster addresses aren't stored


def test_tip_validation_honeypot_and_rate_limit(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    anon = site.client()
    short = _tip(anon, slug, "hi")
    assert short.status_code == 400 and "what you saw" in short.get_data(as_text=True)
    bad = _tip(anon, slug, files={"photo": (b"not an image at all", "x.jpg")})
    assert bad.status_code == 400 and "readable photo" in bad.get_data(as_text=True)
    assert _tip(anon, slug, website="spam").status_code == 302
    assert not site.store.get_case(case_id).evidence  # honeypot hit saved nothing

    # The bad photo counted (photos are decoded only after the rate limit, so junk can't be sent without limit).
    for _ in range(4):
        assert _tip(anon, slug).status_code == 302
    assert _tip(anon, slug).status_code == 429
    import sqlite3

    events = sqlite3.connect(site.db.path).execute("SELECT ip_hash FROM public_events").fetchall()
    assert events and all("127.0.0.1" not in row[0] for row in events)


def test_tips_only_reach_live_profiles(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    site.db.unpublish(case_id)
    anon = site.client()
    assert anon.get(f"/missing/{slug}").status_code == 404
    assert anon.post(f"/missing/{slug}/tip", data={"csrf_token": token(anon, "/login"), "what": "x" * 20}).status_code == 404


# Evidence upload and lead review


def test_uploads_feed_the_analysis(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    page = f"/app/cases/{case_id}/upload"
    post(owner, page, {"kind": "notes"}, form_page=page,
         files={"file": (b"Call 303-555-0147 about Riley\n", "volunteer notes.txt")})
    post(owner, page, {"kind": "csv"}, form_page=page,
         files={"file": (b"Tip,Received\nShe texted from 303.555.0147,2026-10-01 10:00\n", "hotline.csv")})
    resp = post(owner, page, {"kind": "photo"}, form_page=page, files={"file": (photo(tmp_path / "u.jpg"), "u.jpg")})
    assert resp.status_code == 302 and "uploaded" in resp.headers["Location"]
    case = site.store.get_case(case_id)
    sources = {(item.metadata or {}).get("source") for item in case.evidence}
    assert {"volunteer_notes", "hotline", "photos"} <= sources  # each file name becomes its source
    assert any(lead.identifier == "corr-phone-3035550147" for lead in case.leads)
    assert all("/" not in str((item.metadata or {}).get("path", "")) for item in case.evidence)

    bad = post(owner, page, {"kind": "photo"}, form_page=page, files={"file": (b"%PDF-1.4", "x.jpg")})
    assert bad.status_code == 400
    garbage = post(owner, page, {"kind": "chat"}, form_page=page, files={"file": (b"\x00\xff" * 50, "chat.txt")})
    assert garbage.status_code in (302, 400)  # either read as nothing useful or refused, never a crash
    assert post(owner, page, {"kind": "nonsense"}, form_page=page, files={"file": (b"x", "x.txt")}).status_code == 400


def test_evidence_files_are_members_only_and_sandboxed(site, tmp_path: Path) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    slug = publish(site, owner, case_id)
    _tip(site.client(), slug, files={"photo": (photo(tmp_path / "t.jpg"), "t.jpg")})
    name = next(item for item in site.store.get_case(case_id).evidence if item.type == "text").metadata["photo"]
    resp = owner.get(f"/app/cases/{case_id}/files/{name}")
    assert resp.status_code == 200 and "sandbox" in resp.headers["Content-Security-Policy"]
    assert site.member("stranger").get(f"/app/cases/{case_id}/files/{name}").status_code == 404
    for bad in ("../../web.db", "..%2f..%2fweb.db", "photo-zzzz.jpg", "notes.txt"):
        assert owner.get(f"/app/cases/{case_id}/files/{bad}").status_code == 404


def test_lead_review_on_the_site(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    page = f"/app/cases/{case_id}/upload"
    post(owner, page, {"kind": "notes"}, form_page=page, files={"file": (b"call 303-555-0147\n", "a.txt")})
    post(owner, page, {"kind": "notes"}, form_page=page, files={"file": (b"texted 303.555.0147\n", "b.txt")})
    lead = "corr-phone-3035550147"
    case_page = f"/app/cases/{case_id}"
    no_note = post(owner, f"{case_page}/review", {"lead": lead, "status": "dismissed"}, form_page=case_page)
    assert no_note.status_code == 400 and "Say why" in no_note.get_data(as_text=True)
    post(owner, f"{case_page}/review", {"lead": lead, "status": "verified", "note": "matches records"},
         form_page=case_page)
    review = site.store.get_case(case_id).reviews[-1]
    assert (review.status, review.reviewer, review.note) == ("verified", "owner", "matches records")
    assert post(owner, f"{case_page}/review", {"lead": "nope", "status": "verified"}, form_page=case_page).status_code == 404


# Images


def test_read_image_refuses_decompression_bombs_and_odd_formats() -> None:
    side = int(MAX_IMAGE_PIXELS ** 0.5) + 10
    with pytest.raises(UploadError, match="too large"):
        read_image(_png(side, side))
    gif = io.BytesIO()
    Image.new("L", (4, 4)).save(gif, "GIF")
    with pytest.raises(UploadError, match="JPEG, PNG or WebP"):
        read_image(gif.getvalue())
    image, ext = read_image(_png(8, 8))
    assert ext == ".png" and image.size == (8, 8)


def test_case_file_writes_leave_no_temp_files(site) -> None:
    owner = site.member("owner")
    case_id = new_case(owner)
    post(owner, f"/app/cases/{case_id}/enrich", {}, form_page=f"/app/cases/{case_id}")
    assert sorted(p.name for p in site.store.data_dir.iterdir()) == [f"{case_id}.json"]


# CLI


def test_cli_accounts_import_backup_and_serve_guards(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases"), "web"]

    result = runner.invoke(cli, [*base, "serve"])
    assert result.exit_code != 0 and "create an admin first" in result.output
    result = runner.invoke(cli, [*base, "create-admin", "graves"], input="short\nshort\n")
    assert result.exit_code != 0 and "at least 12" in result.output
    assert runner.invoke(cli, [*base, "create-admin", "graves"], input=f"{PASSWORD}\n{PASSWORD}\n").exit_code == 0

    db = SiteDB(tmp_path / "web.db")
    db.create_user("sam", PASSWORD, note="volunteer")
    assert "sam" in runner.invoke(cli, [*base, "users", "--pending"]).output
    assert runner.invoke(cli, [*base, "approve", "sam"]).exit_code == 0
    assert db.user("sam")["status"] == "active"

    assert runner.invoke(cli, ["--data-dir", str(tmp_path / "cases"), "case", "create", "C1", "CLI case"]).exit_code == 0
    assert runner.invoke(cli, [*base, "import-case", "C1", "--owner", "sam"]).exit_code == 0
    assert db.case_role("C1", db.user("sam")["id"]) == "owner"

    dest = tmp_path / "backup.tar.gz"
    assert runner.invoke(cli, [*base, "backup", str(dest)]).exit_code == 0
    with tarfile.open(dest) as tar:
        names = tar.getnames()
    assert "web.db" in names and "cases/C1.json" in names
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600

    calls = []
    monkeypatch.setattr("waitress.serve", lambda app, **kw: calls.append(kw))
    assert "plain HTTP" in runner.invoke(cli, [*base, "serve", "--host", "0.0.0.0"]).output
    assert "--allow-host" in runner.invoke(cli, [*base, "serve", "--host", "0.0.0.0", "--behind-proxy"]).output
    assert not calls
    assert runner.invoke(cli, [*base, "serve"]).exit_code == 0
    assert calls[-1]["host"] == "127.0.0.1" and "trusted_proxy" not in calls[-1]
    result = runner.invoke(cli, [*base, "serve", "--host", "0.0.0.0", "--behind-proxy", "--proxy-ip", "*"],
                           env={"ARK_ANGEL_ALLOW_HOST": "arkangel.example.org"})
    assert result.exit_code == 0 and calls[-1]["trusted_proxy"] == "*"


def test_cli_serve_waits_for_an_admin(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "cases"), "web"]
    calls, naps = [], []
    monkeypatch.setattr("waitress.serve", lambda app, **kw: calls.append(kw))

    def nap(seconds):  # an admin appears while it waits, as with `docker compose run ... create-admin`
        naps.append(seconds)
        SiteDB(tmp_path / "web.db").create_user("graves", PASSWORD, role="admin", status="active")

    monkeypatch.setattr("time.sleep", nap)
    result = runner.invoke(cli, [*base, "serve", "--wait-for-admin"])
    assert result.exit_code == 0 and "No admin yet" in result.output and naps and calls
