"""Self-contained HTML case file: no external requests, opens offline."""
from __future__ import annotations

import math
from html import escape
from typing import Any, Optional

from ark_angel.report.text import review_line

_CSS = """
:root{
  --bg:#14120e;--surface:#1c1a15;--surface-2:#25221b;--text:#ece6d8;--muted:#a59c88;
  --line:rgba(255,236,200,.12);--accent:#e0b25c;--accent-soft:rgba(224,178,92,.16);--dot:#8d8471;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){
  --bg:#f3efe6;--surface:#fffdf8;--surface-2:#e9e3d6;--text:#1d1b16;--muted:#5b5444;
  --line:rgba(45,32,8,.15);--accent:#8a5a00;--accent-soft:rgba(138,90,0,.10);--dot:#857c69;
}}
:root[data-theme="light"]{
  --bg:#f3efe6;--surface:#fffdf8;--surface-2:#e9e3d6;--text:#1d1b16;--muted:#5b5444;
  --line:rgba(45,32,8,.15);--accent:#8a5a00;--accent-soft:rgba(138,90,0,.10);--dot:#857c69;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 var(--sans)}
main{max-width:1080px;margin:0 auto;padding:40px 16px 64px}
.label{font:600 11px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(26px,4vw,38px);line-height:1.15;margin:10px 0 14px;letter-spacing:-.01em}
h2{font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  margin:0 0 16px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.meta{display:flex;flex-wrap:wrap;gap:6px 22px;font:13px/1.4 var(--mono);color:var(--muted)}
.meta b{color:var(--text);font-weight:600}
.notice{margin:22px 0 0;padding:10px 14px;border-left:3px solid var(--accent);background:var(--accent-soft);
  font-size:13.5px}
section{margin-top:44px}
ol.leads{list-style:none;margin:0;padding:0}
.lead{display:grid;grid-template-columns:44px minmax(0,1fr);gap:4px 14px;padding:16px 0;border-bottom:1px solid var(--line)}
.rank{font:600 18px/1.2 var(--mono);color:var(--muted);text-align:right}
.lead:nth-child(-n+3) .rank{color:var(--accent)}
.lead-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px}
.summary{font-weight:600;font-size:16px;overflow-wrap:anywhere}
.score{font:600 13px/1 var(--mono);color:var(--accent);white-space:nowrap}
.bar{grid-column:2;height:3px;background:var(--surface-2);border-radius:2px;overflow:hidden;max-width:420px}
.bar i{display:block;height:100%;background:var(--accent)}
details{grid-column:2}
summary{cursor:pointer;color:var(--muted);font:12px/1.6 var(--mono);list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"+ ";}
details[open] summary::before{content:"- ";}
.reasons{margin:6px 0 0;padding:0;list-style:none;font:12.5px/1.6 var(--mono);color:var(--muted)}
.reasons li{padding:3px 0 3px 12px;border-left:1px solid var(--line);overflow-wrap:anywhere}
.reasons li:first-child{color:var(--text)}
.chips{grid-column:2;display:flex;flex-wrap:wrap;gap:6px}
.chip{font:11.5px/1 var(--mono);padding:4px 7px;border:1px solid var(--line);border-radius:3px;color:var(--muted);
  background:var(--surface)}
.split{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:40px}
@media (max-width:820px){.split{grid-template-columns:1fr}}
.plot{background:var(--surface);border:1px solid var(--line);border-radius:4px}
.plot svg{display:block;width:100%;height:auto}
.plot text{font:11px var(--mono);fill:var(--muted)}
.plot text.place-label{fill:var(--text)}
.grid line{stroke:var(--line)}
.pt{fill:var(--dot)}
.pt.hot{fill:var(--accent)}
.ring{fill:var(--accent-soft);stroke:var(--accent);stroke-dasharray:3 3}
.clusters{margin:14px 0 0;padding:0;list-style:none;font:13px/1.5 var(--mono)}
.clusters li{padding:8px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px}
.clusters a{color:var(--accent)}
.timeline{list-style:none;margin:0;padding:0}
.timeline li{display:grid;grid-template-columns:118px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);
  font-size:13.5px}
.timeline time{font:12px/1.5 var(--mono);color:var(--muted)}
.timeline .src{font:11px/1 var(--mono);color:var(--accent);margin-right:6px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font:600 11px/1.2 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  background:var(--surface-2)}
td code{font:12.5px var(--mono)}
tr.multi td:first-child{box-shadow:inset 3px 0 var(--accent)}
td.count{font-family:var(--mono);text-align:right}
footer{margin-top:56px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:16px}
.empty{color:var(--muted);font-style:italic}
h3{font:600 11px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:26px 0 2px}
h3:first-child{margin-top:0}
.review{grid-column:2;margin:4px 0 2px;padding:6px 10px;font:12.5px/1.5 var(--mono);border-left:2px solid var(--accent);
  background:var(--accent-soft);overflow-wrap:anywhere}
.review.dismissed{border-left-color:var(--muted);background:var(--surface-2)}
.review.resurfaced{border-left-style:dashed}
.leads.verified .rank{color:var(--accent)}
.dim{color:var(--muted);font:12px/1.5 var(--mono)}
.signals>summary{font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  padding:12px 0;border-bottom:1px solid var(--line)}
.signals td{overflow-wrap:anywhere}
td.score-cell{font-family:var(--mono);color:var(--accent);white-space:nowrap}
.plot-note{font:12px/1.5 var(--mono);color:var(--muted);margin:8px 0 0}
"""


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _plot(report: dict[str, Any], width: int = 560, height: int = 340) -> str:
    all_points = [(p, len(c["points"]) > 1) for c in report["clusters"] for p in c["points"]]
    if not all_points:
        return '<p class="empty">No geotagged evidence yet.</p>'
    # Frame the busiest cluster so a single far-off point can't squash it to a dot.
    focus = max(report["clusters"], key=lambda c: len(c["points"]))
    framed = focus["points"] if len(focus["points"]) > 1 else [p for p, _ in all_points]
    frame_ids = {id(p) for p in framed}
    points = [(p, hot) for p, hot in all_points if id(p) in frame_ids]
    outside = len(all_points) - len(points)
    lats = [p["latitude"] for p, _ in points]
    lons = [p["longitude"] for p, _ in points]
    mid_lat = (max(lats) + min(lats)) / 2
    kx = math.cos(math.radians(mid_lat))  # equirectangular: shrink longitude by latitude
    # 1.6x headroom so the cluster ring and edge points sit inside the frame.
    span_x = max((max(lons) - min(lons)) * kx, 0.004) * 1.6
    span_y = max(max(lats) - min(lats), 0.004) * 1.6
    pad = 36
    scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
    cx = (max(lons) + min(lons)) / 2
    cy = mid_lat

    def xy(lat: float, lon: float) -> tuple[float, float]:
        return width / 2 + (lon - cx) * kx * scale, height / 2 - (lat - cy) * scale

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Plot of geotagged evidence">',
             '<g class="grid">']
    for i in range(1, 6):
        parts.append(f'<line x1="{width * i / 6:.1f}" y1="0" x2="{width * i / 6:.1f}" y2="{height}"/>')
    for i in range(1, 4):
        parts.append(f'<line x1="0" y1="{height * i / 4:.1f}" x2="{width}" y2="{height * i / 4:.1f}"/>')
    parts.append("</g>")
    for cluster in report["clusters"]:
        if len(cluster["points"]) < 2:
            continue
        x, y = xy(cluster["latitude"], cluster["longitude"])
        r = max(cluster["radius_m"] / 1000 / 111.32 * scale + 10, 14)
        parts.append(f'<circle class="ring" cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}"/>')
    labelled: set[str] = set()
    for point, hot in points:
        x, y = xy(point["latitude"], point["longitude"])
        named = point.get("place")
        shape = "rect" if named else "circle"
        geometry = f'x="{x - 4:.1f}" y="{y - 4:.1f}" width="8" height="8"' if named else f'cx="{x:.1f}" cy="{y:.1f}" r="4"'
        parts.append(f'<{shape} class="pt{" hot" if hot else ""}" {geometry}>'
                     f'<title>{_e(point["evidence_id"])}{" names " + _e(named) if named else ""} · '
                     f'{point["latitude"]:.5f}, {point["longitude"]:.5f}</title></{shape}>')
        if named and named not in labelled:
            labelled.add(named)
            parts.append(f'<text class="place-label" x="{x + 9:.1f}" y="{y + 4:.1f}">{_e(named)}</text>')
    km_per_px = 111.32 / scale
    bar_km = _nice(km_per_px * 100)
    bar_px = bar_km / km_per_px
    parts.append(f'<line x1="14" y1="{height - 16}" x2="{14 + bar_px:.1f}" y2="{height - 16}" '
                 'stroke="currentColor" style="stroke:var(--muted)"/>')
    parts.append(f'<text x="14" y="{height - 22}">{bar_km:g} km</text>')
    parts.append("</svg>")
    note = ('<p class="plot-note">Squares are places named in evidence; dots are GPS or dropped pins.'
            + (f" Framed on the largest cluster; {outside} other point(s) are outside this view, listed below."
               if outside else "") + "</p>")
    return f'<div class="plot">{"".join(parts)}</div>{note}'


