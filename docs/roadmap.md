# ArkAngel Roadmap (Draft)

This roadmap turns the high-level architecture into small, demonstrable milestones.
Each phase should remain small enough to deliver in a short iteration while keeping
the end-to-end investigation workflow intact.

## Phase 1: Minimal End-to-End Workflow

**Goal:** A single case can be created, populated with a single ingestion result,
and summarized for a human reviewer.

- Ingestion v0: one source adapter (CLI file import or a single public dataset).
- Enrichment v0: rule-based tagging (e.g., "email present", "phone present").
- AI Analysis v0: summarization of a single record (no clustering yet).
- Geo v0: EXIF parsing for coordinates (if available).
- Case Mgmt v0: create/list/update cases and attach findings.

## Phase 2: Modular Tooling & Extensibility

**Goal:** A contributor can add a new tool without modifying core logic.

- Plugin interface: drop-in registry for new ingestion/enrichment tools.
- Configurable pipelines: enable/disable modules per case.
- Provenance tracking: record which tool produced each artifact.

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

