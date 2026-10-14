"""Import email (.eml files, .mbox mailboxes, or a folder of .eml) as evidence.

Each message becomes one evidence item, and each image attachment with EXIF
data becomes its own item carrying GPS, capture time and device, since photos
emailed to a tip line often carry GPS.

Three choices keep the leads honest:

- Recipients (To/Cc) go in metadata only, not the scanned text. The tip line's
  own address is on every message and would otherwise top the leads.
- Quoted reply text ("> ..." and everything after "On ... wrote:") is dropped,
  so a phone number in a thread isn't counted again in every reply.
- Dates keep the sender's wall-clock time (the timezone is dropped, not
  converted), matching how tip-line times are recorded.
"""
from __future__ import annotations

import mailbox
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator, Optional, Sequence

from ark_angel.geo.exif import exif_metadata, load_exif_bytes
from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Evidence, Lead

MAX_BODY_CHARS = 5000
_QUOTE_HEADER = re.compile(r"^\s*(On .{0,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+)\s*$",
                           re.IGNORECASE)
_SIGNATURE = re.compile(r"^-- ?$")


class _TextFromHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextFromHTML()
    parser.feed(html)
    return re.sub(r"[ \t]+", " ", "".join(parser.parts))


def strip_quoted(body: str) -> str:
    """Drop quoted replies and signatures: "> lines", "On ... wrote:" onwards, "-- " onwards."""
    kept = []
    for line in body.splitlines():
        if _QUOTE_HEADER.match(line) and kept or _SIGNATURE.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        content = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return html_to_text(content) if part.get_content_type() == "text/html" else content


def _short_id(message: EmailMessage, fallback: str) -> str:
    raw = (message.get("Message-ID") or "").strip().strip("<>")
    local = raw.split("@", 1)[0]
    return re.sub(r"[^A-Za-z0-9._-]", "", local)[:40] or fallback


@dataclass
class EmailImport:
    evidence: list[Evidence] = field(default_factory=list)
    messages: int = 0
    attachments_with_gps: int = 0
    skipped_attachments: int = 0


class EmailSource(IngestionSource):
    """Turns .eml files, .mbox mailboxes or folders of .eml into evidence."""

    def __init__(self, source: Optional[str] = None) -> None:
        self.source = source

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        return [Lead(identifier=e.identifier, summary=str(e.content)) for e in self.load(identifier).evidence]

    def load(self, path: str | Path) -> EmailImport:
        path = Path(path)
        source = self.source or path.stem
        result = EmailImport()
        seen: set[str] = set()
        for number, message in enumerate(_messages(path), start=1):
            identifier = f"{path.stem}:{_short_id(message, str(number))}"
            if identifier in seen:  # two messages sharing a Message-ID local part
                identifier = f"{identifier}@{number}"
            seen.add(identifier)
            self._message(message, identifier, source, result)
        return result

    def _message(self, message: EmailMessage, identifier: str, source: str, result: EmailImport) -> None:
        result.messages += 1
        sender = ", ".join(addr for _, addr in getaddresses([message.get("From", "")]) if addr)
        sender_name = ", ".join(name for name, _ in getaddresses([message.get("From", "")]) if name)
        recipients = [addr for _, addr in getaddresses(message.get_all("To", []) + message.get_all("Cc", []))]
        when = _date(message.get("Date"))
        metadata: dict = {"source": source, "sender": sender, "recipients": recipients,
                          "subject": str(message.get("Subject", ""))}
        if when:
            metadata["timestamp"] = when.strftime("%Y-%m-%d %H:%M:%S")

        body = strip_quoted(_body(message))
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + " …[truncated]"
        attachments = [part for part in message.iter_attachments()]
        names = [part.get_filename() or "unnamed" for part in attachments]
        parts = [f"From: {sender_name + ' ' if sender_name else ''}<{sender}>" if sender else "",
                 f"Subject: {metadata['subject']}" if metadata["subject"] else "",
                 " ".join(body.split()),
                 f"Attachments: {', '.join(names)}" if names else ""]
        prefix = f"{when:%Y-%m-%d %H:%M} " if when else ""
        result.evidence.append(Evidence(identifier=identifier, type="email",
                                        content=prefix + " | ".join(p for p in parts if p), metadata=metadata))

        for index, part in enumerate(attachments, start=1):
            self._attachment(part, names[index - 1], identifier, index, source, result)

    def _attachment(self, part: EmailMessage, name: str, parent: str, index: int, source: str,
                    result: EmailImport) -> None:
        if part.get_content_maintype() != "image":
            return
        data = part.get_payload(decode=True) or b""
        found = exif_metadata(load_exif_bytes(data))
        if not found:
            result.skipped_attachments += 1
            return
        if "latitude" in found:
            result.attachments_with_gps += 1
        details = []
        if "latitude" in found:
            details.append(f"GPS {found['latitude']:.6f}, {found['longitude']:.6f}")
        if "timestamp" in found:
            details.append(f"taken {found['timestamp']}")
        if "device" in found:
            details.append(f"device {found['device']}")
        result.evidence.append(Evidence(
            identifier=f"{parent}/{index}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)}",
            type="image",
            content=f"Photo {name} attached to {parent}: {'; '.join(details)}",
            metadata={"source": source, "attached_to": parent, **found},
        ))


def _date(value: Optional[str]):
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value)).replace(tzinfo=None)
    except (TypeError, ValueError, IndexError):
        return None


def _messages(path: Path) -> Iterator[EmailMessage]:
    parser = BytesParser(policy=policy.default)
    if path.is_dir():
        for child in sorted(path.glob("*.eml")):
            yield parser.parsebytes(child.read_bytes())
    elif path.suffix.lower() == ".mbox" or _looks_like_mbox(path):
        for raw in mailbox.mbox(str(path), factory=None, create=False):
            yield parser.parsebytes(raw.as_bytes())
    else:
        yield parser.parsebytes(path.read_bytes())


def _looks_like_mbox(path: Path) -> bool:
    with open(path, "rb") as handle:
        return handle.read(5) == b"From "
