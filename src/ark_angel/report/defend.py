"""The principal-defender briefing: risk level, threats, exposures and what to fix."""
from __future__ import annotations

import json
from typing import Any

from ark_angel.analysis.defend import risk_level
from ark_angel.models import Case
from ark_angel.report.build import build_report
from ark_angel.report.text import lead_lines, review_line, top_score
from ark_angel.style import PLAIN, Style

_FIX = "Fix: "
_INDENT = " " * 26  # under a lead row's summary, like the case report


def build_brief(case: Case) -> dict[str, Any]:
    """The ordinary report plus the principal, an overall risk level, and leads split into threats and exposures."""
    if case.principal is None:
        raise ValueError(f"case {case.identifier!r} isn't a principal-defender case")
    report = build_report(case)
    p = case.principal
    statuses = {lead["identifier"]: lead["status"] for lead in report["leads"]}
    level, why = risk_level([lead for lead in case.leads if lead.source == "analysis"], statuses)
    report["principal"] = {
        "name": p.name, "aliases": list(p.aliases), "phones": list(p.phones), "emails": list(p.emails),
        "handles": list(p.handles), "plates": list(p.plates), "authorized_by": p.authorized_by,
        "created_at": p.created_at,
        "sensitive_places": [{"name": pl.name, "latitude": pl.latitude, "longitude": pl.longitude,
                              "aliases": list(pl.aliases), "radius_m": pl.radius_m} for pl in p.places],
    }
    report["risk"] = {"level": level, "why": why}
    for lead in report["leads"]:
        lead["fixes"] = [r[len(_FIX):] for r in lead["reasons"] if r.startswith(_FIX)]
        lead["kind"] = ("ingest" if lead["source"] != "analysis"
                        else "threat" if lead["identifier"].startswith("threat-")
                        else "pattern" if lead["identifier"].startswith("geo-") else "exposure")
    return report


def _split(brief: dict[str, Any]) -> tuple[list, list, list]:
    # Ingest-time leads (raw EXIF findings, username hits) are in `lead list`; the exposures cover them.
    leads = [lead for lead in brief["leads"] if lead["kind"] != "ingest"]
    live = [lead for lead in leads if lead["status"] != "dismissed"]
    return ([lead for lead in live if lead["kind"] == "threat"],
            [lead for lead in live if lead["kind"] != "threat"],
            [lead for lead in leads if lead["status"] == "dismissed"])


def render_brief_text(brief: dict[str, Any], style: Style = PLAIN, reasons: bool = True,
                      show_dismissed: bool = False) -> str:
    p, risk, case = brief["principal"], brief["risk"], brief["case"]
    dot = style.dot
    lines = [
        style.heading(f"ArkAngel {dot} principal defender {dot} {case['identifier']}"),
        style.strong(case["title"]),
        style.dim(f"protecting {p['name']} {dot} authorized by {p['authorized_by']} {dot} "
                  f"{case['evidence_count']} evidence items {dot} {len(p['sensitive_places'])} sensitive places"),
        "",
        f"{style.heading('Risk')}  {style.accent(style.strong(risk['level']))}  {risk['why']}",
    ]
    threats, exposures, dismissed = _split(brief)
    top = top_score(brief)
    for title, leads, empty in (("Threats", threats, "no threatening messages found"),
                                ("Exposures", exposures, "nothing exposed in the evidence so far")):
        lines += ["", style.heading(title)]
        if not leads:
            lines.append(style.dim(f"  {empty}"))
        for lead in leads:
            lines += lead_lines(lead, top, style, 0)
            if reasons:
                lines += [_INDENT + style.dim(style.fit(f"{style.gutter} {reason}", len(_INDENT)))
                          for reason in lead["reasons"] if not reason.startswith(_FIX)]
            lines += [_INDENT + style.accent(f"{'→' if style.unicode else '->'} {fix}") for fix in lead["fixes"]]
    if dismissed:
        if show_dismissed:
            lines += ["", style.heading("Dismissed / remediated")]
            for lead in dismissed:
                lines += lead_lines(lead, top, style, 0)
        else:
            lines += ["", f"{style.heading('Dismissed / remediated')}  {len(dismissed)} hidden (--all to show)"]
    return "\n".join(lines)


def render_brief_markdown(brief: dict[str, Any]) -> str:
    p, risk, case = brief["principal"], brief["risk"], brief["case"]
    out = [f"# {case['title']} ({case['identifier']})", "",
           f"**Risk: {risk['level']}**: {risk['why']}", "",
           f"Protecting **{p['name']}**, authorized by {p['authorized_by']}. "
           f"{case['evidence_count']} evidence items, {len(p['sensitive_places'])} sensitive places.", ""]
    threats, exposures, dismissed = _split(brief)
    for title, leads in (("Threats", threats), ("Exposures", exposures), ("Dismissed / remediated", dismissed)):
        if not leads and title.startswith("Dismissed"):
            continue
        out += [f"## {title}", ""]
        if not leads:
            out += ["None found.", ""]
        for lead in leads:
            out.append(f"{lead['rank']}. **[{lead['score']:.1f}] {lead['summary']}**")
            if review_line(lead):
                out.append(f"   - *{review_line(lead)}*")
            out += [f"   - {reason}" for reason in lead["reasons"] if not reason.startswith(_FIX)]
            out += [f"   - **Fix:** {fix}" for fix in lead["fixes"]]
        out.append("")
    return "\n".join(out) + "\n"


def render_brief_json(brief: dict[str, Any]) -> str:
    return json.dumps(brief, indent=2)
