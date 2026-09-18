"""Tests for UsernameIngestionSource."""
from unittest.mock import MagicMock, patch

import pytest

from ark_angel.ingest.username_source import SITES, UsernameIngestionSource


def _resp(status: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status
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
