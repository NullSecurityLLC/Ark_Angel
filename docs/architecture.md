# Architecture Overview

ArkAngel turns raw case evidence into ranked, explained leads. There is no
machine-learning model anywhere in the pipeline: every lead comes from
deterministic pattern matching, so an investigator can always see why it ranked
where it did.

```text
ingest ──> evidence (case JSON) ──> extract ──> correlate ──> ranked leads ──> report
 text, photos,                       entities    entity index,                 text / md /
 username hits                                   geo clusters,                 json / html
                                                 timeline
```

## Modules (`src/ark_angel/`)

- **ingest/**: source adapters that produce `Evidence` (and sometimes leads).
  - `file_source.py`: one evidence item per non-blank line of a text file.
  - `image_source.py`: EXIF GPS, capture time and device, stored in evidence metadata.
  - `username_source.py`: parallel public-platform checks with per-site
    "not found" markers. Hits are labelled unverified.
- **analysis/**
  - `extract.py`: regex extraction and normalization of phones, emails,
    handles, plates, URLs, coordinates and dates.
  - `correlate.py`: entity index across all evidence, haversine geo
    clustering, timeline, and explainable scoring. This is the core.
  - `places.py`: gazetteer matching (`PlaceMatcher`, longest name first,
    whole words) and `suggest_places` for place names that aren't resolved yet.
  - `rules.py`: per-item tagging ("phone present in tips.txt:3").
- **geo/**: shared EXIF helpers (`exif.py`), `ExifGeoResolver`, and the opt-in
  Nominatim lookup (`nominatim.py`), which is the only network call in place handling.
- **cases/**: `FileCaseStore`, one JSON file per case (evidence, leads, the
  case gazetteer and the review log) with validated IDs and upsert-by-identifier writes.
- **report/**: `build.py` assembles a plain-data report that the text,
  Markdown, JSON and self-contained HTML renderers share.
- **review.py**: lead review. The append-only `Case.reviews` log is matched
  back onto freshly rebuilt leads, with score-ordered lead numbers, geo-cluster
  carry-over by evidence overlap, and dismissed leads resurfacing on new evidence.
- **pipeline.py**: the ingest, enrich and review steps shared by the CLI and the demo.
  `enrich` replaces earlier analysis leads, so it is always safe to re-run.
- **demo.py** + **demo_data/**: the synthetic case behind `ark-angel demo`.

## Scoring

```text
correlated entity:  kind_weight × evidence_items + 2 × (distinct_sources − 1)
location cluster:   2.5 × evidence_items + 2 × (distinct_sources − 1)
```

Kind weights: phone, email and plate 3; handle 2.5; coarse place 2; URL 1.5.

Places come in two sizes. A precise place (radius up to the 1 km cluster
radius, e.g. a station) turns each mention into a geo point, so it joins
clusters next to photos and dropped pins. A coarse place (a city) is too vague
to be a point. It becomes a `place` entity instead, and geotagged evidence
inside its radius counts as another mention of it. Every lead stores
the reasons behind its score, and the reports show them.
