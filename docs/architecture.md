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
  - `csv_source.py`: tip-line exports, one evidence item per row. It guesses
    column roles from headers, parses US, day-first, ISO and named-month dates,
    and keeps the other columns as `Header: value` text.
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
  - `rules.py`: per-item tagging ("phone present in tips:T-1003").
- **geo/**: shared EXIF helpers (`exif.py`), `ExifGeoResolver`, the built-in US
  gazetteer resolver (`builtin.py` + `data/us_gazetteer.csv.gz`, rebuilt by
  `scripts/build_gazetteer.py`), and the opt-in Nominatim lookup
  (`nominatim.py`), which is the only network call in place handling.
  `cameras.py` is the opt-in video canvass: it asks the Overpass API for OSM-mapped
  surveillance devices around each location lead and ranks them by distance and direction.
- **cases/**: `FileCaseStore`, one JSON file per case (evidence, leads, the
  case gazetteer and the review log) with validated IDs and upsert-by-identifier writes.
- **report/**: `build.py` assembles a plain-data report that the text,
  Markdown, JSON and self-contained HTML renderers share. `gis.py` renders the same
  data as KML (Google Earth/My Maps) or GeoJSON, plus the canvass checklist CSV.
- **web/**: the optional website (`ark-angel[web]`, Flask + waitress).
  - `db.py`: SQLite storage for everything about people. It holds accounts (scrypt hashes, a role, and a pending, active or disabled status), server-side sessions (tokens stored as SHA-256) and the sign-in log that drives lockout. It also records case ownership and sharing (owner, editor or viewer), each public profile's draft and approved copies, and rate-limit events keyed by an HMAC of the address.
  - `app.py`: one request hook enforces access by area, CSRF, Origin and Host on every route; another sets the security headers.
  - `public.py`, `workspace.py` and `admin.py` are the three areas.
  - `notify.py`: email notifications over SMTP (TLS required) from a background thread, throttled per person;
    messages carry links only, never case content.
  - `backup.py`: read-only snapshot, age encryption, rclone upload and pruning, and a restore that
    validates every archive entry before touching live data. Run nightly by the `backup` Compose service.
  - `site.py`: per-case write locks, upload storage and image handling. Uploads are validated by Pillow with a pixel cap; profile photos are re-encoded without metadata.
  - Case evidence stays in `FileCaseStore` and goes through the same pipeline and report builders as the CLI.
  - Deployment: `Dockerfile`, `compose.yaml` and `deploy/Caddyfile`; see `docs/deploy.md`.
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

Kind weights: phone, email and plate 3; handle 2.5; URL 1.5. Coarse places
weigh by size: town-sized (up to 15 km) 2, county-sized (up to 75 km) 1,
state-sized 0.5.

Places come in two sizes. A precise place (radius up to the 1 km cluster
radius, e.g. a station) turns each mention into a geo point, so it joins
clusters next to photos and dropped pins. A coarse place (a city) is too vague
to be a point. It becomes a `place` entity instead, and geotagged evidence
inside its radius counts as another mention of it. Every lead stores
the reasons behind its score, and the reports show them.
