"""Tests for UsernameIngestionSource."""
from unittest.mock import MagicMock, patch

import pytest

from ark_angel.ingest.username_source import SITES, UsernameIngestionSource


def _resp(status: int, text: str = "<html>profile</html>") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def test_200_response_creates_lead():
    with patch("ark_angel.ingest.username_source.requests.get") as mock_get:
        mock_get.return_value = _resp(200)
        leads = UsernameIngestionSource().fetch_leads("testuser")

    assert len(leads) == len(SITES)
    assert all("testuser" in lead.summary for lead in leads)


def test_404_response_returns_no_leads():
    with patch("ark_angel.ingest.username_source.requests.get") as mock_get:
        mock_get.return_value = _resp(404)
        leads = UsernameIngestionSource().fetch_leads("ghostuser")

    assert leads == []


def test_network_error_is_silently_skipped():
    import requests as req

    with patch("ark_angel.ingest.username_source.requests.get") as mock_get:
        mock_get.side_effect = req.RequestException("connection refused")
        leads = UsernameIngestionSource().fetch_leads("testuser")

    assert leads == []


def test_at_symbol_stripped_from_username():
    with patch("ark_angel.ingest.username_source.requests.get") as mock_get:
        mock_get.return_value = _resp(200)
        leads = UsernameIngestionSource().fetch_leads("@janedoe")

    assert all("@janedoe" not in lead.identifier for lead in leads)
    assert all("janedoe" in lead.summary for lead in leads)


def test_lead_identifiers_are_unique():
    with patch("ark_angel.ingest.username_source.requests.get") as mock_get:
        mock_get.return_value = _resp(200)
        leads = UsernameIngestionSource().fetch_leads("testuser")

    identifiers = [l.identifier for l in leads]
    assert len(identifiers) == len(set(identifiers))


def test_absent_marker_on_200_is_not_a_hit():
    def fake_get(url, **kwargs):
        if "t.me" in url:
            return _resp(200, "<title>Telegram Messenger</title>")
        return _resp(404)

    with patch("ark_angel.ingest.username_source.requests.get", side_effect=fake_get):
        leads = UsernameIngestionSource().fetch_leads("ghostuser")

    assert leads == []


def test_hits_use_profile_url_and_are_labelled_unverified():
    def fake_get(url, **kwargs):
        if "gitlab.com/api" in url:
            return _resp(200, '[{"id": 1, "username": "someone"}]')
        return _resp(404)

    with patch("ark_angel.ingest.username_source.requests.get", side_effect=fake_get):
        leads = UsernameIngestionSource().fetch_leads("someone")

    assert len(leads) == 1
    assert "https://gitlab.com/someone" in leads[0].summary
    assert "unverified" in leads[0].summary
