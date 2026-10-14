"""Build the bundled US gazetteer (src/ark_angel/geo/data/us_gazetteer.csv.gz).

Developer tool, not part of the package. Inputs (downloaded by hand):

- cities500.json and us_counties.json from the geonamescache wheel
  (https://pypi.org/project/geonamescache/). The city data is GeoNames,
  CC BY 4.0 (https://www.geonames.org/).
- county_centers.csv: US Census Bureau 2010 county geographic centres, as
  compiled at https://github.com/btskinner/spatial (data/county_centers.csv).
  Census data is public domain.

Neither source carries land area, so radii are estimates:

- city: from population, assuming ~1,000 people/km², clamped to 1.5-30 km.
- county: 0.6 x the mean distance to its four nearest neighbouring county
  centres, which is roughly the county's inradius when counties tile the map.
- state: the furthest county centre from the state's centre, plus that
  county's radius. The state centre is the middle of its counties' bounding box.

Usage:
    python scripts/build_gazetteer.py CITIES500_JSON US_COUNTIES_JSON COUNTY_CENTERS_CSV OUT_CSV_GZ
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def city_radius_m(population: int) -> int:
    area_km2 = max(population, 1) / 1000.0
    return int(round(min(max(math.sqrt(area_km2 / math.pi) * 1200, 1500), 30000), -2))


def city_rows(cities_path):
    """Cities, keeping the most populous entry per (name, state)."""
    best = {}
    for city in json.load(open(cities_path)).values():
        if city.get("countrycode") != "US" or city.get("admin1code") not in STATE_NAMES:
            continue
        key = (city["name"], city["admin1code"])
        if key not in best or city.get("population", 0) > best[key].get("population", 0):
            best[key] = city
    rows = []
    for (name, state), city in sorted(best.items()):
        pop = int(city.get("population") or 0)
        rows.append(("city", name, state, round(city["latitude"], 4), round(city["longitude"], 4),
                     city_radius_m(pop), pop))
    return rows


def county_rows(counties_path, centers_path):
    """County names by FIPS, joined to Census 2010 geographic centres. Returns (rows, skipped)."""
    centers = {}
    with open(centers_path, newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                centers[row["fips"].zfill(5)] = (float(row["clat10"]), float(row["clon10"]))
            except (KeyError, ValueError):
                continue
    counties, missing = [], 0
    for county in json.load(open(counties_path)):
        fips, state = county["fips"].zfill(5), county["state"]
        if state not in STATE_NAMES:
            continue
        if fips not in centers:
            missing += 1
            continue
        counties.append((county["name"], state, *centers[fips]))
    rows = []
    for i, (name, state, lat, lon) in enumerate(counties):
        nearest = sorted(haversine_km(lat, lon, o[2], o[3]) for j, o in enumerate(counties) if j != i)[:4]
        radius = int(round(min(max(0.6 * sum(nearest) / len(nearest) * 1000, 3000), 150000), -2))
        rows.append(("county", name, state, round(lat, 4), round(lon, 4), radius, 0))
    return rows, missing


def state_rows(counties):
    """States from the middle of their counties' bounding box out to the furthest county edge.

    A mean would drift toward wherever counties are small and dense. Aleutian
    longitudes east of 180 are unwrapped so Alaska's box doesn't span the globe.
    """
    by_state = {}
    for row in counties:
        by_state.setdefault(row[2], []).append(row)
    rows = []
    for state, members in sorted(by_state.items()):
        lons = [r[4] - 360 if r[4] > 0 else r[4] for r in members]
        lat = (min(r[3] for r in members) + max(r[3] for r in members)) / 2
        lon = (min(lons) + max(lons)) / 2
        reach = max(haversine_km(lat, lon, r[3], ln) * 1000 + r[5] for r, ln in zip(members, lons))
        rows.append(("state", STATE_NAMES[state], state, round(lat, 4), round(lon, 4), int(round(reach, -3)), 0))
    return rows


def build(cities_path, counties_path, centers_path, out_path):
    counties, missing = county_rows(counties_path, centers_path)
    rows = city_rows(cities_path) + counties + state_rows(counties)
    with gzip.open(out_path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "name", "state", "latitude", "longitude", "radius_m", "population"])
        writer.writerows(rows)
    kinds = {k: sum(1 for r in rows if r[0] == k) for k in ("city", "county", "state")}
    print(f"wrote {len(rows)} rows {kinds}; {missing} counties had no 2010 centre and were skipped")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    build(*sys.argv[1:])