def _nice(value: float) -> float:
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def _lead_card(lead: dict[str, Any], top: float, expanded: bool) -> str:
    width = max(lead["score"] / top * 100, 2)
    reasons = "".join(f"<li>{_e(r)}</li>" for r in lead["reasons"])
    chips = "".join(f'<span class="chip">{_e(eid)}</span>' for eid in lead["evidence_ids"][:8])
    chips_html = f'<div class="chips">{chips}</div>' if chips else ""
    details = (f'<details{" open" if expanded else ""}><summary>why this ranks here</summary>'
               f'<ul class="reasons">{reasons}</ul></details>') if reasons else ""
    status = review_line(lead)
    kind = "resurfaced" if lead["resurfaced"] else lead["status"]
    review_html = f'<p class="review {kind}">{_e(status)}</p>' if status else ""
    return (
        f'<li class="lead"><div class="rank">#{lead["rank"]}</div>'
        f'<div class="lead-head"><span class="summary">{_e(lead["summary"])}</span>'
        f'<span class="score">{lead["score"]:.1f}</span></div>'
        f'<div class="bar"><i style="width:{width:.0f}%"></i></div>'
        f"{review_html}{details}{chips_html}</li>"
    )


def _rows_table(title: str, rows: list[str]) -> str:
    return (f'<details class="signals"><summary>{_e(title)} ({len(rows)})</summary>'
            '<div class="table-wrap"><table><tbody>' + "".join(rows) + "</tbody></table></div></details>")


