"""Public pages: approved missing-person profiles, the tip form, sign-up requests and sign-in.

Visitors only ever see a profile's approved copy. A tip goes into that case's
evidence as one item (plus one for an attached photo, whose EXIF GPS and time
are kept for analysis) and the case is re-analysed. Tippers' contact details
are stored with the tip for the case team only; their address is not stored.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, send_from_directory, url_for

from ark_angel.models import Evidence
from ark_angel.web.app import client_ip, safe_next, site, user_agent
from ark_angel.web.db import AuthError
from ark_angel.web.site import UploadError, save_evidence_photo

bp = Blueprint("public", __name__)

TIP_LIMIT = (5, 3600)  # tips per address per hour
SIGNUP_LIMIT = (3, 3600)
MAX_PENDING_SIGNUPS = 200
MAX_TIP_PHOTO = 10 * 1024 * 1024
TIP_SOURCE = "web_tips"
LOGIN_NOTICES = {"reauth": "Password changed. Sign in with the new one."}
_TIP_FIELDS = {"what": 5000, "when": 120, "where": 200, "contact_name": 120, "contact_phone": 60,
               "contact_email": 200}


def _field(name: str, limit: int) -> str:
    """A trimmed form value; one-line fields also get their whitespace collapsed."""
    value = request.form.get(name, "")
    if name != "what":
        value = " ".join(value.split())
    return value.strip()[:limit]


@bp.get("/")
def home():
    profiles = site().db.published_profiles()
    return render_template("public/home.html", profiles=profiles)


@bp.get("/privacy")
def privacy():
    return render_template("public/privacy.html")


@bp.get("/media/<name>")
def media(name: str):
    """Public profile photos, only while the profile they belong to is live (drafts are served in /app)."""
    if site().db.photo_in_use(name) != "published":
        abort(404)
    resp = send_from_directory(site().media_dir, name, mimetype="image/jpeg", max_age=3600)
    resp.headers["Content-Disposition"] = "inline"
    return resp


def _profile_or_404(slug: str) -> dict:
    profile = site().db.profile_by_slug(slug)
    if profile is None:
        abort(404)
    return profile


@bp.get("/missing/<slug>")
def profile(slug: str):
    return render_template("public/profile.html", case=_profile_or_404(slug), errors=[], form={})


@bp.post("/missing/<slug>/tip")
def submit_tip(slug: str):
    case = _profile_or_404(slug)
    form = {name: _field(name, limit) for name, limit in _TIP_FIELDS.items()}
    if request.form.get("website"):  # honeypot: people never see this field, bots fill it in
        return redirect(url_for("public.thanks", slug=slug))
    errors = []
    if len(form["what"]) < 10:
        errors.append("Tell us what you saw (at least a sentence).")
    photo = request.files.get("photo")
    data = b""
    if photo and photo.filename:
        data = photo.read(MAX_TIP_PHOTO + 1)
        if len(data) > MAX_TIP_PHOTO:
            errors.append("Photos can be up to 10 MB.")
    if errors:
        return render_template("public/profile.html", case=case, errors=errors, form=form), 400
    if not site().db.allow("tip", client_ip(), *TIP_LIMIT):
        abort(429)

    s = site()
    case_id = case["case_id"]
    with s.case_lock(case_id):
        photo_path = None
        if data:
            try:
                photo_path = save_evidence_photo(s, case_id, data)
            except UploadError as exc:
                return render_template("public/profile.html", case=case, errors=[str(exc)], form=form), 400
        _add_tip(case_id, form, photo_path)
    return redirect(url_for("public.thanks", slug=slug))


def _add_tip(case_id: str, form: dict, photo_path) -> None:
    from ark_angel import pipeline
    from ark_angel.ingest.image_source import ImageIngestionSource

    store = site().store
    existing = store.get_case(case_id)
    number = 1 + sum(1 for item in existing.evidence if item.identifier.startswith(f"{TIP_SOURCE}:"))
    tip_id = f"{TIP_SOURCE}:tip-{number:04d}-{secrets.token_hex(2)}"
    now = datetime.now(timezone.utc)
    lines = [f"Website tip: {form['what']}"]
    if form["when"]:
        lines.append(f"When seen: {form['when']}")
    if form["where"]:
        lines.append(f"Where: {form['where']}")
    contact = {k.removeprefix("contact_"): form[k] for k in ("contact_name", "contact_phone", "contact_email")
               if form[k]}
    metadata = {"source": TIP_SOURCE, "timestamp": now.strftime("%Y-%m-%d %H:%M"),
                "received_at": now.isoformat(timespec="seconds"), "contact": contact}
    items = [Evidence(tip_id, "text", "\n".join(lines), metadata)]
    leads = []
    if photo_path is not None:
        metadata["photo"] = photo_path.name
        photo_item, leads = ImageIngestionSource().ingest(str(photo_path))
        if photo_item is not None:
            # Same tipster, same source: a photo shouldn't count as independent corroboration of their own tip.
            photo_item.metadata.update(source=TIP_SOURCE, tip=tip_id, path=photo_path.name)
            items.append(photo_item)
    store.add_evidence(case_id, items)
    if leads:
        store.add_leads(case_id, leads)
    pipeline.enrich(store, case_id)


@bp.get("/missing/<slug>/thanks")
def thanks(slug: str):
    return render_template("public/thanks.html", case=_profile_or_404(slug))


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.session is not None:
        return redirect(url_for("workspace.cases"))
    form = {"username": request.form.get("username", "").strip()[:64], "note": request.form.get("note", "").strip()}
    errors = []
    if request.method == "POST":
        if request.form.get("website"):
            return render_template("public/signup_done.html")
        password = request.form.get("password", "")
        if len(form["note"]) < 20:
            errors.append("Tell the administrators who you are and which case you're working on (a sentence or two).")
        if password != request.form.get("password2", ""):
            errors.append("The two passwords don't match.")
        if not errors:
            db = site().db
            if len(db.users("pending")) >= MAX_PENDING_SIGNUPS or not db.allow("signup", client_ip(), *SIGNUP_LIMIT):
                abort(429)
            try:
                db.create_user(form["username"], password, note=form["note"][:500])
            except ValueError as exc:
                message = str(exc)
                errors.append("That username is taken." if "already exists" in message
                              else message[:1].upper() + message[1:] + ".")
            else:
                return render_template("public/signup_done.html")
    return render_template("public/signup.html", errors=errors, form=form), 400 if errors else 200


@bp.route("/login", methods=["GET", "POST"])
def login():
    target = safe_next(request.values.get("next"))
    if g.session is not None:
        return redirect(target)
    error, status = "", 200
    if request.method == "POST":
        try:
            session = site().db.authenticate(request.form.get("username", ""), request.form.get("password", ""),
                                             client_ip(), user_agent())
        except AuthError as exc:
            error, status = str(exc), 401
        else:
            return current_app.extensions["ark_start_session"](redirect(target), session.token)
    notice = LOGIN_NOTICES.get(request.args.get("notice", ""), "")
    return render_template("login.html", error=error, next=target, notice=notice), status
