import csv
import gzip
from importlib import resources

import pytest

from ark_angel.analysis.correlate import analyze_case, place_weight
from ark_angel.geo.builtin import STATES, find, normalize_states, resolve, state_hints
from ark_angel.models import Evidence


def test_bundled_data_covers_every_state_and_looks_sane() -> None:
    path = resources.files("ark_angel") / "geo" / "data" / "us_gazetteer.csv.gz"
    with gzip.open(path.open("rb"), "rt", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    kinds = {kind: [r for r in rows if r["kind"] == kind] for kind in ("city", "county", "state")}
    assert len(kinds["city"]) > 20000 and len(kinds["county"]) > 3000
    assert {r["state"] for r in kinds["state"]} == set(STATES)
    for row in rows:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        assert 18 <= lat <= 72 and (-180 <= lon <= -60 or 170 <= lon <= 180), row  # Aleutians cross 180°
        assert int(row["radius_m"]) > 0


def test_find_orders_states_counties_then_cities_by_population() -> None:
    labels = [e.label for e in find("Springfield")]
    assert labels[0].startswith("Springfield, MO")
    assert [e.kind for e in find("Washington")][0] == "state"
    assert find("Boulder", ["MT"])[0].population < find("Boulder", ["CO"])[0].population


def test_saint_and_fort_abbreviations_match() -> None:
    assert find("St. Paul", ["MN"]) == find("Saint Paul", ["MN"])
    assert find("Ft Collins") == find("Fort Collins")


def test_normalize_states() -> None:
    assert normalize_states(["co", "Texas", "district of columbia"]) == ["CO", "TX", "DC"]
    with pytest.raises(ValueError, match="unknown US state"):
        normalize_states(["Narnia"])


def test_state_hints_from_codes_and_names() -> None:
    texts = ["she is in Boulder, CO now", "or Boulder, Montana", "Boulder, XX", "not Boulder County, CO"]
    assert state_hints("Boulder", texts) == ["CO", "MT"]


@pytest.mark.parametrize("name, hints, region, status, expected", [
    ("Boulder", [], [], "ambiguous", None),
    ("Boulder", [], ["CO"], "resolved", "Boulder, CO"),
    ("Boulder", ["MT"], ["CO"], "resolved", "Boulder, MT"),  # a hint in the evidence beats the region
    ("Colorado", [], [], "resolved", "Colorado"),
    ("Boulder County", [], [], "resolved", "Boulder County, CO"),
    ("Springfield", [], [], "ambiguous", None),
    ("Riley", [], ["CO"], "ambiguous", None),  # never jumps to Riley, KS on its own
    ("Union Station", [], ["CO"], "unknown", None),
])
def test_resolve_rules(name, hints, region, status, expected) -> None:
    result = resolve(name, hints, region)
    assert result.status == status
    if expected:
        assert result.entry.label.startswith(expected)


def test_to_place_keeps_evidence_spelling_and_adds_variant_alias() -> None:
    place = find("Saint Paul", ["MN"])[0].to_place("St. Paul")
    assert place.name == "St. Paul" and place.aliases == ["Saint Paul"]
    assert place.source == "builtin:city:Saint Paul, MN"
    assert place.radius_m > 1000


def test_bigger_places_weigh_less() -> None:
    assert place_weight(7_000) == (2.0, "town-sized")
    assert place_weight(25_000) == (1.0, "county-sized")
    assert place_weight(375_000) == (0.5, "state-sized")

    def lead_score(entry_name: str, state: str) -> float:
        place = find(entry_name, [state])[0].to_place()
        evidence = [
            Evidence("t:1", "text", f"she is in {place.name}", {"source": "tips"}),
            Evidence("i:1", "image", "photo", {"source": "photos", "latitude": place.latitude,
                                                "longitude": place.longitude}),
        ]
        return next(lead.score for lead in analyze_case(evidence, places=[place]).leads
                    if lead.identifier.startswith("corr-place-"))

    assert lead_score("Boulder", "CO") > lead_score("Boulder County", "CO") > lead_score("Colorado", "CO")
