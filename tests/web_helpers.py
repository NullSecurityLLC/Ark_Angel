"""Shared fixtures for the website tests."""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from ark_angel.cases.store import FileCaseStore
from ark_angel.web.app import create_app
from ark_angel.web.db import SiteDB

PASSWORD = "correct horse battery"
UA = {"User-Agent": "pytest-browser"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class Site:
    """A site with an admin, plus helpers to act as different people."""

    def __init__(self, tmp_path: Path) -> None:
        self.base = tmp_path
        self.store = FileCaseStore(tmp_path / "cases")
        self.clock = Clock()
        self.db = SiteDB(tmp_path / "web.db", clock=self.clock)
        self.db.create_user("boss", PASSWORD, role="admin", status="active")
        self.app = create_app(self.store, self.db)
        self.app.config["TESTING"] = True

    def client(self):
        return self.app.test_client()

    def member(self, name: str):
        self.db.create_user(name, PASSWORD, status="active")
        client = self.client()
        assert login(client, name).status_code == 302
        return client

    def admin(self):
        client = self.client()
        assert login(client, "boss").status_code == 302
        return client


def token(client, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    return re.search(r'name="csrf_token" value="([^"]+)"', resp.get_data(as_text=True)).group(1)


def login(client, username: str, password: str = PASSWORD, next_url: str = ""):
    data = {"csrf_token": token(client, "/login"), "username": username, "password": password}
    if next_url:
        data["next"] = next_url
    return client.post("/login", data=data, headers=UA)


def post(client, path: str, data: dict, form_page: str = "", files: dict | None = None):
    """POST with the CSRF token from ``form_page`` (default: the account page, or /login when signed out)."""
    page = form_page or ("/app/account" if client.get_cookie("ark_session") else "/login")
    body = {"csrf_token": token(client, page), **data}
    for name, (content, filename) in (files or {}).items():
        body[name] = (io.BytesIO(content), filename)
    return client.post(path, data=body, content_type="multipart/form-data" if files else None, headers=UA)


def new_case(client, title: str = "Riley Tamsin") -> str:
    resp = post(client, "/app/cases/new", {"title": title})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return re.search(r"/app/cases/(c[0-9a-f]+)", resp.headers["Location"]).group(1)


def photo(path: Path, lat: float = 39.7527, lon: float = -104.9997) -> bytes:
    from ark_angel.demo import make_photo

    return make_photo(path, lat, lon, "2026:10:01 18:00:00", "Acme", "Cam 1").read_bytes()


def publish(site: Site, owner, case_id: str, name: str = "Riley Tamsin", image: bytes = b"") -> str:
    """Request and approve a public profile. Returns its public slug."""
    files = {"photo": (image, "riley.jpg")} if image else None
    resp = post(owner, f"/app/cases/{case_id}/profile",
                {"action": "request", "name": name, "age": "19", "last_seen_place": "Union Station, Denver",
                 "last_seen_date": "28 Sep 2026", "description": "Red backpack.", "contact": "Denver PD 720-555-0100",
                 "police_report": "Denver PD 26-1234", "attest": "yes"},
                form_page=f"/app/cases/{case_id}/profile", files=files)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return site.db.approve_publication(case_id)


@pytest.fixture
def site(tmp_path: Path) -> Site:
    pytest.importorskip("flask")
    return Site(tmp_path)
