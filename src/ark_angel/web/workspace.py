"""Member workspace (``/app``): your cases, their evidence and leads, sharing, and the public profile.

Every case page goes through ``case_route``: you need a role on the case
(viewer < editor < owner) or to be a site admin, and someone with no role gets
a 404, so case ids can't be probed. Writes to a case happen under its lock.
"""
from __future__ import annotations

import re
import secrets
from functools import wraps
from pathlib import Path
from typing import Callable

from flask import Blueprint, Response, abort, current_app, g, redirect, render_template, request, \
    send_from_directory, url_for

from ark_angel.cases.store import CASE_ID_PATTERN, CaseNotFoundError
from ark_angel.models import Case
from ark_angel.web.app import SESSION_COOKIE, client_ip, report_csp, site, user_agent
from ark_angel.web.db import CASE_RANK, validate_password
from ark_angel.web.public import TIP_SOURCE
from ark_angel.web.site import UploadError, photo_kind, save_evidence_photo, save_profile_photo, save_upload

bp = Blueprint("workspace", __name__)

MAX_UPLOAD = 25 * 1024 * 1024
MAX_PROFILE_PHOTO = 10 * 1024 * 1024
_EVIDENCE_PHOTO = re.compile(r"^photo-[0-9a-f]{16}\.(jpg|png|webp)$")
PROFILE_FIELDS = {"name": 80, "age": 20, "last_seen_date": 60, "last_seen_place": 160, "description": 2000,
                  "contact": 240, "police_report": 160}
UPLOAD_KINDS = {
    "notes": "Notes or a text file (one item per line)",
    "csv": "Tip-line export (CSV/TSV)",
    "email": "Email (.eml or .mbox)",
    "chat": "WhatsApp or Telegram chat export",
    "photo": "Photo (GPS, time and device are read from it)",
}
# Fixed messages for ?notice=, so nothing from the URL is ever echoed onto the page.
NOTICES = {
    "created": "Case created. Add evidence to get started.",
    "enriched": "Analysis re-run.",
    "reviewed": "Lead updated.",
    "uploaded": "Evidence added and analysis re-run.",
    "no_exif": "That photo has no GPS, time or device data, so there's nothing to analyse in it.",
    "saved": "Draft saved. Nothing public has changed.",
    "requested": "Sent for review. An administrator checks every profile before it goes public.",
    "withdrawn": "Request withdrawn.",
    "unpublished": "The public profile is down.",
    "shared": "Sharing updated.",
    "signed_out_others": "Your other sessions were signed out.",
}


def notice() -> str:
    return NOTICES.get(request.args.get("notice", ""), "")


