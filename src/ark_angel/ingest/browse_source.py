"""The investigator's browsing as evidence: page visits, searches and clipped text.

Captures come from the ArkAngel Recorder browser extension (``ark-angel
browse``) or from browser history (``ingest-history``). Each one becomes an
evidence item with ``metadata["origin"] = "investigator"``.

What counts toward correlation is only what the *page* said, kept in
``metadata["page_excerpt"]``: short snippets around every phone, email,
handle, plate, coordinate or watched phrase on it. The search you typed, the
URL you followed and the profile you opened are kept for the trail
(``ark-angel trail``) but never count as corroboration. Otherwise googling a
number would make the number rank higher.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from ark_angel.analysis.base import INVESTIGATOR_ORIGIN
from ark_angel.analysis.extract import extract_entities, parse_datetime
from ark_angel.models import Evidence

SOURCE = "browsing"
KINDS = ("visit", "search", "clip")
MAX_URL = 2048
MAX_TITLE = 300
MAX_TEXT = 300_000  # characters of page text read per capture
MAX_SELECTION = 5000
MAX_SNIPPETS = 40
SNIPPET_CONTEXT = 60
_SCHEMES = ("http", "https")

# host suffix -> (engine label, query parameters to try), plus an optional path the search lives under.
_SEARCH_ENGINES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("google.", "Google", ("q",), "/search"),
    ("bing.com", "Bing", ("q",), "/search"),
    ("duckduckgo.com", "DuckDuckGo", ("q",), ""),
    ("search.brave.com", "Brave", ("q",), "/search"),
    ("search.yahoo.com", "Yahoo", ("p",), ""),
    ("yandex.", "Yandex", ("text",), "/search"),
    ("baidu.com", "Baidu", ("wd", "word"), "/s"),
    ("startpage.com", "Startpage", ("query", "q"), ""),
    ("ecosia.org", "Ecosia", ("q",), "/search"),
    ("kagi.com", "Kagi", ("q",), "/search"),
    ("youtube.com", "YouTube", ("search_query",), "/results"),
    ("facebook.com", "Facebook", ("q",), "/search"),
    ("x.com", "X", ("q",), "/search"),
    ("twitter.com", "X", ("q",), "/search"),
    ("reddit.com", "Reddit", ("q",), "/search"),
    ("tiktok.com", "TikTok", ("q",), "/search"),
    ("linkedin.com", "LinkedIn", ("keywords",), "/search"),
    ("github.com", "GitHub", ("q",), "/search"),
    ("instagram.com", "Instagram", ("q",), "/explore/search"),
)

# host -> (platform, path pattern capturing the handle). Reserved first segments aren't profiles.
_PROFILES: tuple[tuple[str, str, re.Pattern], ...] = (
    ("instagram.com", "Instagram", re.compile(r"^/([A-Za-z0-9_.]{2,30})/?$")),
    ("x.com", "X", re.compile(r"^/([A-Za-z0-9_]{2,15})/?$")),
    ("twitter.com", "X", re.compile(r"^/([A-Za-z0-9_]{2,15})/?$")),
    ("tiktok.com", "TikTok", re.compile(r"^/@([A-Za-z0-9_.]{2,24})/?$")),
    ("threads.net", "Threads", re.compile(r"^/@([A-Za-z0-9_.]{2,30})/?$")),
    ("youtube.com", "YouTube", re.compile(r"^/@([A-Za-z0-9_.-]{2,30})/?$")),
    ("reddit.com", "Reddit", re.compile(r"^/(?:u|user)/([A-Za-z0-9_-]{3,20})/?$")),
    ("github.com", "GitHub", re.compile(r"^/([A-Za-z0-9-]{2,39})/?$")),
    ("facebook.com", "Facebook", re.compile(r"^/([A-Za-z0-9.]{5,50})/?$")),
    ("linkedin.com", "LinkedIn", re.compile(r"^/in/([A-Za-z0-9_-]{3,100})/?$")),
    ("t.me", "Telegram", re.compile(r"^/([A-Za-z0-9_]{5,32})/?$")),
    ("twitch.tv", "Twitch", re.compile(r"^/([A-Za-z0-9_]{4,25})/?$")),
    ("pinterest.com", "Pinterest", re.compile(r"^/([A-Za-z0-9_]{3,30})/?$")),
    ("snapchat.com", "Snapchat", re.compile(r"^/add/([A-Za-z0-9_.-]{3,15})/?$")),
    ("bsky.app", "Bluesky", re.compile(r"^/profile/([A-Za-z0-9_.-]{3,253})/?$")),
    ("medium.com", "Medium", re.compile(r"^/@([A-Za-z0-9_.]{1,30})/?$")),
)
_RESERVED = {
    "explore", "accounts", "about", "home", "search", "settings", "login", "signup", "help", "privacy", "terms",
    "messages", "notifications", "i", "intent", "share", "hashtag", "watch", "groups", "pages", "events",
    "marketplace", "gaming", "reels", "stories", "direct", "p", "tv", "orgs", "topics", "trending", "features",
    "pricing", "enterprise", "marketplace", "sponsors", "issues", "pulls", "new", "profile.php", "photo.php",
    "people", "friends", "business", "ads", "policies", "compose", "tos", "jobs", "feed", "following",
}


# Query parameters that carry secrets: password-reset and magic sign-in links, OAuth codes, signed downloads,
# session ids. They'd otherwise land in a case the whole team reads, and in every export of it.
_SECRET_PARAM_NAMES = {
    "code", "state", "key", "apikey", "sid", "auth", "otp", "ticket", "nonce", "jwt", "sig", "hash", "pwd", "pass",
    "samlresponse", "samlrequest", "oauth_verifier", "client_id", "magic", "t",
}
_SECRET_PARAM_PARTS = ("token", "secret", "password", "passwd", "signature", "session", "credential",
                       "auth_", "api_key", "x-amz-", "reset")
REDACTED = "REDACTED"


def scrub_url(url: str) -> str:
    """``url`` with credentials, secret-looking query values and token fragments removed. Search terms stay."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    params = [(name, REDACTED if _secret_param(name) else value)
              for name, value in parse_qsl(parts.query, keep_blank_values=True)]
    # A fragment with "=" in it is OAuth's implicit flow (#access_token=...), not an anchor on the page.
    fragment = "" if "=" in parts.fragment else parts.fragment
    return urlunsplit((parts.scheme, host, parts.path, urlencode(params, safe="/:@,"), fragment))


