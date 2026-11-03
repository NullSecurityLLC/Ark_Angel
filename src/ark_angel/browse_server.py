"""``ark-angel browse``: the local receiver for the ArkAngel Recorder browser extension.

It listens on 127.0.0.1 only, so browsing data never leaves this computer.
Every request must carry the pairing token. Requests are refused when they
come from a web page (an ``Origin`` that isn't a browser extension) or name a
host other than localhost, which stops pages you visit from writing into the
case or reading it through DNS rebinding.

Each capture is stored as investigator-origin evidence straight away, and
checked against everything already in the case, so a page that mentions a
phone from the tip line is flagged while you're still on it.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from ark_angel.analysis.base import is_investigator_activity
from ark_angel.analysis.correlate import entities_of, source_of
from ark_angel.cases.store import FileCaseStore
from ark_angel.ingest.browse_source import capture_to_evidence, parse_capture

DEFAULT_PORT = 8766
MAX_BODY = 2 * 1024 * 1024
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
TRACKED = ("phone", "email", "handle", "plate")


def load_token(path: Path, rotate: bool = False) -> str:
    """The pairing token, created on first use and kept so the extension stays paired across runs."""
    if path.exists() and not rotate:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows: permissions come from the user profile folder
        pass
    return token


def watch_terms(case) -> list[str]:
    """Phrases worth catching on a page even with no phone or handle next to them."""
    terms = [name for place in case.places for name in (place.name, *place.aliases)]
    if case.principal is not None:
        p = case.principal
        terms += [p.name, *p.aliases] + [alias for place in p.places for alias in place.aliases]
    return [t for t in terms if len(t.strip()) >= 3]


Index = dict[tuple[str, str], list[str]]


def evidence_label(item) -> str:
    meta = item.metadata or {}
    return f"a page on {meta.get('host')}" if is_investigator_activity(item) else f"{source_of(item)} ({item.identifier})"


def index_evidence(items, index: Optional[Index] = None, labels: Optional[dict[str, str]] = None) -> tuple[Index, dict]:
    """Where each phone, email, handle and plate appears; extends ``index`` and ``labels`` when given."""
    index = {} if index is None else index
    labels = {} if labels is None else labels
    for item in items:
        labels[item.identifier] = evidence_label(item)
        for entity in entities_of(item):
            if entity.kind in TRACKED:
                ids = index.setdefault((entity.kind, entity.value), [])
                if item.identifier not in ids:
                    ids.append(item.identifier)
    return index, labels


def capture_hits(evidence, index: Index, labels: dict[str, str]) -> list[dict]:
    """What a new capture shares with evidence already in the case."""
    hits = []
    for entity in entities_of(evidence):
        others = [eid for eid in index.get((entity.kind, entity.value), []) if eid != evidence.identifier]
        if entity.kind in TRACKED and others:
            hits.append({"kind": entity.kind, "value": entity.value, "seen_in": [labels[eid] for eid in others[:5]]})
    return hits


def describe(evidence) -> str:
    meta = evidence.metadata
    return (f'search "{meta.get("query")}"' if meta["kind"] == "search" else
            f"clip from {meta['host']}" if meta["kind"] == "clip" else
            f"{meta.get('title') or meta['url']} ({meta['host']})")


class Recorder:
    """Stores captures for one case and spots matches with what the case already holds."""

    def __init__(self, store: FileCaseStore, case_id: str, log: Callable[[str], None] = print) -> None:
        self.store, self.case_id, self.log = store, case_id, log
        self.lock = threading.Lock()
        case = store.get_case(case_id)
        self.title = case.title
        self.watch = watch_terms(case)
        self.index, self.labels = index_evidence(case.evidence)
        self.captured = 0
        self.matched = 0

    def capture(self, payload: dict) -> dict:
        evidence = capture_to_evidence(parse_capture(payload), self.watch)
        with self.lock:
            self.store.add_evidence(self.case_id, [evidence])
            hits = capture_hits(evidence, self.index, self.labels)
            index_evidence([evidence], self.index, self.labels)
            self.captured += 1
            self.matched += bool(hits)
        meta = evidence.metadata
        self.log(f"  {meta['timestamp'][11:16]}  {meta['kind']:<6} {describe(evidence)}")
        for hit in hits:
            self.log(f"           MATCH {hit['kind']} {hit['value']}: also in {', '.join(hit['seen_in'])}")
        return {"stored": evidence.identifier, "kind": meta["kind"], "hits": hits}

    def status(self) -> dict:
        return {"case": self.case_id, "title": self.title, "captured": self.captured, "matched": self.matched}


class _Handler(BaseHTTPRequestHandler):
    server: "RecorderServer"
    server_version = "ArkAngelRecorder"
    sys_version = ""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin", "")
        if origin.startswith(EXTENSION_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(data)

    def _refusal(self, need_token: bool = True) -> Optional[tuple[int, str]]:
        host = (self.headers.get("Host") or "").lower()
        if host not in self.server.allowed_hosts:
            return 421, "wrong host"
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(EXTENSION_ORIGINS):
            return 403, "requests from web pages are refused"
        if need_token:
            given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), self.server.token.encode()):
                return 401, "missing or wrong token (copy it from the `ark-angel browse` window)"
        return None

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
        refused = self._refusal(need_token=False)
        if refused:
            return self._send(refused[0], {"error": refused[1]})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        refused = self._refusal()
        if refused:
            return self._send(refused[0], {"error": refused[1]})
        if self.path != "/status":
            return self._send(404, {"error": "not found"})
        self._send(200, self.server.recorder.status())

    def do_POST(self) -> None:  # noqa: N802
        refused = self._refusal()
        if refused:
            return self._send(refused[0], {"error": refused[1]})
        if self.path != "/capture":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad Content-Length"})
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"error": f"body must be 1 byte to {MAX_BODY // 1024 // 1024} MB"})
        try:
            payload = json.loads(self.rfile.read(length))
            result = self.server.recorder.capture(payload)
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send(400, {"error": str(exc)})
        self._send(200, result)


class RecorderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, recorder: Recorder, token: str, port: int = DEFAULT_PORT) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.recorder, self.token = recorder, token
        bound = self.server_address[1]
        self.port = bound
        self.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
