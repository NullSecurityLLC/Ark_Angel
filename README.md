# ArkAngel

```text
      _        _     _             _       
     / \   ___| |__ (_)_ __   __ _| |_ ___ 
    / _ \ / __| '_ \| | '_ \ / _` | __/ _ \
   / ___ \ (__| | | | | | | | (_| | ||  __/
  /_/   \_\___|_| |_|_|_| |_|\__,_|\__\___|


           Open Source OSINT Framework  
             Built to Find the Unfound

# ArkAngel

*"Because someone out there is still waiting."*

ArkAngel is an open-source OSINT (Open Source Intelligence) framework designed to assist in locating missing persons. Combining automation, AI-assisted analysis, and human investigation workflows, ArkAngel acts as your digital sidekick in real-world searches.

Whether you're a professional investigator, an ethical hacker, or just someone who refuses to give up—ArkAngel gives you the tools to go further, faster.

---

## Features

- Modular Recon Engine — Run targeted OSINT scans across usernames, emails, phone numbers, images, and more.
- AI-Assisted Analysis — Summarize, cross-reference, and cluster leads using intelligent NLP models.
- GeoOSINT Tools — Reverse image search, EXIF parsing, and geolocation utilities.
- Custom Toolchain Support — Plug in your own scripts or APIs; ArkAngel plays well with others.
- Live Case Mode — Structure active investigations with timelines, notes, and real-time data flow.

---

## Use Cases

- Missing persons investigations  
- Human trafficking tracking  
- Cold case recon  
- Volunteer search efforts  
- Personal OSINT deep-dives  

---

## Ethical Use

ArkAngel is built with one mission: help find those who’ve gone missing.  
Do not use this toolkit for harassment, stalking, or unauthorized surveillance.  
Every action you take is your responsibility. We recommend operating under local legal guidance and, if possible, collaborating with law enforcement or official organizations.

---


## Quickstart

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Install Ark Angel in editable mode so the CLI is available:
   ```bash
   pip install -e .
   ```
3. Run the CLI help to explore commands:
   ```bash
   ark-angel --help
   ```

## Documentation

A high-level architecture overview is available at `docs/architecture.md`.

## Roadmap

We are converting the architecture sketch into short, testable milestones with a
minimal end-to-end workflow first. See the draft roadmap at `docs/roadmap.md`.

## Workflow (Happy Path)

The Phase 1 minimal workflow is implemented end-to-end via the CLI:

```bash
# 1. Create a case
ark-angel case create C1 "Missing Person Case"

# 2. Ingest a local text file as evidence (one item per line)
ark-angel ingest C1 notes.txt

# 3. Enrich evidence with rule-based tagging (email/phone detection)
ark-angel enrich C1

# 4. Review a brief report of the case's leads, ranked by score
ark-angel report C1

# List and inspect cases
ark-angel case list
ark-angel case show C1
```

Case data is stored as JSON files under `.ark_angel/cases` by default
(override with `--data-dir`).

## Adding Tools

Ingestion sources and analyzers are looked up through a plugin registry
(`ark_angel.registry`), so new tools can be added without touching the CLI or
core pipeline code. Register an implementation with a decorator:

```python
from ark_angel.ingest.base import IngestionSource
from ark_angel.registry import ingestion_sources

@ingestion_sources.register("my-source")
class MySource(IngestionSource):
    def fetch_leads(self, identifier):
        ...
```

Then select it at runtime: `ark-angel ingest C1 notes.txt --source my-source`.
Run `ark-angel plugins` to list what's currently registered.

Not yet implemented: AI-assisted summarization, geolocation/EXIF resolution,
and additional ingestion source adapters — see `docs/roadmap.md`.
