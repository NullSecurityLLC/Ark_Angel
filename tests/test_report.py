import json

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
    assert "[10.0] Phone matches" in render_text(report)
    assert "## Ranked leads" in render_markdown(report)
    assert json.loads(render_json(report))["case"]["identifier"] == "C1"


def test_html_is_escaped_and_self_contained() -> None:
    page = render_html(build_report(_case()), notice="synthetic")
    assert "<script>alert(1)</script>" not in page
    assert "Case &lt;One&gt;" in page
    assert "Single-source signals (1)" in page
    assert "<link" not in page and "<script" not in page
