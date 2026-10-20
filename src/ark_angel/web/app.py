"""The ArkAngel website: public missing-person profiles and tip forms, member case workspaces, an admin console.

Protections, all enforced in one place (``_guard``) so a new route can't forget them:

- ``/app`` pages need an approved account; ``/admin`` pages need an admin (others get a 404).
- Every POST needs a CSRF token: the session's token when signed in, otherwise a
  double-submit token from an HttpOnly cookie (tip, sign-up and sign-in forms).
- Cross-origin POSTs are refused, and requests must name an allowed Host, so a
  DNS-rebinding page can't reach a panel on localhost.
- Strict security headers on every response: a CSP with no scripts at all on
  site pages, no framing, no referrer, no caching of signed-in pages.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, redirect, render_template, request, url_for

from ark_angel.cases.store import FileCaseStore
from ark_angel.web.db import ABSOLUTE_TIMEOUT_S, SiteDB, tokens_match
from ark_angel.web.site import Site

SESSION_COOKIE = "ark_session"
FORM_COOKIE = "ark_form"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PRIVATE_BLUEPRINTS = ("workspace", "admin")

SITE_CSP = ("default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")


def _sha256_source(source: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(source.encode()).digest()).decode() + "'"


def report_csp() -> str:
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
        return "/app/"
    parts = urlsplit(target)
    return target if not parts.scheme and not parts.netloc else "/app/"


def _when(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def site() -> Site:
    from flask import current_app

    return current_app.extensions["ark_site"]


def client_ip() -> str:
    return request.remote_addr or "?"


def user_agent() -> str:
    return request.headers.get("User-Agent", "")


def create_app(store: FileCaseStore, db: SiteDB, base_dir: Optional[Path] = None,
               allowed_hosts: Iterable[str] = LOCAL_HOSTS, secure_cookies: bool = False,
               site_name: str = "ArkAngel") -> Flask:
    """Build the site. ``secure_cookies`` marks cookies HTTPS-only and turns on HSTS (use behind TLS)."""
    from ark_angel.web import admin, public, workspace

    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 1024 * 1024, TEMPLATES_AUTO_RELOAD=False,
                      SITE_NAME=site_name, SECURE_COOKIES=secure_cookies)
    app.extensions["ark_site"] = Site(store, db, Path(base_dir) if base_dir else store.data_dir.parent)
    hosts = {_host_only(h) for h in allowed_hosts}
    app.jinja_env.filters["when"] = _when

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
        if request.endpoint is None or request.endpoint == "static":
            return None  # Flask answers 404/405 itself; static files are public
        if request.blueprint in PRIVATE_BLUEPRINTS and g.session is None:
            if request.method == "POST":
                abort(403)
            return redirect(url_for("public.login", next=request.full_path.rstrip("?")))
        if request.blueprint == "admin" and not g.session.is_admin:
            abort(404)
        if request.method == "POST":
            expected = g.session.csrf_token if g.session else request.cookies.get(FORM_COOKIE, "")
            if not tokens_match(request.form.get("csrf_token", ""), expected):
                abort(400)
        return None

    def form_token() -> str:
        if g.get("session") is not None:
            return g.session.csrf_token
        if "form_token" not in g:
            current = request.cookies.get(FORM_COOKIE, "")
            g.form_token = current if 20 <= len(current) <= 64 else secrets.token_urlsafe(32)
            g.set_form_cookie = g.form_token != current
        return g.form_token

    @app.after_request
    def _headers(resp: Response) -> Response:
        if g.get("set_form_cookie"):
            resp.set_cookie(FORM_COOKIE, g.form_token, max_age=12 * 3600, httponly=True, samesite="Strict",
                            secure=secure_cookies, path="/")
        resp.headers.setdefault("Content-Security-Policy", SITE_CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        if g.get("session") is not None or request.blueprint in PRIVATE_BLUEPRINTS:
            resp.headers["Cache-Control"] = "no-store"
        else:
            resp.headers.setdefault("Cache-Control", "no-cache")
        if secure_cookies:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    @app.context_processor
    def _inject():
        session = g.get("session")
        return {"me": session, "csrf_token": form_token, "site_name": site_name}

    def start_session(resp: Response, token: str) -> Response:
        resp.set_cookie(SESSION_COOKIE, token, max_age=ABSOLUTE_TIMEOUT_S, httponly=True, samesite="Strict",
                        secure=secure_cookies, path="/")
        return resp

    app.extensions["ark_start_session"] = start_session

    app.register_blueprint(public.bp)
    app.register_blueprint(workspace.bp, url_prefix="/app")
    app.register_blueprint(admin.bp, url_prefix="/admin")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def _error(exc):
        code = getattr(exc, "code", 500)
        return render_template("error.html", code=code, name=getattr(exc, "name", "Error")), code

    @app.errorhandler(500)
    def _crash(exc):
        return render_template("error.html", code=500, name="Something went wrong"), 500

    return app