def _leads_html(leads: list[dict[str, Any]]) -> str:
    """Verified leads first, then open cross-source leads, then collapsed signals and dismissals."""
    top = max((lead["score"] for lead in leads), default=0) or 1
    verified = [lead for lead in leads if lead["status"] == "verified"]
    open_leads = [lead for lead in leads if lead["status"] == "open"]
    correlated = [lead for lead in open_leads if lead["correlated"] or lead["resurfaced"]]
    signals = [lead for lead in open_leads if lead not in correlated]
    dismissed = [lead for lead in leads if lead["status"] == "dismissed"]

    parts = []
    if verified:
        parts.append('<h3>Verified</h3><ol class="leads verified">'
                     + "".join(_lead_card(lead, top, expanded=False) for lead in verified) + "</ol>")
        parts.append("<h3>Open</h3>")
    if correlated:
        parts.append('<ol class="leads">'
                     + "".join(_lead_card(lead, top, expanded=i < 3) for i, lead in enumerate(correlated)) + "</ol>")
    else:
        parts.append('<p class="empty">No open cross-source leads. Run enrich after adding evidence.</p>')
    if signals:
        parts.append(_rows_table("Single-source signals", [
            f'<tr><td class="score-cell">#{lead["rank"]}</td><td class="score-cell">{lead["score"]:.1f}</td>'
            f'<td>{_e(lead["summary"])}</td></tr>' for lead in signals]))
    if dismissed:
        parts.append(_rows_table("Dismissed", [
            f'<tr><td class="score-cell">#{lead["rank"]}</td><td>{_e(lead["summary"])}'
            f'<br><span class="dim">{_e(review_line(lead))}</span></td></tr>' for lead in dismissed]))
    return "".join(parts)