def case_route(min_role: str) -> Callable:
    """Load ``case_id`` from the URL and require at least ``min_role`` on it (admins count as owners)."""
    def decorate(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(case_id: str, *args, **kwargs):
            s = site()
            meta = s.db.case(case_id) if CASE_ID_PATTERN.match(case_id) else None
            if meta is None:
                abort(404)
            role = s.db.case_role(case_id, g.session.user_id)
            via_admin = role is None and g.session.is_admin
            if via_admin:
                role = "owner"
            if role is None:
                abort(404)
            if CASE_RANK[role] < CASE_RANK[min_role]:
                abort(403)
            try:
                case = s.store.get_case(case_id)
            except (CaseNotFoundError, ValueError):
                abort(404)
            g.case, g.meta, g.role, g.via_admin = case, meta, role, via_admin
            return view(case_id, *args, **kwargs)
        return wrapper
    return decorate


def _back(endpoint: str, case_id: str, key: str) -> Response:
    return redirect(url_for(endpoint, case_id=case_id, notice=key))


# Cases


@bp.get("/")
def cases():
    s = site()
    rows = []
    for meta in s.db.cases_for(g.session.user_id):
        try:
            case = s.store.get_case(meta["case_id"])
        except (CaseNotFoundError, ValueError, KeyError, TypeError, OSError):
            continue
        rows.append({"id": case.identifier, "title": case.title, "role": meta["role"],
                     "evidence": len(case.evidence), "live": meta["published"] is not None,
                     "requested": meta["draft_state"] == "requested"})
    return render_template("app/cases.html", cases=rows, error="")


@bp.post("/cases/new")
def new_case():
    s = site()
    title = " ".join(request.form.get("title", "").split())[:120]
    if len(title) < 3:
        return render_template("app/cases.html", cases=[], error="Give the case a title."), 400
    case_id = "c" + secrets.token_hex(5)
    s.store.create_case(Case(identifier=case_id, title=title))
    s.db.register_case(case_id, g.session.username)
    return _back("workspace.case", case_id, "created")


@bp.get("/cases/<case_id>")
@case_route("viewer")
def case(case_id: str):
    return _render_case(notice=notice())


def _render_case(notice: str = "", error: str = ""):
    from ark_angel.report import build_report

    data = build_report(g.case)
    leads = data["leads"]
    page = render_template("app/case.html", data=data, open_leads=[x for x in leads if x["status"] != "dismissed"],
                           dismissed=[x for x in leads if x["status"] == "dismissed"],
                           timeline=data["timeline"][-8:][::-1], notice=notice, error=error)
    return page, 400 if error else 200


@bp.post("/cases/<case_id>/review")
@case_route("editor")
def review(case_id: str):
    from ark_angel import pipeline

    status = request.form.get("status", "")
    note = request.form.get("note", "").strip()[:500]
    if status not in ("verified", "dismissed", "open"):
        abort(400)
    if status == "dismissed" and not note:
        return _render_case(error="Say why you're dismissing it (the note goes in the audit trail).")
    s = site()
    with s.case_lock(case_id):
        try:
            pipeline.review_lead(s.store, case_id, request.form.get("lead", ""), status, note, g.session.username)
        except ValueError:
            abort(404)
    return _back("workspace.case", case_id, "reviewed")


@bp.post("/cases/<case_id>/enrich")
@case_route("editor")
def enrich(case_id: str):
    from ark_angel import pipeline

    s = site()
    with s.case_lock(case_id):
        pipeline.enrich(s.store, case_id)
    return _back("workspace.case", case_id, "enriched")


@bp.get("/cases/<case_id>/tips")
@case_route("viewer")
def tips(case_id: str):
    items = [item for item in g.case.evidence if (item.metadata or {}).get("source") == TIP_SOURCE
             and item.type == "text"]
    items.sort(key=lambda item: (item.metadata or {}).get("received_at", ""), reverse=True)
    return render_template("app/tips.html", tips=items)


@bp.get("/cases/<case_id>/files/<name>")
@case_route("viewer")
def evidence_file(case_id: str, name: str):
    if not _EVIDENCE_PHOTO.match(name):
        abort(404)
    folder = site().uploads_dir(case_id)
    if not (folder / name).is_file():
        abort(404)
    resp = send_from_directory(folder, name, mimetype=photo_kind(folder / name))
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@bp.route("/cases/<case_id>/upload", methods=["GET", "POST"])
@case_route("editor")
def upload(case_id: str):
    if request.method == "GET":
        return render_template("app/upload.html", kinds=UPLOAD_KINDS, error="")
    kind = request.form.get("kind", "")
    file = request.files.get("file")
    if kind not in UPLOAD_KINDS or not file or not file.filename:
        return render_template("app/upload.html", kinds=UPLOAD_KINDS, error="Pick a file and what kind it is."), 400
    data = file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        abort(413)
    s = site()
    with s.case_lock(case_id):
        try:
            outcome = _ingest(case_id, kind, file.filename, data)
        except UploadError as exc:
            return render_template("app/upload.html", kinds=UPLOAD_KINDS, error=str(exc)), 400
        except Exception:  # parsers for six file formats; any failure means "couldn't read it"
            current_app.logger.exception("upload failed for case %s", case_id)
            return render_template("app/upload.html", kinds=UPLOAD_KINDS,
                                   error="Couldn't read that file as the kind you picked."), 400
    return _back("workspace.case", case_id, outcome)


def _ingest(case_id: str, kind: str, filename: str, data: bytes) -> str:
    from ark_angel import pipeline
    from ark_angel.ingest.csv_source import CsvTipSource

    s = site()
    if kind == "photo":
        evidence, _ = pipeline.ingest_image(s.store, case_id, save_evidence_photo(s, case_id, data))
        if evidence is None:
            return "no_exif"
        evidence.metadata["path"] = Path(evidence.metadata["path"]).name  # no server paths in reports
        s.store.add_evidence(case_id, [evidence])
    else:
        path = save_upload(s, case_id, filename, data)
        if kind == "notes":
            pipeline.ingest_text(s.store, case_id, path)
        elif kind == "csv":
            pipeline.ingest_csv(s.store, case_id, path, CsvTipSource())
        elif kind == "email":
            pipeline.ingest_email(s.store, case_id, path)
        else:
            pipeline.ingest_chat(s.store, case_id, path)
    pipeline.enrich(s.store, case_id)
    return "uploaded"


@bp.get("/cases/<case_id>/report")
@case_route("viewer")
def report(case_id: str):
    from ark_angel.report import build_report
    from ark_angel.report.html import render_html

    resp = Response(render_html(build_report(g.case)), mimetype="text/html")
    resp.headers["Content-Security-Policy"] = report_csp()
    return resp


@bp.get("/cases/<case_id>/map.<fmt>")
@case_route("viewer")
def case_map(case_id: str, fmt: str):
    from ark_angel.report import build_report
    from ark_angel.report.gis import render_geojson, render_kml

    if fmt not in ("kml", "geojson"):
        abort(404)
    data = build_report(g.case)
    body = render_kml(data) if fmt == "kml" else render_geojson(data)
    mimetype = "application/vnd.google-earth.kml+xml" if fmt == "kml" else "application/geo+json"
    resp = Response(body, mimetype=mimetype)
    resp.headers["Content-Disposition"] = f'attachment; filename="{case_id}.{fmt}"'
    return resp


# Public profile


def _profile_form() -> dict:
    form = {}
    for name, limit in PROFILE_FIELDS.items():
        value = request.form.get(name, "")
        form[name] = value.strip()[:limit] if name == "description" else " ".join(value.split())[:limit]
    form["attest"] = request.form.get("attest") == "yes"
    return form


@bp.route("/cases/<case_id>/profile", methods=["GET", "POST"])
@case_route("owner")
def profile(case_id: str):
    s = site()
    meta = g.meta
    if request.method == "GET":
        return render_template("app/profile.html", meta=meta, form=meta["draft"], errors=[], notice=notice())
    action = request.form.get("action", "save")
    if action == "unpublish":
        s.db.unpublish(case_id)
        return _back("workspace.profile", case_id, "unpublished")
    if action == "withdraw":
        s.db.withdraw_request(case_id)
        return _back("workspace.profile", case_id, "withdrawn")
    form = _profile_form()
    errors = []
    photo_name = None
    photo = request.files.get("photo")
    if photo and photo.filename:
        data = photo.read(MAX_PROFILE_PHOTO + 1)
        if len(data) > MAX_PROFILE_PHOTO:
            errors.append("Photos can be up to 10 MB.")
        else:
            try:
                photo_name = save_profile_photo(s, data)
            except UploadError as exc:
                errors.append(str(exc))
    if action == "request":
        if not form["name"]:
            errors.append("The profile needs the person's name.")
        if not form["police_report"]:
            errors.append("Add the police report (agency and report number). Profiles go public only for "
                          "people reported missing to police.")
        if not form["attest"]:
            errors.append("Confirm you are family or acting for them, and that the person is reported missing.")
    if errors:
        if photo_name:
            s.remove_photo(photo_name)
        return render_template("app/profile.html", meta=meta, form=form, errors=errors, notice=""), 400
    old_draft_photo = meta["draft_photo"]
    s.db.save_draft(case_id, form, photo_name)
    if photo_name and old_draft_photo and s.db.photo_in_use(old_draft_photo) is None:
        s.remove_photo(old_draft_photo)
    if action == "request":
        s.db.request_publication(case_id)
        return _back("workspace.profile", case_id, "requested")
    return _back("workspace.profile", case_id, "saved")


@bp.get("/cases/<case_id>/profile/photo")
@case_route("owner")
def draft_photo(case_id: str):
    name = g.meta["draft_photo"]
    if not name:
        abort(404)
    return send_from_directory(site().media_dir, name, mimetype="image/jpeg")


# Sharing and deletion


@bp.route("/cases/<case_id>/members", methods=["GET", "POST"])
@case_route("owner")
def members(case_id: str):
    s = site()
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "")
        if request.form.get("action") == "remove":
            s.db.remove_member(case_id, username)
            return _back("workspace.members", case_id, "shared")
        try:
            s.db.set_member(case_id, username, request.form.get("role", ""))
        except ValueError as exc:
            error = str(exc)[:1].upper() + str(exc)[1:] + "."
        else:
            return _back("workspace.members", case_id, "shared")
    return render_template("app/members.html", members=s.db.members(case_id), error=error, notice=notice()), \
        400 if error else 200


