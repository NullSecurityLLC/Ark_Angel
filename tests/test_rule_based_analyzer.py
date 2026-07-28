from ark_angel.analysis.rules import RuleBasedAnalyzer
from ark_angel.models import Case, Evidence


def _case() -> Case:
    return Case(identifier="C1", title="Case One")


def test_tags_email_and_phone_and_skips_plain_text() -> None:
    analyzer = RuleBasedAnalyzer()
    evidence = [
        Evidence(identifier="E1", type="text", content="contact jane@example.com for details"),
        Evidence(identifier="E2", type="text", content="call 555-123-4567"),
        Evidence(identifier="E3", type="text", content="no contact info here"),
    ]

    leads = analyzer.analyze_evidence(_case(), evidence)

    assert [lead.identifier for lead in leads] == ["tag-E1", "tag-E2"]
    assert "email" in leads[0].summary
    assert "phone" in leads[1].summary


def test_score_lead_weighs_email_higher_than_phone() -> None:
    analyzer = RuleBasedAnalyzer()
    email_lead = analyzer.analyze_evidence(
        _case(), [Evidence(identifier="E1", type="text", content="a@b.com")]
    )[0]
    phone_lead = analyzer.analyze_evidence(
        _case(), [Evidence(identifier="E2", type="text", content="555-123-4567")]
    )[0]

    assert analyzer.score_lead(email_lead) > analyzer.score_lead(phone_lead)