def _secret_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SECRET_PARAM_NAMES or any(part in lowered for part in _SECRET_PARAM_PARTS)


@dataclass
class Capture:
    """One thing the investigator did in the browser."""

    url: str
    when: datetime
    kind: str = "visit"
    title: str = ""
    text: str = ""
    selection: str = ""
    session: str = ""


def parse_capture(payload: dict) -> Capture:
    """Validate a capture from the extension (or anywhere else). Raises ``ValueError``."""
    if not isinstance(payload, dict):
        raise ValueError("capture must be a JSON object")
    url = str(payload.get("url") or "").strip()[:MAX_URL]
    if urlsplit(url).scheme.lower() not in _SCHEMES:
        raise ValueError("url must be http or https")
    kind = str(payload.get("kind") or "visit")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    when = parse_datetime(str(payload.get("when") or "").replace("T", " ")) or datetime.now()
    selection = " ".join(str(payload.get("selection") or "").split())[:MAX_SELECTION]
    if kind == "clip" and not selection:
        raise ValueError("a clip needs the selected text")
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(payload.get("session") or ""))[:40]
    return Capture(url=url, when=when, kind=kind, title=" ".join(str(payload.get("title") or "").split())[:MAX_TITLE],
                   text=str(payload.get("text") or "")[:MAX_TEXT], selection=selection, session=session)


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches_host(host: str, suffix: str) -> bool:
    if suffix.endswith("."):  # google., yandex.: any country domain
        label = suffix.rstrip(".")
        return host == label or host.startswith(suffix) or f".{label}." in f".{host}"
    return host == suffix or host.endswith("." + suffix)


def search_query(url: str) -> Optional[tuple[str, str]]:
    """``(engine, query)`` if ``url`` is a search results page."""
    parts = urlsplit(url)
    host = _host(url)
    params = parse_qs(parts.query)
    for suffix, engine, keys, path in _SEARCH_ENGINES:
        if not _matches_host(host, suffix) or (path and not parts.path.startswith(path)):
            continue
        for key in keys:
            value = " ".join(" ".join(params.get(key, [])).split())
            if value:
                return engine, value
    return None


