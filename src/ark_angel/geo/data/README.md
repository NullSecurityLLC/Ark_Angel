# Built-in US gazetteer

`us_gazetteer.csv.gz` holds 21,454 US cities and towns, 3,139 counties (and
county equivalents: parishes, boroughs, census areas, independent cities) and
the 50 states plus DC. The columns are:
`kind,name,state,latitude,longitude,radius_m,population`.

It is regenerated with `scripts/build_gazetteer.py`. The script's docstring has
the exact inputs.

## Sources and licences

- **Cities and towns**: [GeoNames](https://www.geonames.org/), populated places
  with population 500 or more, taken from the `cities500.json` file shipped in
  [geonamescache](https://pypi.org/project/geonamescache/) 3.0.2. GeoNames data
  is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  The coordinates and populations here come from GeoNames; the radii are ours
  (see below).
- **County names**: geonamescache `us_counties.json` (FIPS codes and names).
- **County centres**: US Census Bureau 2010 county geographic centres, as
  compiled in [btskinner/spatial](https://github.com/btskinner/spatial)
  (`data/county_centers.csv`). Census data is public domain. Four counties
  created or renamed after 2010 have no centre and are left out.
- **States**: derived here from their counties.

## Radii are estimates

Neither source has land area, so each `radius_m` is an approximation. It
decides which geotagged evidence counts as "inside" a place:

- city: from population, assuming about 1,000 people per km², clamped to 1.5-30 km
- county: 0.6 × the mean distance to its four nearest neighbouring county centres
- state: from the middle of its counties' bounding box out to the furthest county edge

That's good enough to say a photo was taken "in Boulder", and too rough for
anything finer. Landmarks belong in the case gazetteer with real coordinates.
