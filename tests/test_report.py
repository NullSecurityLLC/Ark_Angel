import json
import re

from ark_angel.models import Case, Evidence, Lead
from ark_angel.report import build_report
from ark_angel.report.html import render_html
from ark_angel.report.text import render_json, render_markdown, render_text


def _case() -> Case:
    return Case(
        identifier="C1",
        title="Case <One>",
        evidence=[
            Evidence(identifier="tips:1", type="text", content="2026-10-01 call 303-555-0147",
                     metadata={"source": "tips"}),
            Evidence(identifier="notes:1", type="text", content="<script>alert(1)</script> 303.555.0147",
                     metadata={"source": "notes"}),
        ],
        leads=[
            Lead(identifier="corr-phone-3035550147", summary="Phone matches", score=10, source="analysis",
                 reasons=["seen twice"], evidence_ids=["tips:1", "notes:1"]),
            Lead(identifier="legacy", summary="email present in x"),
        ],
    )


def test_build_report_ranks_and_falls_back_to_rule_scores() -> None:
    report = build_report(_case())
    assert [lead["identifier"] for lead in report["leads"]] == ["corr-phone-3035550147", "legacy"]
    assert report["leads"][1]["score"] == 2.0
    assert report["leads"][0]["correlated"] is True
    assert report["entities"][0]["value"] == "3035550147"
    assert report["case"]["sources"] == ["notes", "tips"]


def test_text_markdown_and_json_render() -> None:
    report = build_report(_case())
    text = render_text(report)
    assert text.splitlines()[0] == "ARKANGEL · CASE FILE C1"
    assert re.search(r"^  #1 +10\.0  █+ +Phone matches$", text, re.MULTILINE)
    assert "│ seen twice" in text
    assert "LATEST DATED ACTIVITY" in text and "2026-10-01" in text
    assert "## Ranked leads" in render_markdown(report)
    assert json.loads(render_json(report))["case"]["identifier"] == "C1"


def test_html_is_escaped_and_self_contained() -> None:
    page = render_html(build_report(_case()), notice="synthetic")
    assert "<script>alert(1)</script>" not in page
    assert "Case &lt;One&gt;" in page
    assert "Single-source signals (1)" in page
    assert not re.search(r"<link[^>]+href=\"(?!data:)", page)  # only the embedded favicon
    assert "<script src" not in page and "@import" not in page  # opens offline
    assert '<link rel="icon" type="image/png" href="data:image/png;base64,' in page
    assert page.count("<script>") == 1  # only the inline print helper


def test_style_bars_colour_and_fallbacks() -> None:
    from ark_angel.style import PLAIN, Style

    assert PLAIN.bar(5, 10) == "█████     "
    assert PLAIN.bar(10, 10) == "██████████" and PLAIN.bar(0, 10) == " " * 10
    assert len(PLAIN.bar(3.3, 10)) == 10  # partial blocks keep the width
    ascii_style = Style(color=False, unicode=False)
    assert ascii_style.bar(5, 10) == "#####     " and ascii_style.gutter == "|"
    assert "\x1b[38;5;179m" in Style(color=True).accent("x")
    assert Style(color=False, width=20).fit("x" * 50, 5).endswith("…")
    assert len(Style(color=False, width=20).fit("x" * 50, 5)) == 15
    assert PLAIN.fit("x" * 500, 5) == "x" * 500  # no width: never truncated


def test_report_data_numbers_exhibits_in_evidence_order() -> None:
    report = build_report(_case())
    assert report["exhibits"] == {"tips:1": "EX-001", "notes:1": "EX-002"}
    assert [e["exhibit"] for e in report["evidence"]] == ["EX-001", "EX-002"]
    assert report["evidence"][0]["source"] == "tips"
    assert report["generated"]["version"]


def test_html_case_binder_sections() -> None:
    page = render_html(build_report(_case()))
    assert 'class="print-btn"' in page and "@media print" in page and "beforeprint" in page
    assert "Latest dated activity" in page and "Activity over time" in page
    assert '<span class="ex" title="tips:1">EX-001</span>' in page  # chips and reasons use exhibit numbers
    assert "<h2>Exhibits</h2>" in page and "<td>EX-002</td>" in page
    assert "Generated " in page and "by ArkAngel" in page


def test_exhibit_linking_matches_whole_ids_only() -> None:
    from ark_angel.report.html import _Exhibits

    ex = _Exhibits({"tips.txt:1": "EX-001", "tips.txt:10": "EX-010", "mail:a": "EX-003", "mail:a/1-x.jpg": "EX-004"})
    linked = ex.link("tips.txt:10 and tips.txt:1, then mail:a/1-x.jpg and mail:a.")
    assert linked.count(">EX-010<") == 1 and linked.count(">EX-001<") == 1
    assert linked.count(">EX-004<") == 1 and linked.count(">EX-003<") == 1
