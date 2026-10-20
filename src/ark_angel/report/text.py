"""Plain-text and Markdown renderers."""
from __future__ import annotations

import json
from typing import Any

from ark_angel.report.gis import google_maps_url, street_view_url
from ark_angel.style import PLAIN, Style


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


_ROW_INDENT = " " * 26  # lines under a lead row start under its summary


def top_score(report: dict[str, Any]) -> float:
    return max((lead["score"] for lead in report["leads"]), default=0.0)


def lead_lines(lead: dict[str, Any], top: float, style: Style = PLAIN, reasons: int | None = None,
               label: str | None = None) -> list[str]:
    """One lead as an aligned row (number, score, bar, finding), its review state and its reasons."""
    label = label or f"#{lead['rank']}"
    lines = [f"  {style.accent(f'{label:<5}')}{lead['score']:5.1f}  {style.bar(lead['score'], top)}  "
             f"{style.strong(lead['summary'])}"]
    status = review_line(lead)
    if status:
        lines.append(_ROW_INDENT + (style.accent(status) if lead["resurfaced"] else status))
    shown = lead["reasons"] if reasons is None else lead["reasons"][:reasons]
    lines += [_ROW_INDENT + style.dim(style.fit(f"{style.gutter} {reason}", len(_ROW_INDENT))) for reason in shown]
    return lines


def case_header(report: dict[str, Any], style: Style = PLAIN) -> list[str]:
    case = report["case"]
    dot = style.dot
    opened = case["created_at"][:16].replace("T", " ")
    return [
        style.heading(f"ArkAngel {dot} case file {case['identifier']}"),
        style.strong(case["title"]),
        style.dim(f"opened {opened} {dot} {case['evidence_count']} evidence items {dot} "
                  f"{len(case['sources'])} sources {dot} {case['lead_count']} leads"),
    ]


def render_text(report: dict[str, Any], limit: int | None = None, reasons: bool = True,
                show_dismissed: bool = False, style: Style = PLAIN) -> str:
    """The case report for a terminal (pass ``terminal_style()``) or a plain file (default)."""
    lines = case_header(report, style)
    if report["timeline"]:
        latest = report["timeline"][-1]
        lines += ["", style.heading("Latest dated activity"),
                  f"  {style.accent(latest['when'])}  {latest['source']}  {latest['text']}"]

    top = top_score(report)
    depth = None if reasons else 0
    verified, open_leads, dismissed = (_group(report, s) for s in ("verified", "open", "dismissed"))
    if verified:
        lines += ["", style.heading("Verified")]
        for lead in verified:
            lines += lead_lines(lead, top, style, depth)
    lines += ["", style.heading("Open leads")]
    if not open_leads:
        lines.append(style.dim("  none. Run `ark-angel enrich` after adding evidence."))
    for lead in open_leads[:limit]:
        lines += lead_lines(lead, top, style, depth)
    if limit is not None and len(open_leads) > limit:
        lines.append(style.dim(f"  … {len(open_leads) - limit} more (drop --top to see all)"))
    if dismissed:
        if show_dismissed:
            lines += ["", style.heading("Dismissed")]
            for lead in dismissed:
                lines += lead_lines(lead, top, style, depth)
        else:
            lines += ["", f"{style.heading('Dismissed')}  {len(dismissed)} hidden (--all to show)"]
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
        out += ["", "## Location clusters", "", "| Centre | Items | Spread | Map |", "|---|---|---|---|"]
        for c in report["clusters"]:
            lat, lon = c["latitude"], c["longitude"]
            out.append(f"| {lat:.5f}, {lon:.5f} | {len(c['points'])} | {c['radius_m']} m | "
                       f"[Google]({google_maps_url(lat, lon)}) · [Street View]({street_view_url(lat, lon)}) |")
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
