"""Check username existence across public platforms."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Sequence

import requests

from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Evidence, Lead

_TIMEOUT = 10
_MAX_WORKERS = 8
_HEADERS = {"User-Agent": "ArkAngel-OSINT/0.1 (open-source missing persons research tool)"}


@dataclass(frozen=True)
class Site:
    """How to probe one platform.

    Attributes:
        url: URL template requested with the username.
        profile: Human-facing profile URL template (defaults to ``url``).
        absent_marker: Text in a 200 response that means "no such user". Some
            sites answer 200 for every name, so status alone is not enough.
    """

    url: str
    profile: Optional[str] = None
    absent_marker: Optional[str] = None


@dataclass(frozen=True)
class UsernameHit:
    site: str
    username: str
    url: str


SITES: dict[str, Site] = {
    "GitHub": Site("https://github.com/{}"),
    "GitLab": Site("https://gitlab.com/api/v4/users?username={}", "https://gitlab.com/{}", absent_marker="[]"),
    "Keybase": Site("https://keybase.io/{}"),
    "Telegram": Site("https://t.me/{}", absent_marker="<title>Telegram Messenger</title>"),
    "Reddit": Site("https://www.reddit.com/user/{}/about.json", "https://www.reddit.com/user/{}"),
    "Replit": Site("https://replit.com/@{}"),
    "Pastebin": Site("https://pastebin.com/u/{}"),
    "DevTo": Site("https://dev.to/{}"),
}


def _probe(site_name: str, site: Site, username: str) -> Optional[UsernameHit]:
    try:
        resp = requests.get(site.url.format(username), headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    if site.absent_marker and site.absent_marker in str(getattr(resp, "text", "")):
        return None
    return UsernameHit(site=site_name, username=username, url=(site.profile or site.url).format(username))


class UsernameIngestionSource(IngestionSource):
    """Checks a username against known platforms and returns leads for matches.

    Hits mean "a profile with this name exists", not "this is the person".
    Every hit is labelled unverified and needs a human to confirm it.
    """

    def check(self, identifier: str) -> list[UsernameHit]:
        username = identifier.lstrip("@")
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            results = pool.map(lambda item: _probe(item[0], item[1], username), SITES.items())
        return [hit for hit in results if hit]

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        return [lead for _, lead in self.ingest(identifier)]

    def ingest(self, identifier: str) -> list[tuple[Evidence, Lead]]:
        """Return an (evidence, lead) pair for each platform with a matching profile."""
        return [hit_records(hit) for hit in self.check(identifier)]


def hit_records(hit: UsernameHit, simulated: bool = False) -> tuple[Evidence, Lead]:
    """Build the evidence item and lead for a username hit."""
    evidence_id = f"username:{hit.site.lower()}:{hit.username}"
    note = "simulated demo data" if simulated else "unverified"
    evidence = Evidence(
        identifier=evidence_id,
        type="username-hit",
        content=f"@{hit.username} has a profile on {hit.site}: {hit.url}",
        metadata={"source": "username-lookup", "handle": hit.username, "site": hit.site, "url": hit.url,
                  "simulated": simulated},
    )
    lead = Lead(
        identifier=f"username-{hit.site.lower()}-{hit.username}",
        summary=f"{hit.username} found on {hit.site} ({note}): {hit.url}",
        evidence_ids=[evidence_id],
        score=1.0,
    )
    return evidence, lead
