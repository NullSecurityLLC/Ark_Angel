"""Import chat exports as evidence, one item per message.

Supported formats:

- WhatsApp "Export chat" text files, both layouts:
  Android ``12/31/26, 9:41 PM - Name: message`` and
  iPhone ``[12/31/26, 9:41:05 PM] Name: message``.
  Lines that don't start a new message continue the previous one. System lines
  ("Messages and calls are end-to-end encrypted") and "<Media omitted>"
  placeholders are skipped.
- Telegram Desktop "Export chat history" JSON (``result.json``).

Other chat tools usually export CSV; ``ingest-csv`` handles those (it knows
message/content, author/sender and timestamp columns).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from ark_angel.ingest.base import IngestionSource
from ark_angel.ingest.csv_source import parse_when
from ark_angel.models import Evidence, Lead

_WHATSAPP_ANDROID = re.compile(
    r"^(?P<when>\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},? \d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?)?) - (?P<rest>.*)$")
_WHATSAPP_IOS = re.compile(
    r"^‎?\[(?P<when>\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},? \d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?)?)\] (?P<rest>.*)$")
_MEDIA_ONLY = re.compile(r"^‎?(<Media omitted>|<attached: [^>]+>|image omitted|video omitted|audio omitted|"
                         r"sticker omitted|GIF omitted|document omitted)$", re.IGNORECASE)


@dataclass
class ChatMessage:
    when: str
    sender: str
    text: str


@dataclass
class ChatImport:
    format: str
    evidence: list[Evidence] = field(default_factory=list)
    skipped: int = 0
    unparsed_times: int = 0


def detect_format(path: Path) -> str:
    """``"telegram"`` for JSON exports, ``"whatsapp"`` for text exports that look like one."""
    if path.suffix.lower() == ".json":
        return "telegram"
    head = [line for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:20] if line.strip()]
    hits = sum(1 for line in head if _WHATSAPP_ANDROID.match(line) or _WHATSAPP_IOS.match(line))
    if head and hits >= max(1, len(head) // 2):
        return "whatsapp"
    raise ValueError(f"{path.name} doesn't look like a WhatsApp or Telegram export; pass --format, "
                     "or use `ingest` for plain text and `ingest-csv` for CSV exports")


def parse_whatsapp(text: str) -> tuple[list[ChatMessage], int]:
    """Return messages and how many system/media-only lines were skipped."""
    messages: list[ChatMessage] = []
    skipped = 0
    for line in text.splitlines():
        match = _WHATSAPP_ANDROID.match(line) or _WHATSAPP_IOS.match(line)
        if match:
            rest = match.group("rest")
            sender, sep, body = rest.partition(": ")
            if not sep:  # "Jordan added Priya", encryption notices, etc.
                skipped += 1
                messages.append(ChatMessage("", "", ""))  # placeholder so continuations aren't misattributed
                continue
            messages.append(ChatMessage(match.group("when"), sender.strip("‎ "), body))
        elif messages and line.strip():
            messages[-1].text += "\n" + line
    kept = []
    for message in messages:
        if not message.sender:
            continue
        if _MEDIA_ONLY.match(message.text.strip()):
            skipped += 1
            continue
        kept.append(message)
    return kept, skipped


def _telegram_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in value)
    return ""


def parse_telegram(text: str) -> tuple[list[ChatMessage], int]:
    data = json.loads(text)
    messages, skipped = [], 0
    for item in data.get("messages", []):
        body = _telegram_text(item.get("text"))
        if item.get("type") != "message" or not body.strip():
            skipped += 1
            continue
        messages.append(ChatMessage(str(item.get("date", "")), str(item.get("from") or item.get("from_id") or "?"),
                                    body))
    return messages, skipped


def _clean_when(value: str) -> str:
    """``"10/02/26, 8:15 p.m."`` -> ``"10/02/26 8:15 pm"``; dotted dates like 02.10.26 are left alone."""
    value = re.sub(r"\b([AaPp])\.\s?([Mm])\.?", r"\1\2", value)
    return value.replace(",", "").replace("\u202f", " ")


class ChatSource(IngestionSource):
    """Turns a WhatsApp or Telegram export into evidence, one item per message."""

    def __init__(self, format: str = "auto", dayfirst: bool = False, source: Optional[str] = None) -> None:
        self.format = format
        self.dayfirst = dayfirst
        self.source = source

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        return [Lead(identifier=e.identifier, summary=str(e.content)) for e in self.load(identifier).evidence]

    def load(self, path: str | Path) -> ChatImport:
        path = Path(path)
        fmt = detect_format(path) if self.format == "auto" else self.format
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if fmt == "whatsapp":
            messages, skipped = parse_whatsapp(text)
        elif fmt == "telegram":
            try:
                messages, skipped = parse_telegram(text)
            except (json.JSONDecodeError, AttributeError) as exc:
                raise ValueError(f"{path.name} isn't a Telegram JSON export ({exc})") from exc
        else:
            raise ValueError(f"unknown chat format {fmt!r}")

        result = ChatImport(format=fmt, skipped=skipped)
        source = self.source or path.stem
        for number, message in enumerate(messages, start=1):
            when = parse_when(_clean_when(message.when), self.dayfirst)
            metadata: dict = {"source": source, "sender": message.sender}
            if when:
                metadata["timestamp"] = when.strftime("%Y-%m-%d %H:%M:%S")
                prefix = f"{when:%Y-%m-%d %H:%M} "
            else:
                prefix = f"[{message.when}] " if message.when else ""
                if message.when:
                    result.unparsed_times += 1
            body = " ".join(message.text.split())
            result.evidence.append(Evidence(identifier=f"{path.stem}:{number}", type="chat",
                                            content=f"{prefix}{message.sender}: {body}", metadata=metadata))
        return result
