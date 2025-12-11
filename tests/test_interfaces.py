from ark_angel.analysis.base import Analyzer
from ark_angel.cases.base import CaseStore
from ark_angel.geo.base import GeoResolver
from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Case, Evidence, Lead, Location


class MockIngestion(IngestionSource):
    def __init__(self) -> None:
        self.requests: list[str] = []

    def fetch_leads(self, identifier: str):
        self.requests.append(identifier)
        return [Lead(identifier=f"lead-{identifier}", summary="from ingestion")]


class MockAnalyzer(Analyzer):
    def analyze_evidence(self, case: Case, evidence_items):
        return [Lead(identifier=f"analysis-{item.identifier}", summary=case.title) for item in evidence_items]

    def score_lead(self, lead: Lead) -> float:
        return float(len(lead.summary))


class MockGeoResolver(GeoResolver):
    def resolve(self, query: str) -> Location:
        if not query:
            raise ValueError("query must not be empty")
        return Location(name=query.title(), latitude=1.0, longitude=2.0, country_code="XX")


class MockCaseStore(CaseStore):
    def __init__(self) -> None:
        self.cases: dict[str, Case] = {}

    def create_case(self, case: Case) -> Case:
        self.cases[case.identifier] = case
        return case

    def add_leads(self, case_id: str, leads):
        case = self.cases[case_id]
        case.leads.extend(leads)

    def add_evidence(self, case_id: str, evidence):
        case = self.cases[case_id]
        case.evidence.extend(evidence)

    def get_case(self, case_id: str) -> Case:
        return self.cases[case_id]


def test_ingestion_collects_multiple_identifiers():
    source = MockIngestion()
    leads = source.ingest_all(["A", "B"])

    assert [lead.identifier for lead in leads] == ["lead-A", "lead-B"]
    assert source.requests == ["A", "B"]


def test_analyzer_generates_leads_and_scores():
    analyzer = MockAnalyzer()
    case = Case(identifier="C1", title="Case Title")
    evidence = [Evidence(identifier="E1", type="document", content="text")]

    generated = analyzer.analyze_evidence(case, evidence)
    assert generated[0].identifier == "analysis-E1"
    assert analyzer.score_lead(generated[0]) == float(len(case.title))


def test_geo_resolver_returns_location_and_validates_query():
    resolver = MockGeoResolver()
    location = resolver.resolve("Test town")

    assert location.name == "Test Town"
    assert location.latitude == 1.0
    assert location.country_code == "XX"



def test_case_store_persists_leads_and_evidence():
    store = MockCaseStore()
    case = Case(identifier="C1", title="Case Title")
    store.create_case(case)

    leads = [Lead(identifier="L1", summary="s")]
    evidence = [Evidence(identifier="E1", type="document", content="")]
    store.add_leads(case.identifier, leads)
    store.add_evidence(case.identifier, evidence)

    stored = store.get_case(case.identifier)
    assert stored.leads == leads
    assert stored.evidence == evidence
