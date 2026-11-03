"""Recorder API (``/api/recorder``): the ArkAngel Recorder browser extension, recording into a website case.

Same two calls as the local ``ark-angel browse`` receiver, so the extension
doesn't care which one it's paired with:

- ``GET  /api/recorder/status``: which case the token records into
- ``POST /api/recorder/capture``: one page, search or clip, as JSON

The API never looks at the session cookie. It takes a per-case recorder
token in an ``Authorization: Bearer`` header instead (members make one on the
case's Recorder page), so no other website can ride a signed-in member's
cookie into it, and no CSRF exemption is needed for the rest of the site.
Requests from web pages (an ``Origin`` that isn't a browser extension) are
refused outright.

Every capture re-checks the token: the account must be active and still an
editor or owner of that one case. Captures are rate limited per token and
written under the case's lock, like tips and uploads.
"""
from __future__ import annotations

from typing import Optional

from flask import Blueprint, Response, g, jsonify, request

from ark_angel.web.app import site

bp = Blueprint("recorder", __name__)

EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
CAPTURES_PER_HOUR = 1200  # a page every 3 seconds for an hour, per token


def _error(code: int, message: str) -> Response:
    resp = jsonify(error=message)
    resp.status_code = code
    return resp


def guard() -> Optional[Response]:
    """Authenticate an API request. Called by the site's one request guard for this blueprint."""
    origin = request.headers.get("Origin")
    if origin and not origin.startswith(EXTENSION_ORIGINS):
        return _error(403, "requests from web pages are refused")
    if request.method == "OPTIONS" and origin:  # a CORS preflight from the extension
        resp = Response(status=204)
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Max-Age"] = "600"
        return resp
    if request.method not in ("GET", "POST"):
        return _error(405, "method not allowed")
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    access = site().db.recorder_access(token) if token else None
    if access is None:
        return _error(401, "missing, expired or revoked recorder token, or you can no longer edit that case")
    g.recorder = access
    return None


@bp.after_request
def _cors(resp: Response) -> Response:
    """Let the extension read the answer. Web-page origins never get here: the guard refused them."""
    origin = request.headers.get("Origin", "")
    if origin.startswith(EXTENSION_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/status")
def status():
    access = g.recorder
    case = site().store.get_case(access.case_id)
    mine = sum(1 for item in case.evidence if (item.metadata or {}).get("recorded_by") == access.username)
    return jsonify(case=access.case_id, title=case.title, user=access.username, captured=mine)


@bp.post("/capture")
def capture():
    from ark_angel import pipeline
    from ark_angel.browse_server import capture_hits, index_evidence, watch_terms
    from ark_angel.ingest.browse_source import capture_to_evidence, parse_capture

    access = g.recorder
    s = site()
    if request.content_length is None or request.content_length > MAX_CAPTURE_BYTES:
        return _error(413, "a capture can be up to 2 MB")
    if not s.db.allow(f"recorder:{access.token_id}", "recorder", CAPTURES_PER_HOUR, 3600):
        return _error(429, "too many captures from this token in the last hour; they'll queue in the extension")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error(400, "send one capture as a JSON object")
    try:
        parsed = parse_capture(payload)
    except ValueError as exc:
        return _error(400, str(exc))

    with s.case_lock(access.case_id):
        case = s.store.get_case(access.case_id)
        evidence = capture_to_evidence(parsed, watch_terms(case))
        evidence.metadata["recorded_by"] = access.username
        index, labels = index_evidence(case.evidence)
        hits = capture_hits(evidence, index, labels)
        s.store.add_evidence(access.case_id, [evidence])
        if hits:  # only a page that shares something with the case can change the leads
            pipeline.enrich(s.store, access.case_id)
    return jsonify(stored=evidence.identifier, kind=evidence.metadata["kind"], hits=hits)
