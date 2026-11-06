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
  - `browse_source.py`: the investigator's browsing (page visits, searches, clips) as evidence with
    `metadata["origin"] = "investigator"`. Search queries and profile handles come from the URL;
    `page_excerpt` keeps snippets around each entity or watched phrase on the page.
  - `history_source.py`: a time window of Chrome/Edge/Brave, Firefox or Safari history, read from a copy.
  - `username_source.py`: parallel public-platform checks with per-site
    "not found" markers. Hits are labelled unverified.
- **analysis/**
  - `extract.py`: regex extraction and normalization of phones, emails,
    handles, plates, URLs, coordinates and dates.
  - `correlate.py`: entity index across all evidence, haversine geo
    clustering, timeline, and explainable scoring. This is the core.
  - `places.py`: gazetteer matching (`PlaceMatcher`, longest name first,
    whole words) and `suggest_places` for place names that aren't resolved yet.
  - `base.py`: `scan_text()`. For investigator-origin evidence, pattern matching reads only
    `page_excerpt`, never the query, URL or profile, so your own searches can't corroborate a lead.
    Browsing also stays out of the case timeline.
  - `trail.py`: rebuilds the investigator's sessions, dwell, case matches, pivots, threads and loose ends.
  - `rules.py`: per-item tagging ("phone present in tips:T-1003").
  - `defend.py`: principal defender mode. It matches the principal's identifiers and
    sensitive places (by alias and by geotag radius) in evidence, finds patterns of
    life and weekday/time routines in the correlation clusters, and runs threat-phrase
    triage grouped by sender, with escalation and "knows a sensitive location"
    flags. `pipeline.enrich` runs it instead of correlation leads when a case has a
    `principal`. `report/defend.py` renders the briefing.
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
  - `recorder.py`: `/api/recorder` for the Recorder extension. It takes a per-case bearer token (hashed,
    expiring, re-checked against the member's current role on every request) and never the session
    cookie, so it needs no CSRF exemption. It reuses `browse_server`'s capture matching and writes
    under the case lock. The workspace's Recorder tab issues and revokes tokens, and the Trail tab
    renders `analysis/trail.py`.
  - `totp.py`: RFC 6238 codes (stdlib HMAC), otpauth links and the setup QR code as an SVG data URI (segno).
    `db.py` keeps the challenge between the password and the code, replay protection and hashed recovery codes,
    and `app.py`'s guard holds anyone who must have two-factor but hasn't set it up on their account page.
  - `notify.py`: email notifications over SMTP (TLS required) from a background thread, throttled per person;
    messages carry links only, never case content.
  - `backup.py`: read-only snapshot, age encryption, rclone upload and pruning, and a restore that
    validates every archive entry before touching live data. Run nightly by the `backup` Compose service.
  - `site.py`: per-case write locks, upload storage and image handling. Uploads are validated by Pillow with a pixel cap; profile photos are re-encoded without metadata.
  - Case evidence stays in `FileCaseStore` and goes through the same pipeline and report builders as the CLI.
  - Deployment: `Dockerfile`, `compose.yaml` and `deploy/Caddyfile`; see `docs/deploy.md`.
- **browse_server.py**: the `ark-angel browse` receiver for the Recorder extension (`extension/`, MV3).
  It is stdlib `http.server` on 127.0.0.1 with a pairing token. It refuses web-page Origins and
  non-localhost Host headers, caps request bodies at 2 MB, and stores each capture immediately while
  checking it against the case.
- **godseye.py**: God's Eye, a defender case's standby emergency protocol. It's a state machine (off, standby,
  active) with scrypt-hashed check-in and duress codes, deadline checks (`sweep`), activation that seals a
  case fingerprint, and the time-aware emergency briefing built from `analysis/defend.py` (`routine_slot`, each
  threat lead's known places). `report/godseye.py` renders it. `web/godseye.py` runs the site's deadline
  watcher thread and sends link-only activation emails; the workspace's God's Eye tab arms it, takes check-ins
  (a duress check-in gets the same neutral page as a normal one), activates and stands it down.
- **review.py**: lead review. The append-only `Case.reviews` log is matched
  back onto freshly rebuilt leads, with score-ordered lead numbers, geo-cluster
  carry-over by evidence overlap, and dismissed leads resurfacing on new evidence.
- **pipeline.py**: the ingest, enrich and review steps shared by the CLI and the demo.
  `enrich` replaces earlier analysis leads, so it is always safe to re-run.
- **demo.py** + **demo_data/**: the synthetic case behind `ark-angel demo`.
  `defend_demo.py` and `demo_data/defend_*` build the one behind `ark-angel defend demo`.

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
