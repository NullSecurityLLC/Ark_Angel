# ArkAngel Roadmap (Draft)

This roadmap turns the high-level architecture into small, demonstrable milestones.
Each phase should remain small enough to deliver in a short iteration while keeping
the end-to-end investigation workflow intact.

## Phase 1: Minimal End-to-End Workflow

**Goal:** A single case can be created, populated with a single ingestion result,
and summarized for a human reviewer.

- [x] Ingestion v0: one source adapter (CLI file import). `FileIngestionSource`.
- [x] Enrichment v0: rule-based tagging (e.g., "email present", "phone present").
      `RuleBasedAnalyzer`.
- [ ] AI Analysis v0: summarization of a single record (no clustering yet).
      Currently only rule-based scoring/ranking exists in `report`.
- [ ] Geo v0: EXIF parsing for coordinates (if available). `GeoResolver` interface
      exists but has no concrete implementation yet.
- [x] Case Mgmt v0: create/list/show cases and attach findings.
      `FileCaseStore` (JSON-file-backed) plus `ark-angel case`/`ingest`/`enrich`/`report` CLI commands.

## Phase 2: Modular Tooling & Extensibility

**Goal:** A contributor can add a new tool without modifying core logic.

- [x] Plugin interface: drop-in registry for new ingestion/enrichment tools.
      `ark_angel.registry.Registry`, with `ingestion_sources` and `analyzers`
      registries. New tools register themselves via `@registry.register("name")`
      and are selectable in the CLI with `--source`/`--analyzer`
      (see `ark-angel plugins` to list what's registered).
- [ ] Configurable pipelines: enable/disable modules per case.
- [x] Provenance tracking: record which tool produced each artifact.
      `Evidence.metadata["source"]` and `Lead.metadata["produced_by"]` are
      stamped by the CLI based on the selected plugin name.

## Phase 3: Investigation Collaboration

**Goal:** Multi-user workflows with auditability.

- Timeline of events and findings per case.
- Notes and tagging for human investigators.
- Exportable reports (PDF/Markdown) for sharing with partners.

## Phase 4: Advanced Intelligence

**Goal:** Deeper analysis on aggregated findings.

- Entity clustering across sources.
- Relationship graph exploration.
- Risk scoring and confidence indicators.

