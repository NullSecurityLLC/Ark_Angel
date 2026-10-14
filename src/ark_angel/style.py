"""Terminal styling: one amber accent, hierarchy through brightness.

ANSI 256-colour 179 (#d7af5f) is the closest terminal match to the HTML
report's amber (#e0b25c). Findings are bright, numbers and headings amber,
reasons dim. Colour follows click's rules (stripped when output isn't a
terminal) plus ``NO_COLOR``/``FORCE_COLOR`` and ``--no-color``. Bars fall back
to ASCII when the console encoding can't show block characters.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Optional

import click

AMBER = 179
_BLOCKS = " ▏▎▍▌▋▊▉█"


def _console_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "█▏│·─".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


@dataclass(frozen=True)
class Style:
    """Text decorations; ``Style(color=False, unicode=False)`` returns text unchanged."""

    color: bool = True
    unicode: bool = True
    width: Optional[int] = None  # terminal columns; None means never truncate

    def fit(self, text: str, indent: int) -> str:
        """Cut ``text`` to the terminal width (after ``indent`` columns), ending in an ellipsis."""
        if not self.width or len(text) + indent <= self.width:
            return text
        room = max(self.width - indent - 1, 10)
        return text[:room] + ("…" if self.unicode else "~")

    def _paint(self, text: str, **kwargs) -> str:
        return click.style(text, **kwargs) if self.color else text

    def accent(self, text: str) -> str:
        return self._paint(text, fg=AMBER)

    def heading(self, text: str) -> str:
        return self._paint(text.upper(), fg=AMBER, bold=True)

    def strong(self, text: str) -> str:
        return self._paint(text, bold=True)

    def dim(self, text: str) -> str:
        return self._paint(text, dim=True)

    @property
    def dot(self) -> str:
        return "·" if self.unicode else "-"

    @property
    def rule(self) -> str:
        return "─" if self.unicode else "-"

    @property
    def gutter(self) -> str:
        return "│" if self.unicode else "|"

    def bar(self, value: float, top: float, width: int = 10) -> str:
        """A fixed-width bar for ``value`` out of ``top``: ``█████▌    `` or ``#####     ``."""
        fraction = 0.0 if top <= 0 else max(0.0, min(value / top, 1.0))
        if not self.unicode:
            filled = round(fraction * width)
            return self.accent("#" * filled) + " " * (width - filled)
        eighths = round(fraction * width * 8)
        full, part = divmod(eighths, 8)
        text = "█" * full + (_BLOCKS[part] if part and full < width else "")
        return self.accent(text) + " " * (width - len(text))


PLAIN = Style(color=False, unicode=True)


def terminal_style() -> Style:
    """The style for output going to the current terminal."""
    width = shutil.get_terminal_size(fallback=(0, 0)).columns if sys.stdout.isatty() else 0
    return Style(color=True, unicode=_console_unicode(), width=width or None)