def profile_handle(url: str) -> Optional[tuple[str, str]]:
    """``(platform, handle)`` if ``url`` is a social profile page."""
    host, path = _host(url), unquote(urlsplit(url).path)
    for suffix, platform, pattern in _PROFILES:
        if not _matches_host(host, suffix):
            continue
        match = pattern.match(path)
        if match and match.group(1).lower() not in _RESERVED:
            return platform, match.group(1).lower()
    return None


def _snippet(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - SNIPPET_CONTEXT), min(len(text), end + SNIPPET_CONTEXT)
    body = " ".join(text[lo:hi].split())
    return ("…" if lo else "") + body + ("…" if hi < len(text) else "")


def page_snippets(text: str, watch: Sequence[str] = (), skip: Iterable[tuple[str, str]] = ()) -> list[str]:
    """Short passages around every entity, and every watched phrase, in a page's text.

    ``watch`` holds phrases worth catching even without an entity next to
    them: the case's place names, a principal's names and addresses. ``skip``
    holds entities to leave out, like the handle of the profile being viewed,
    which its own page naturally mentions.
    """
    skipped = set(skip)
    spans: list[tuple[int, int]] = []
    for entity in extract_entities(text):
        if (entity.kind, entity.value) in skipped or entity.kind == "url":
            continue
        position = text.find(entity.raw) if entity.raw else -1
        if position >= 0:
            spans.append((position, position + len(entity.raw)))
    phrases = sorted({" ".join(p.split()) for p in watch if p and p.strip()}, key=len, reverse=True)
    if phrases:
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(r"\s+".join(map(re.escape, p.split())) for p in phrases)
                             + r")(?!\w)", re.IGNORECASE)
        spans += [m.span() for m in pattern.finditer(text)]
    snippets: list[str] = []
    last_end = -1
    for start, end in sorted(spans):
        if start < last_end:  # already inside the previous snippet
            continue
        snippets.append(_snippet(text, start, end))
        last_end = end + SNIPPET_CONTEXT
        if len(snippets) >= MAX_SNIPPETS:
            break
    return snippets


def capture_id(capture: Capture) -> str:
    digest = hashlib.sha1(f"{capture.kind}|{capture.url}|{capture.when.isoformat()}|{capture.selection}".encode())
    return f"web:{capture.when:%Y%m%dT%H%M%S}:{digest.hexdigest()[:8]}"


def capture_to_evidence(capture: Capture, watch: Sequence[str] = ()) -> Evidence:
    """One capture as an investigator-origin evidence item. The URL is scrubbed of secrets first."""
    capture = Capture(url=scrub_url(capture.url), when=capture.when, kind=capture.kind, title=capture.title,
                      text=capture.text, selection=capture.selection, session=capture.session)
    host = _host(capture.url)
    search = search_query(capture.url)
    profile = profile_handle(capture.url)
    kind = "search" if search and capture.kind == "visit" else capture.kind
    stamp = f"{capture.when:%Y-%m-%d %H:%M}"
    title = f'"{capture.title}"' if capture.title else capture.url

    excerpt: list[str] = []
    if kind == "search":
        content = f'{stamp} Searched {search[0]} for "{search[1]}"'
    elif kind == "clip":
        content = f'{stamp} Clipped from {title} ({host}): "{capture.selection}"'
        excerpt = [capture.selection]
    else:
        content = f"{stamp} Visited {title} ({host})"
        if profile:
            content += f", {profile[0]} profile {profile[1]}"
        skip = [("handle", profile[1])] if profile else []
        excerpt = page_snippets(capture.text, watch, skip) if capture.text else []
        if excerpt:
            content += f" | page mentions: {' | '.join(excerpt)}"

    metadata = {"source": SOURCE, "origin": INVESTIGATOR_ORIGIN, "kind": kind, "url": capture.url, "host": host,
                "title": capture.title, "timestamp": capture.when.strftime("%Y-%m-%d %H:%M:%S")}
    if excerpt:
        metadata["page_excerpt"] = " | ".join(excerpt)
    if capture.session:
        metadata["session"] = capture.session
    if search:
        metadata["engine"], metadata["query"] = search
    if profile:
        metadata["platform"], metadata["profile"] = profile
    return Evidence(identifier=capture_id(capture), type="web", content=content, metadata=metadata)
