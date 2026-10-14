"""Plain-text and Markdown renderers."""
from __future__ import annotations

import json
from typing import Any


def review_line(lead: dict[str, Any]) -> str:
    """One line describing a lead's review state, or ``""`` if never reviewed."""
    review = lead["review"]
    if not review:
        return ""
    who = f" by {review['reviewer']}" if review["reviewer"] else ""
    when = f" on {review['reviewed_at'][:10]}" if review["reviewed_at"] else ""
    note = f": {review['note']}" if review["note"] else ""
    new = lead["new_evidence_ids"]
    if lead["resurfaced"]:
        return (f"RESURFACED: dismissed{who}{when}{note}. "
                f"{len(new)} new item(s) since: {', '.join(new)}")
    line = f"{review['status']}{who}{when}{note}"
    if new:
        line += f" ({len(new)} new item(s) since: {', '.join(new)})"
    return line


def _group(report: dict[str, Any], status: str) -> list[dict[str, Any]]:
    return [lead for lead in report["leads"] if lead["status"] == status]


def render_text(report: dict[str, Any], limit: int | None = None, reasons: bool = True,
                show_dismissed: bool = False) -> str:
    case = report["case"]
    lines = [
        f"Case: {case['title']} ({case['identifier']})",
        f"Opened: {case['created_at']}",
        f"Evidence items: {case['evidence_count']} from {len(case['sources'])} source(s)",
    ]

    def emit(lead: dict[str, Any]) -> None:
        lines.append(f"  #{lead['rank']:<3} [{lead['score']:.1f}] {lead['summary']}")
        status = review_line(lead)
        if status:
            lines.append(f"         > {status}")
        if reasons:
            lines.extend(f"         - {reason}" for reason in lead["reasons"])

    verified, open_leads, dismissed = (_group(report, s) for s in ("verified", "open", "dismissed"))
    if verified:
        lines.append("Verified:")
        for lead in verified:
            emit(lead)
    lines.append("Leads:")
    for lead in open_leads[:limit]:
        emit(lead)
    if dismissed:
        if show_dismissed:
            lines.append("Dismissed:")
            for lead in dismissed:
                emit(lead)
        else:
            lines.append(f"Dismissed: {len(dismissed)} hidden (--all to show)")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    case = report["case"]
    out = [
        f"# {case['title']} ({case['identifier']})",
        "",
        f"Opened {case['created_at']} · {case['evidence_count']} evidence items · "
        f"sources: {', '.join(case['sources']) or 'none'}",
        "",
    ]
    for title, status in (("Verified", "verified"), ("Ranked leads", "open"), ("Dismissed", "dismissed")):
        leads = _group(report, status)
        if not leads and status != "open":
            continue
        out += [f"## {title}", ""]
        for lead in leads:
            out.append(f"{lead['rank']}. **[{lead['score']:.1f}] {lead['summary']}**")
            if review_line(lead):
                out.append(f"   - *{review_line(lead)}*")
            out += [f"   - {reason}" for reason in lead["reasons"]]
        out.append("")
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