def render_html(report: dict[str, Any], notice: Optional[str] = None) -> str:
    case = report["case"]
    leads = report["leads"]

    cluster_items = []
    for c in sorted(report["clusters"], key=lambda c: -len(c["points"])):
        osm = (f"https://www.openstreetmap.org/?mlat={c['latitude']:.5f}&amp;mlon={c['longitude']:.5f}"
               f"#map=16/{c['latitude']:.5f}/{c['longitude']:.5f}")
        cluster_items.append(
            f"<li><span>{_e(', '.join(c['places'])) + '<br>' if c['places'] else ''}"
            f"{c['latitude']:.5f}, {c['longitude']:.5f}<br>"
            f"<span class=label>{len(c['points'])} item(s) · {c['radius_m']} m spread</span></span>"
            f'<a href="{osm}" target="_blank" rel="noopener">map</a></li>'
        )

    timeline_items = "".join(
        f'<li><time>{_e(ev["when"])}</time><span><span class="src">{_e(ev["source"])}</span>{_e(ev["text"])}</span></li>'
        for ev in report["timeline"]
    ) or '<li class="empty">No dated evidence yet.</li>'

    entity_rows = "".join(
        f'<tr class="{"multi" if len(ent["mentions"]) > 1 else ""}"><td>{_e(ent["kind"])}</td>'
        f'<td><code>{_e(ent["value"])}</code></td><td class="count">{len(ent["mentions"])}</td>'
        f'<td>{_e(", ".join(m["evidence_id"] for m in ent["mentions"]))}</td></tr>'
        for ent in report["entities"]
    )

    notice_html = f'<p class="notice">{_e(notice)}</p>' if notice else ""
    leads_html = _leads_html(leads)
    if entity_rows:
        entities_html = ('<div class="table-wrap"><table><thead><tr><th>Kind</th><th>Value</th><th>Seen</th>'
                         "<th>Evidence</th></tr></thead><tbody>" + entity_rows + "</tbody></table></div>")
    else:
        entities_html = '<p class="empty">No entities extracted.</p>'
    clusters_html = "".join(cluster_items)
    opened = _e(case["created_at"][:16].replace("T", " "))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(case["title"])} · ArkAngel</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header>
<div class="label">ArkAngel · case file {_e(case["identifier"])}</div>
<h1>{_e(case["title"])}</h1>
<div class="meta">
<span>opened <b>{opened}</b></span>
<span><b>{case["evidence_count"]}</b> evidence items</span>
<span><b>{len(case["sources"])}</b> sources</span>
<span><b>{len(leads)}</b> leads</span>
</div>
{notice_html}
</header>

<section>
<h2>Cross-source leads</h2>
{leads_html}
</section>

<section class="split">
<div>
<h2>Locations</h2>
{_plot(report)}
<ul class="clusters">{clusters_html}</ul>
</div>
<div>
<h2>Timeline</h2>
<ol class="timeline">{timeline_items}</ol>
</div>
</section>

<section>
<h2>Entity index</h2>
{entities_html}
</section>

<footer>Leads point at where a human should look next. They are not findings. Verify everything before acting on it.</footer>
</main>
</body>
</html>
"""
