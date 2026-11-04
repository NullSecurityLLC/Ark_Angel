"""Make untrusted text safe for each place it gets shown.

Evidence is written by strangers: tips, emails, chat messages, web pages the
Recorder captured, names mapped in OpenStreetMap. Stored evidence stays exactly
as it arrived (it may be an exhibit). It's made safe on the way *out*, in the
form the destination needs:

- :func:`printable` for terminals: no escape sequences that could rewrite the
  screen, retitle the window, fake a hyperlink or write to the clipboard, and no
  bidirectional overrides that make text read differently than it is.
- :func:`md` for Markdown: no links, images or HTML that a preview would load
  or render (an image is a beacon telling the sender their tip was read).
- :func:`csv_cell` for spreadsheets: nothing Excel or Sheets runs as a formula.
- :func:`no_markup` for map pin details, which Google Earth and geojson.io
  render as HTML.
"""
from __future__ import annotations

import re
from typing import Any

# C0 controls except tab and newline, DEL, C1 controls, and the bidirectional overrides and isolates.
_UNPRINTABLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~])")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")


def printable(value: Any) -> str:
    """Text safe to print to a terminal. Control characters become a visible ``?``; tab and newline stay."""
    return _UNPRINTABLE.sub("?", str(value))


_SGR = re.compile("(\x1b\\[[0-9;]*m)")


def terminal_safe(value: Any) -> str:
    """The CLI's last line of defence: keep plain colour codes (ArkAngel's own styling), neutralize every other
    control sequence. Untrusted text is also cleaned with :func:`printable` before it's styled, so this only
    matters if a new command forgets to."""
    parts = _SGR.split(str(value))
    return "".join(part if index % 2 else printable(part) for index, part in enumerate(parts))


def printable_tree(value: Any) -> Any:
    """:func:`printable` applied to every string in nested dicts and lists (a report, before it's rendered)."""
    if isinstance(value, str):
        return printable(value)
    if isinstance(value, dict):
        return {key: printable_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [printable_tree(item) for item in value]
    return value


def md(value: Any) -> str:
    """Untrusted text for Markdown: every character that could start a link, image, HTML tag or heading is escaped."""
    text = " ".join(printable(value).split("\n"))
    return _MD_SPECIAL.sub(r"\\\1", text)


def md_url(url: str) -> str:
    """A URL safe inside ``[text](url)``: only http(s), with the characters that could end the link encoded."""
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return ""
    return re.sub(r"[\s()<>\[\]\\`\"']", lambda m: f"%{ord(m.group(0)):02X}", url)


def csv_cell(value: Any) -> Any:
    """A spreadsheet cell that can't run as a formula (OWASP CSV injection). Plain numbers pass through."""
    if not isinstance(value, str) or not value or _NUMBER.match(value):
        return value
    return "'" + value if value.startswith(_FORMULA_START) else value


def no_markup(value: Any) -> str:
    """Text for map pins: angle brackets become look-alikes, so a viewer that renders HTML shows them as text."""
    return printable(value).replace("<", "‹").replace(">", "›")
