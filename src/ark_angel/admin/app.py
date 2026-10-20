"""The admin panel's Flask app.

Protections, all enforced in one place (``_guard``) so a new route can't forget them:

- Every page but the sign-in page needs a live server-side session.
- Every POST needs a CSRF token (a session's token, or a double-submit token on
  the sign-in form) and, when the browser sends one, an Origin of this site.
- Requests must name an allowed Host, so a DNS-rebinding page can't talk to a
  panel listening on localhost.
- Strict security headers, no caching, no framing, and a CSP with no inline
  scripts on panel pages.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, redirect, render_template, request, url_for

from ark_angel.admin.db import ABSOLUTE_TIMEOUT_S, AdminDB, AuthError, Session, tokens_match
from ark_angel.cases.store import CaseNotFoundError, FileCaseStore

SESSION_COOKIE = "ark_admin_session"
LOGIN_CSRF_COOKIE = "ark_admin_login_csrf"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
_PUBLIC_ENDPOINTS = {"login", "static"}

_PANEL_CSP = ("default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
              "frame-ancestors 'none'; base-uri 'none'")


def _sha256_source(source: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(source.encode()).digest()).decode() + "'"


def _report_csp() -> str:
    """The case file has inline CSS, one inline print helper and an onclick: allow exactly those."""
    from ark_angel.report.html import _PRINT_SCRIPT

    script = _PRINT_SCRIPT.removeprefix("<script>").removesuffix("</script>")
    return (f"default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
            f"script-src {_sha256_source(script)} 'unsafe-hashes' {_sha256_source('window.print()')}; "
            "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")


def _host_only(host: str) -> str:
    """``example.org:8765`` -> ``example.org``; ``[::1]:8765`` -> ``::1``."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def safe_next(target: Optional[str]) -> str:
    """Only same-site paths: no scheme, no host, no ``//`` or backslash tricks."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    parts = urlsplit(target)
    return target if not parts.scheme and not parts.netloc else "/"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def create_app(store: FileCaseStore, db: AdminDB, allowed_hosts: Iterable[str] = LOCAL_HOSTS,
               secure_cookies: bool = False) -> Flask:
    """Build the panel. ``secure_cookies`` marks cookies HTTPS-only and turns on HSTS (use behind TLS)."""
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16 * 1024, TEMPLATES_AUTO_RELOAD=False)
    hosts = {_host_only(h) for h in allowed_hosts}
    report_csp = _report_csp()
    app.jinja_env.filters["when"] = _when

    def client_ip() -> str:
        return request.remote_addr or "?"

    def set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
        resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="Strict", secure=secure_cookies,
                        path="/")

    @app.before_request
    def _guard():
        if _host_only(request.host) not in hosts:
            abort(400)
        if request.method not in ("GET", "HEAD", "POST"):
            abort(405)
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != "null" and _host_only(urlsplit(origin).netloc) not in hosts:
                abort(400)
        g.session = db.session(request.cookies.get(SESSION_COOKIE, ""))
        if request.endpoint not in _PUBLIC_ENDPOINTS and g.session is None:
            if request.method == "POST":
                abort(403)
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        if request.method == "POST" and request.endpoint != "login":
            if not tokens_match(request.form.get("csrf_token", ""), g.session.csrf_token):
                abort(400)
        return None

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers.setdefault("Content-Security-Policy", _PANEL_CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        resp.headers["Cache-Control"] = "no-store"
        if secure_cookies:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    @app.context_processor
    def _inject():
        session: Optional[Session] = g.get("session")
        return {"me": session.username if session else None, "csrf_token": session.csrf_token if session else ""}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        target = safe_next(request.values.get("next"))
        if g.session is not None:
            return redirect(target)
        error = ""
        status = 200
        if request.method == "POST":
            if not tokens_match(request.form.get("csrf_token", ""), request.cookies.get(LOGIN_CSRF_COOKIE, "")):
                abort(400)
            try:
                session = db.authenticate(request.form.get("username", ""), request.form.get("password", ""),
                                          client_ip(), request.headers.get("User-Agent", ""))
            except AuthError as exc:
                error, status = str(exc), 401
            else:
                resp = redirect(target)
                set_cookie(resp, SESSION_COOKIE, session.token, ABSOLUTE_TIMEOUT_S)
                resp.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
                return resp
        login_csrf = secrets.token_urlsafe(32)
        resp = app.make_response((render_template("login.html", error=error, next=target, login_csrf=login_csrf),
                                  status))
        set_cookie(resp, LOGIN_CSRF_COOKIE, login_csrf, 3600)
        return resp

    @app.post("/logout")
    def logout():
        db.end_session(request.cookies.get(SESSION_COOKIE, ""), client_ip(), request.headers.get("User-Agent", ""))
        resp = redirect(url_for("login"))
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    def load_case(case_id: str):
        try:
            return store.get_case(case_id)
        except (CaseNotFoundError, ValueError):
            abort(404)

    @app.get("/")
    def dashboard():
        from ark_angel.review import ranked_leads

        rows = []
        for case_id in store.list_cases():
            try:
                case = store.get_case(case_id)
            except (ValueError, KeyError, TypeError, OSError):
                continue  # unreadable or hand-edited file; don't take the panel down with it
            ranked = ranked_leads(case)
            open_leads = [item for item in ranked if item.state.status == "open"]
            rows.append({
                "id": case.identifier, "title": case.title, "evidence": len(case.evidence),
                "open": len(open_leads), "verified": sum(1 for i in ranked if i.state.status == "verified"),
                "dismissed": sum(1 for i in ranked if i.state.status == "dismissed"),
                "top": open_leads[0].lead.summary if open_leads else "",
                "created": case.created_at.strftime("%Y-%m-%d"),
            })
        return render_template("dashboard.html", cases=rows)

    @app.get("/cases/<case_id>")
    def case_view(case_id: str):
        from ark_angel.report import build_report

        data = build_report(load_case(case_id))
        return render_template("case.html", case=data["case"], leads=data["leads"][:15],
                               clusters=len(data["clusters"]), timeline=data["timeline"][-8:][::-1])

    @app.get("/cases/<case_id>/report")
    def case_report(case_id: str):
        from ark_angel.report import build_report
        from ark_angel.report.html import render_html

        resp = Response(render_html(build_report(load_case(case_id))), mimetype="text/html")
        resp.headers["Content-Security-Policy"] = report_csp
        return resp

    @app.get("/cases/<case_id>/map.<fmt>")
    def case_map(case_id: str, fmt: str):
        from ark_angel.report import build_report
        from ark_angel.report.gis import render_geojson, render_kml

        if fmt not in ("kml", "geojson"):
            abort(404)
        case = load_case(case_id)
        data = build_report(case)
        body = render_kml(data) if fmt == "kml" else render_geojson(data)
        mimetype = "application/vnd.google-earth.kml+xml" if fmt == "kml" else "application/geo+json"
        resp = Response(body, mimetype=mimetype)
        resp.headers["Content-Disposition"] = f'attachment; filename="{case.identifier}.{fmt}"'
        return resp

    @app.get("/security")
    def security():
        return render_template("security.html", sessions=db.sessions_for(g.session.user_id),
                               current=g.session.token_hash, events=db.recent_events())

    @app.post("/security/sign-out-others")
    def sign_out_others():
        db.end_other_sessions(g.session)
        return redirect(url_for("security"))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    def _error(exc):
        code = getattr(exc, "code", 500)
        return render_template("error.html", code=code, name=getattr(exc, "name", "Error")), code

    @app.errorhandler(500)
    def _crash(exc):
        return render_template("error.html", code=500, name="Something went wrong"), 500

    return app