@bp.post("/cases/<case_id>/delete")
@case_route("owner")
def delete_case(case_id: str):
    if request.form.get("confirm", "").strip() != case_id:
        return _render_case(error=f"To delete this case, type its id ({case_id}) in the box.")
    s = site()
    meta = g.meta
    with s.case_lock(case_id):
        s.store.delete_case(case_id)
        s.db.forget_case(case_id)
        s.remove_case_files(case_id, (meta["draft_photo"], meta["published_photo"]))
    return redirect(url_for("workspace.cases"))


# Account


@bp.get("/account")
def account():
    s = site()
    return render_template("app/account.html", sessions=s.db.sessions_for(g.session.user_id),
                           current=g.session.token_hash, events=s.db.recent_events(20, g.session.username),
                           error="", notice=notice())


@bp.post("/account/sign-out-others")
def sign_out_others():
    site().db.end_other_sessions(g.session)
    return redirect(url_for("workspace.account", notice="signed_out_others"))


@bp.post("/account/password")
def change_password():
    s = site()
    new = request.form.get("password", "")
    error = ""
    if not s.db.check_password(g.session.user_id, request.form.get("current", "")):
        error = "Your current password is wrong."
    elif new != request.form.get("password2", ""):
        error = "The two new passwords don't match."
    else:
        try:
            validate_password(new)
        except ValueError as exc:
            error = str(exc)[:1].upper() + str(exc)[1:] + "."
    if error:
        return render_template("app/account.html", sessions=s.db.sessions_for(g.session.user_id),
                               current=g.session.token_hash, events=s.db.recent_events(20, g.session.username),
                               error=error, notice=""), 400
    s.db.set_password(g.session.username, new)
    resp = redirect(url_for("public.login", notice="reauth"))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@bp.post("/logout")
def logout():
    site().db.end_session(request.cookies.get(SESSION_COOKIE, ""), client_ip(), user_agent())
    resp = redirect(url_for("public.home"))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp

