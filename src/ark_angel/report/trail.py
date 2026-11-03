"""Render the investigator's trail as text, Markdown or JSON."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Optional

from ark_angel.analysis.trail import Step, Trail
from ark_angel.style import PLAIN, Style

_ICONS = {"search": "search", "visit": "page", "clip": "clip"}


def _span(delta: Optional[timedelta]) -> str:
    if delta is None:
        return ""
    minutes = delta.total_seconds() / 60
    return f"{minutes:.0f} min" if minutes >= 1 else f"{delta.total_seconds():.0f} s"


def _key(key: tuple[str, str]) -> str:
    return f"{key[0]} {key[1]}"


def _step_notes(step: Step) -> list[str]:
    notes = []
    for key, seen in step.pivots.items():
        where = f"in {seen.label}" if seen.when is None else f"on {seen.label} at {seen.when:%H:%M}"
        notes.append(f"pivot: {_key(key)}, which you'd seen {where}")
    for key, ids in step.hits.items():
        notes.append(f"MATCH: {_key(key)} is also in {', '.join(ids[:4])}{' …' if len(ids) > 4 else ''}")
    return notes


def trail_dict(trail: Trail, case_id: str) -> dict[str, Any]:
    def step(s: Step) -> dict[str, Any]:
        return {"evidence_id": s.evidence_id, "kind": s.kind, "by": s.by, "when": s.when.isoformat(sep=" ", timespec="seconds"),
                "label": s.label, "host": s.host, "url": s.url,
                "dwell_seconds": round(s.dwell.total_seconds()) if s.dwell else None,
                "looked_up": [list(k) for k in s.looked_up], "found": [list(k) for k in s.found],
                "hits": [{"kind": k[0], "value": k[1], "evidence_ids": ids} for k, ids in s.hits.items()],
                "pivots": [{"kind": k[0], "value": k[1], "seen_in": seen.evidence_id, "label": seen.label}
                           for k, seen in s.pivots.items()]}

    return {
        "case": case_id,
        "sessions": [{"start": se.start.isoformat(sep=" ", timespec="minutes"),
                      "end": se.end.isoformat(sep=" ", timespec="minutes"),
                      "steps": [step(s) for s in se.steps]} for se in trail.sessions],
        "threads": [{"kind": k[0], "value": k[1], "steps": [s.evidence_id for s in run]} for k, run in trail.threads],
        "loose_ends": [{"kind": k[0], "value": k[1], "seen_in": s.evidence_id, "matches": ids}
                       for k, s, ids in trail.loose_ends],
        "domains": [{"host": host, "visits": n} for host, n in trail.domains],
    }


def render_trail_text(trail: Trail, case_id: str, style: Style = PLAIN, session: Optional[int] = None) -> str:
    dot = style.dot
    steps = trail.steps
    if not steps:
        return (f"No browsing recorded for case {case_id!r} yet. Run `ark-angel browse {case_id}` with the "
                "ArkAngel Recorder extension, or `ark-angel ingest-history` for after the fact.")
    searches = sum(1 for s in steps if s.kind == "search")
    matched = sum(1 for s in steps if s.hits)
    lines = [style.heading(f"ArkAngel {dot} investigator trail {dot} {case_id}"),
             style.dim(f"{len(trail.sessions)} session(s) {dot} {len(steps) - searches} pages {dot} "
                       f"{searches} searches {dot} {matched} page(s) matching the case")]
    if trail.domains:
        lines.append(style.dim("most visited: " + ", ".join(f"{h} ({n})" for h, n in trail.domains[:6])))

    lines += ["", style.heading("Threads you followed")]
    if not trail.threads:
        lines.append(style.dim("  none yet: nothing was looked up more than once"))
    for key, run in trail.threads[:8]:
        chain = f" {'→' if style.unicode else '->'} ".join(f"{s.when:%H:%M} {_ICONS[s.kind]}" for s in run)
        lines.append(f"  {style.strong(_key(key))}  {style.dim(chain)}")

    lines += ["", style.heading("Loose ends")]
    if not trail.loose_ends:
        lines.append(style.dim("  none: everything you clipped or matched, you followed up"))
    for key, seen, ids in trail.loose_ends[:10]:
        why = f"matches {', '.join(ids[:3])}" if ids else "you clipped it"
        lines.append(f"  {style.accent(_key(key))}  seen {seen.when:%m-%d %H:%M} on {seen.host or seen.label}"
                     f" {dot} {why} {dot} {style.dim('never looked up')}")

    for number, se in enumerate(trail.sessions, start=1):
        if session is not None and number != session:
            continue
        lines += ["", style.heading(f"Session {number}") + style.dim(f"  {se.start:%Y-%m-%d %H:%M}–{se.end:%H:%M}")]
        for s in se.steps:
            dwell = _span(s.dwell)
            head = f"  {style.accent(f'{s.when:%H:%M}')}  {_ICONS[s.kind]:<6} "
            body = f'"{s.label}"' if s.kind == "search" else s.label
            where = f"  {style.dim(s.host)}" if s.host and s.kind != "search" else ""
            lines.append(head + style.strong(body) + where + (style.dim(f"  {dwell}") if dwell else ""))
            for note in _step_notes(s):
                lines.append(" " * 17 + (style.accent(note) if note.startswith("MATCH") else style.dim(note)))
    return "\n".join(lines)


def render_trail_markdown(trail: Trail, case_id: str) -> str:
    out = [f"# Investigator trail: {case_id}", ""]
    if trail.threads:
        out += ["## Threads you followed", ""]
        out += [f"- **{_key(k)}**: " + " → ".join(f"{s.when:%H:%M} {_ICONS[s.kind]} ({s.evidence_id})" for s in run)
                for k, run in trail.threads]
        out.append("")
    if trail.loose_ends:
        out += ["## Loose ends", ""]
        out += [f"- **{_key(k)}**, seen {s.when:%Y-%m-%d %H:%M} on {s.host}"
                + (f", matches {', '.join(ids)}" if ids else ", clipped") for k, s, ids in trail.loose_ends]
        out.append("")
    for number, se in enumerate(trail.sessions, start=1):
        out += [f"## Session {number}: {se.start:%Y-%m-%d %H:%M}–{se.end:%H:%M}", "",
                "| Time | What | Where | Time spent | Notes |", "|---|---|---|---|---|"]
        for s in se.steps:
            label = s.label.replace("|", "\\|")
            what = f"{_ICONS[s.kind]}: [{label}]({s.url})" if s.url and s.kind != "search" else f"{_ICONS[s.kind]}: {label}"
            notes = "; ".join(_step_notes(s)).replace("|", "\\|")
            out.append(f"| {s.when:%H:%M} | {what} | {s.host} | {_span(s.dwell)} | {notes} |")
        out.append("")
    return "\n".join(out) + "\n"


def render_trail_json(trail: Trail, case_id: str) -> str:
    return json.dumps(trail_dict(trail, case_id), indent=2)
