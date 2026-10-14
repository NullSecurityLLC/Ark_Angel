# ArkAngel Roadmap

ArkAngel is pattern recognition for case evidence, with no AI/LLM layer. Each
phase should stay small enough to ship in a short iteration without breaking the
end-to-end workflow.

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

- More ingestion adapters: CSV/tip-line exports, email (.eml), chat exports.
- Bundled offline gazetteer (e.g. GeoNames cities) so towns resolve without manual entry.
- Time-window correlation: entities that co-occur within N hours.
- Co-occurrence links: two entities that keep appearing in the same items.
- Per-case scoring weights and an "ignore this entity" list (e.g. the tip line's own number).

## Phase 3: Investigation collaboration

- Notes and manual tagging on leads; mark leads verified or dismissed.
- Provenance on every artifact (which tool, when, with what input).
- Shareable exports (PDF) for partners and law enforcement.

## Phase 4: Scale

- Relationship graph view across entities.
- SQLite-backed store for large cases.
- Plugin registry for third-party ingestion and extraction tools.
