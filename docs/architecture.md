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
  - `rules.py`: per-item tagging ("phone present in tips.txt:3").
- **geo/**: shared EXIF helpers (`exif.py`) and `ExifGeoResolver`.
- **cases/**: `FileCaseStore`, one JSON file per case with validated IDs and
  upsert-by-identifier writes.
- **report/**: `build.py` assembles a plain-data report that the text,
  Markdown, JSON and self-contained HTML renderers share.
- **pipeline.py**: the ingest and enrich steps shared by the CLI and the demo.
  `enrich` replaces earlier analysis leads, so it is always safe to re-run.
- **demo.py** + **demo_data/**: the synthetic case behind `ark-angel demo`.

## Scoring

```text
correlated entity:  kind_weight × evidence_items + 2 × (distinct_sources − 1)
location cluster:   2.5 × evidence_items + 2 × (distinct_sources − 1)
```

Kind weights: phone, email and plate 3; handle 2.5; URL 1.5. Every lead stores
the reasons behind its score, and the reports show them.
