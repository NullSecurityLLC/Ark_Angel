"""Admin console (``/admin``): approve sign-ups and public profiles, manage users, see every case and sign-in.

Only admins reach these pages; everyone else gets a 404 from the request guard.
Admins can't disable or demote themselves, so the site can't lose its last admin by accident.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, redirect, render_template, request, send_from_directory, url_for

from ark_angel.web.app import site

bp = Blueprint("admin", __name__)

USER_ACTIONS = {"approve": "active", "enable": "active", "disable": "disabled"}
NOTICES = {
    "user": "User updated.",
    "approved": "Profile is live.",
    "rejected": "Request sent back to the owner with your note.",
    "unpublished": "Profile taken down.",
}


def _notice() -> str:
    return NOTICES.get(request.args.get("notice", ""), "")


@bp.get("/")
def home():
    db = site().db
    return render_template("admin/home.html", pending=db.users("pending"), requests=db.publication_requests(),
                           users=len(db.users()), cases=len(db.all_cases()), live=len(db.published_profiles()))


@bp.get("/users")
def users():
    return render_template("admin/users.html", users=site().db.users(), notice=_notice(), error="")


@bp.post("/users/<username>")
def user_action(username: str):
    db = site().db
    action = request.form.get("action", "")
    user = db.user(username)
    if user is None:
        abort(404)
    if user["id"] == g.session.user_id and action in ("disable", "reject", "make_member"):
        return render_template("admin/users.html", users=db.users(), notice="",
                               error="You can't disable, reject or demote your own account."), 400
    if action in USER_ACTIONS:
        db.set_status(username, USER_ACTIONS[action])
    elif action == "reject" and user["status"] == "pending":
        db.delete_user(username)
    elif action in ("make_admin", "make_member"):
        db.set_role(username, action.removeprefix("make_"))
    else:
        abort(400)
    return redirect(url_for("admin.users", notice="user"))


@bp.get("/publications")
def publications():
    return render_template("admin/publications.html", requests=site().db.publication_requests(), notice=_notice(),
                           error="")


@bp.get("/publications/<case_id>/photo")
def publication_photo(case_id: str):
    meta = site().db.case(case_id)
    if meta is None or not meta["draft_photo"]:
        abort(404)
    return send_from_directory(site().media_dir, meta["draft_photo"], mimetype="image/jpeg")


@bp.post("/publications/<case_id>")
def decide_publication(case_id: str):
    db = site().db
    decision = request.form.get("decision")
    try:
        if decision == "approve":
            old = (db.case(case_id) or {}).get("published_photo", "")
            db.approve_publication(case_id)
            if old and db.photo_in_use(old) is None:
                site().remove_photo(old)
        elif decision == "reject":
            note = " ".join(request.form.get("note", "").split())[:500]
            if not note:
                return render_template("admin/publications.html", requests=db.publication_requests(), notice="",
                                       error="Tell the owner why (they see this note)."), 400
            db.reject_publication(case_id, note)
        else:
            abort(400)
    except ValueError:
        abort(404)
    return redirect(url_for("admin.publications", notice="approved" if decision == "approve" else "rejected"))


@bp.get("/cases")
def cases():
    return render_template("admin/cases.html", cases=site().db.all_cases(), notice=_notice())


@bp.post("/cases/<case_id>/unpublish")
def unpublish(case_id: str):
    if site().db.case(case_id) is None:
        abort(404)
    site().db.unpublish(case_id)
    return redirect(url_for("admin.cases", notice="unpublished"))


@bp.get("/activity")
def activity():
    return render_template("admin/activity.html", events=site().db.recent_events(200))
