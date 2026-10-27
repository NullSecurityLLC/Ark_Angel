# ArkAngel Roadmap

ArkAngel is pattern recognition for case evidence. Each phase should stay small
enough to ship in a short iteration without breaking the end-to-end workflow.

## Phase 1: Minimal end-to-end workflow (done)

- [x] Case management: create/list/show, JSON-file store with validated IDs.
- [x] Ingestion: text files, image EXIF, username lookups.
- [x] Geo v0: EXIF GPS parsing (`geo/exif.py`, `ExifGeoResolver`).
- [x] Enrichment v0: per-item rule tagging.

## Phase 1.5: Lead surfacing (done, powers `ark-angel demo`)

- [x] Entity extraction and normalization (phone, email, handle, plate, URL, coords, dates).
- [x] Cross-source correlation with explainable scores.
- [x] Geo clustering (haversine) and timeline.
- [x] `pivot`: feed extracted handles into username lookups (opt-in network).
- [x] Reports: text, Markdown, JSON, self-contained HTML case file.
- [x] Idempotent `enrich`, clean CLI errors.
- [x] Place names: per-case gazetteer, CSV import, `place suggest`, opt-in
      Nominatim lookup. Named places join geo clusters; cities collect geotagged
      evidence inside them.

## Phase 2: More signal, less noise

- [x] CSV/TSV tip-line exports (`ingest-csv`).
- More ingestion adapters: email (.eml), chat exports.
- [x] Built-in offline US gazetteer (cities, counties, states) with `place resolve`.
- [x] GIS export (KML for Google Earth/My Maps, GeoJSON) and Google Maps / Street View links.
- [x] Video canvass: OSM-mapped cameras and plate readers near location leads (`cameras`).
- Traffic-camera snapshots from state DOT 511 feeds near a lead (each state's API differs).
- Gazetteers for other countries; real county/state boundaries instead of radius estimates.
- Time-window correlation: entities that co-occur within N hours.
- Co-occurrence links: two entities that keep appearing in the same items.
- Per-case scoring weights and an "ignore this entity" list (e.g. the tip line's own number).

## Phase 3: Investigation collaboration

- [x] Mark leads verified or dismissed with notes and an audit trail; dismissed
      leads resurface on new evidence.
- [x] Website (`ark-angel web`): public missing-person profiles and tip forms, member case
      workspaces with sharing, admin approval of accounts and every public profile, Docker + Caddy deploy.
- Two-factor sign-in for members and admins.
- [x] Email notifications (new tip, account requested/approved, profile to review/reviewed), link-only and throttled.
- Printable missing-person flyer from an approved profile, with a QR code to its tip form.
- SQLite-backed case store for very large or busy sites.
- Free-form notes and tags on evidence items.
- Provenance on every artifact (which tool, when, with what input).
- Shareable exports (PDF) for partners and law enforcement.

## Phase 4: Scale

- Relationship graph view across entities.
- SQLite-backed store for large cases.
- Plugin registry for third-party ingestion and extraction tools.
