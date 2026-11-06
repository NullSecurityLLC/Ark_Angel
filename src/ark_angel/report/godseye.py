"""Render God's Eye's emergency briefing: terminal, Markdown (prints well, hands to police) or JSON."""
from __future__ import annotations

import json
from typing import Any

from ark_angel.style import PLAIN, Style
from ark_angel.textsafe import md, md_url, printable_tree

_PRIORITY = {1: "NOW", 2: "LAST SEEN", 3: "THREAT KNOWS", 4: "SENSITIVE", 5: "REGULAR"}


def render_godseye_text(data: dict[str, Any], style: Style = PLAIN) -> str:
    data = printable_tree(data)
    proto, p, case = data["protocol"], data["principal"], data["case"]
    dot = style.dot
    state = proto["state"].upper()
    lines = [style.heading(f"ArkAngel {dot} God's Eye {dot} {case['identifier']}"),
             style.strong(case["title"]),
             f"{style.heading('Status')}  {style.accent(style.strong(state))}"
             + (f"  {proto['trigger']} at {proto['activated_at']} UTC" if proto["state"] == "active" else "")]
    if proto["reason"]:
        lines.append(style.dim(f"  {proto['reason']}"))
    if proto["state"] != "active":
        lines.append(style.dim("  Not activated. This is what the briefing would say right now."))

    lines += ["", style.heading("Do now")]
    lines += [f"  {n}. {action}" for n, action in enumerate(data["actions"], start=1)]

    lines += ["", style.heading("Where to look first") + style.dim(f"  (as of {data['generated_local']} local time)")]
    if not data["places"]:
        lines.append(style.dim("  No places in the evidence yet."))
    for n, place in enumerate(data["places"], start=1):
        tag = _PRIORITY.get(place["priority"], "")
        lines.append(f"  {n}. {style.accent(f'[{tag}]')} {style.strong(place['label'])}  "
                     f"{place['latitude']:.5f}, {place['longitude']:.5f}")
        lines.append(style.dim(f"     {place['why']}"))
        lines.append(style.dim(f"     {place['map']}"))

    lines += ["", style.heading("Threats")]
    if not data["threats"]:
        lines.append(style.dim("  No threatening messages in the evidence."))
    for person in data["threats"]:
        flags = [flag for flag, on in (("ESCALATING", person["escalating"]),
                                       (f"KNOWS {', '.join(person['knows'])}", person["knows"])) if on]
        lines.append(f"  {style.strong(person['name'])}  {style.accent(' · '.join(flags))}".rstrip())
        lines.append(style.dim(f"     {person['summary']}"))
        if person["identifiers"]:
            lines.append(style.dim(f"     identifiers: {', '.join(person['identifiers'])}"))
        if person["latest"]:
            lines.append(style.dim(f"     last message {person['latest']}"))

    ident = [f"name {p['name']}" + (f" (also {', '.join(p['aliases'])})" if p["aliases"] else "")]
    ident += [f"{label} {', '.join(values)}" for label, values in (("phones", p["phones"]), ("emails", p["emails"]),
              ("handles", p["handles"]), ("plates", p["plates"])) if values]
    lines += ["", style.heading("The principal")] + [f"  {line}" for line in ident]
    if data["exposed"]:
        lines += ["", style.heading("What's exposed") + style.dim("  (what someone hostile may already know)")]
        lines += [style.dim(f"  - {item}") for item in data["exposed"]]

    lines += ["", style.heading("Integrity")]
    if proto["snapshot_sha256"]:
        same = proto["snapshot_sha256"] == proto["current_sha256"]
        lines.append(style.dim(f"  case at activation  {proto['snapshot_sha256']}"))
        lines.append(style.dim(f"  case now            {proto['current_sha256']}")
                     + ("" if same else style.accent("  (changed since: new evidence or reviews)")))
    else:
        lines.append(style.dim(f"  case now  {proto['current_sha256']}"))
    return "\n".join(lines)


def render_godseye_markdown(data: dict[str, Any]) -> str:
    proto, p, case = data["protocol"], data["principal"], data["case"]
    out = [f"# EMERGENCY BRIEFING: {md(case['title'])} ({md(case['identifier'])})", "",
           f"**God's Eye: {proto['state'].upper()}**"
           + (f", {md(proto['trigger'])} at {md(proto['activated_at'])} UTC" if proto["state"] == "active" else
              " (not activated: what the briefing would say now)"), ""]
    if proto["reason"]:
        out += [f"> {md(proto['reason'])}", ""]
    out += ["## Do now", ""] + [f"{n}. {md(a)}" for n, a in enumerate(data["actions"], start=1)] + [""]
    out += [f"## Where to look first (as of {md(data['generated_local'])} local time)", "",
            "| # | Place | Why | Map |", "|---|---|---|---|"]
    for n, place in enumerate(data["places"], start=1):
        out.append(f"| {n} | **{md(place['label'])}** {place['latitude']:.5f}, {place['longitude']:.5f} | "
                   f"{md(place['why'])} | [map]({md_url(place['map'])}) |")
    out += ["", "## Threats", ""]
    for person in data["threats"]:
        out.append(f"- **{md(person['name'])}**: {md(person['summary'])}"
                   + (f"; identifiers: {md(', '.join(person['identifiers']))}" if person["identifiers"] else "")
                   + (f"; knows: {md(', '.join(person['knows']))}" if person["knows"] else "")
                   + (f"; last message {md(person['latest'])}" if person["latest"] else ""))
    if not data["threats"]:
        out.append("None in the evidence.")
    out += ["", "## The principal", "", f"- Name: {md(p['name'])}" + (f" (also {md(', '.join(p['aliases']))})"
                                                                    if p["aliases"] else "")]
    out += [f"- {label}: {md(', '.join(values))}" for label, values in (
        ("Phones", p["phones"]), ("Emails", p["emails"]), ("Handles", p["handles"]), ("Plates", p["plates"])) if values]
    out += [f"- Protection authorized by: {md(p['authorized_by'])}", ""]
    if data["exposed"]:
        out += ["## What's exposed", ""] + [f"- {md(item)}" for item in data["exposed"]] + [""]
    out += ["## Integrity", "", f"- Case at activation: `{proto['snapshot_sha256'] or 'not activated'}`",
            f"- Case now: `{proto['current_sha256']}`", ""]
    return "\n".join(out)


def render_godseye_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2)
