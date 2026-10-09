"""Check username existence across public platforms."""
from __future__ import annotations

from typing import Sequence

import requests

from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Lead
from ark_angel.registry import ingestion_sources

_TIMEOUT = 10
_HEADERS = {"User-Agent": "ArkAngel-OSINT/0.1 (open-source missing persons research tool)"}

# Sites where HTTP 200 = profile exists, 4xx = not found.
# Results may include false positives — always verify manually.
SITES: dict[str, str] = {
    "GitHub": "https://github.com/{}",
    "GitLab": "https://gitlab.com/{}",
    "Keybase": "https://keybase.io/{}",
    "Telegram": "https://t.me/{}",
    "Reddit": "https://www.reddit.com/user/{}/about.json",
    "Replit": "https://replit.com/@{}",
    "Pastebin": "https://pastebin.com/u/{}",
    "DevTo": "https://dev.to/{}",
}


@ingestion_sources.register("username")
class UsernameIngestionSource(IngestionSource):
    """Checks a username against known platforms and returns leads for matches."""

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        username = identifier.lstrip("@")
        leads: list[Lead] = []
        for site, url_template in SITES.items():
            url = url_template.format(username)
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
                if resp.status_code == 200:
                    leads.append(
                        Lead(
                            identifier=f"username-{site.lower()}-{username}",
                            summary=f"{username} found on {site}: {url}",
                        )
                    )
            except requests.RequestException:
                pass
        return leads
