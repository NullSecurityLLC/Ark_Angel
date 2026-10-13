"""Plain-text and Markdown renderers."""
from __future__ import annotations

import json
from typing import Any


def render_text(report: dict[str, Any], limit: int | None = None, reasons: bool = True) -> str:
    case = report["case"]
    lines = [
        f"Case: {case['title']} ({case['identifier']})",
        f"Opened: {case['created_at']}",
        f"Evidence items: {case['evidence_count']} from {len(case['sources'])} source(s)",
        "Leads:",
    ]
    for lead in report["leads"][:limit]:
        lines.append(f"  [{lead['score']:.1f}] {lead['summary']}")
        if reasons:
            lines += [f"         - {reason}" for reason in lead["reasons"]]
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    case = report["case"]
    out = [
        f"# {case['title']} ({case['identifier']})",
        "",
        f"Opened {case['created_at']} · {case['evidence_count']} evidence items · "
        f"sources: {', '.join(case['sources']) or 'none'}",
        "",
        "## Ranked leads",
        "",
    ]
    for rank, lead in enumerate(report["leads"], start=1):
        out.append(f"{rank}. **[{lead['score']:.1f}] {lead['summary']}**")
        out += [f"   - {reason}" for reason in lead["reasons"]]
    if report["clusters"]:
        out += ["", "## Location clusters", "", "| Centre | Items | Spread |", "|---|---|---|"]
        for c in report["clusters"]:
            out.append(f"| {c['latitude']:.5f}, {c['longitude']:.5f} | {len(c['points'])} | {c['radius_m']} m |")
    if report["timeline"]:
        out += ["", "## Timeline", ""]
        out += [f"- `{e['when']}` **{e['source']}**: {e['text']}" for e in report["timeline"]]
    if report["entities"]:
        out += ["", "## Entity index", "", "| Kind | Value | Seen in |", "|---|---|---|"]
        for e in report["entities"]:
            seen = ", ".join(m["evidence_id"] for m in e["mentions"])
            out.append(f"| {e['kind']} | `{e['value']}` | {seen} |")
    return "\n".join(out) + "\n"


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2)
