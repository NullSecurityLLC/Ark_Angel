# ArkAngel

```text
    _         _        _                     _
   / \   _ __| | __   / \   _ __   __ _  ___| |
  / _ \ | '__| |/ /  / _ \ | '_ \ / _` |/ _ \ |
 / ___ \| |  |   <  / ___ \| | | | (_| |  __/ |
/_/   \_\_|  |_|\_\/_/   \_\_| |_|\__, |\___|_|
                                  |___/

       Open Source OSINT Framework
         Built to Find the Unfound
```

*"Because someone out there is still waiting."*

ArkAngel is an open-source OSINT (Open Source Intelligence) framework for missing-person investigations. It does one job: take the pile of tips, notes, posts and photos a case collects, find where they agree, and put the strongest leads in front of a human first.

There is no AI model in the loop. Every lead comes from pattern matching you can read, and every score shows the arithmetic and the evidence behind it.

---

## What it does

- **Tip-line import**: CSV/TSV exports become one evidence item per row. Column roles are guessed from the headers (tip text, id, time received, lat/lon) and can be overridden. Every other column, like a callback number or caller location, is still scanned for leads. It copes with Excel BOMs, `;`/tab delimiters and US or day-first dates.
- **Entity extraction**: phones, emails, handles, plates, URLs, coordinates and dates pulled from free text and normalized, so `(303) 555-0147`, `303.555.0147` and `+1 303 555 0147` are the same number.
- **Cross-source correlation**: anything that shows up in more than one evidence item becomes a lead, and agreement across *different* sources (a tip and phone records and a social post) ranks higher than repetition within one.
- **Geo clustering**: EXIF GPS from photos and coordinates dropped into tips are clustered by distance, so three sightings within a few hundred metres surface together.
- **Place names**: each case has a gazetteer mapping names like "Union Station" or "the station" to coordinates. Tips that only *name* a spot join the same location cluster as the photos taken there. A vague "she's in Boulder" gets linked to any photo geotagged inside Boulder. `place suggest` lists the place names your evidence uses that the gazetteer doesn't cover yet.
- **Timeline**: every dated item, in order, with the most recent activity flagged.
- **Lead review**: verify or dismiss leads, with a note on why. Every decision is kept as an audit trail. A dismissed lead comes back by itself, flagged as resurfaced, when new evidence joins it.
- **Username lookup**: check a handle against public platforms (opt-in, network required). Hits are labelled unverified.
- **Reports**: terminal, Markdown, JSON, or a self-contained HTML case file that opens offline.

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

```bash
pip install -e .
ark-angel demo
```

`ark-angel demo` builds a fully synthetic case offline (invented person, reserved
555-01xx numbers, example.com emails, generated photos with fake GPS) and writes
`ark_angel_demo.html`. The data has overlaps planted in it on purpose. Watch the
engine pull them out, ranked:

```text
Verified
  #3  [ 13.0] Phone 3035550147 appears in 3 items across 3 sources
              verified by Det. Demo (simulated): matches the subpoenaed phone records

Top open leads
  1. [ 23.5] Location cluster around Union Station: 7 items within 295 m
  2. [ 21.0] Handle rt_demo_owl appears in 6 items across 4 sources
  3. [ 11.0] Plate ABC1234 appears in 3 items across 2 sources
  4. [ 10.0] Place Boulder appears in 3 items across 3 sources
              RESURFACED: dismissed by Det. Demo (simulated): the Boulder photo was taken
              two weeks before she went missing. 1 new item(s) since: late_tips.txt:1
  5. [  8.0] Email sam.k.demo@example.org appears in 2 items across 2 sources
```

The cluster at the top is a dropped pin, two geotagged photos, and four tips,
notes and posts that only name the spot. The demo then plays investigator: it
verifies the phone and dismisses the Boulder lead. A late tip from a Boulder bus
driver then arrives, and the dismissed lead comes back with the new evidence
named. The noise in the data (a psychic, an unrelated post) stays at the bottom.

## Working a real case

```bash
ark-angel case create C1 "Missing Person Case"

# Evidence in: text files are one item per line; photos contribute EXIF GPS/time/device
ark-angel ingest-csv C1 hotline_export.csv --dry-run   # check the guessed columns first
ark-angel ingest-csv C1 hotline_export.csv             # or: --text Narrative --when "Date Received" --dayfirst
ark-angel ingest C1 investigator_notes.txt
ark-angel ingest-image C1 photo.jpg

# Map place names in the evidence to coordinates
ark-angel place suggest C1                         # names the evidence uses that aren't resolved yet
ark-angel place add C1 "Union Station" 39.7527 -104.9997 --alias "the station"
ark-angel place add C1 Boulder 40.015 -105.2705 --radius-m 6000   # towns/cities: use a big radius
ark-angel place import C1 places.csv              # name,latitude,longitude[,aliases][,radius_m]
ark-angel place lookup C1 "Union Station, Denver" --online           # OpenStreetMap; add --pick N to save

# Find and rank the patterns (safe to re-run after adding evidence or places)
ark-angel enrich C1

# Handles found in the evidence -> public platform lookups (network, opt-in)
ark-angel pivot C1            # list the handles it would check
ark-angel pivot C1 --online   # actually check them, then re-run enrich

# Review: LEAD is the number from `lead list`, an id, or a unique id prefix
ark-angel lead list C1
ark-angel lead verify C1 3 --note "matches phone records"
ark-angel lead dismiss C1 corr-phone-3035550147 --note "the tip line's own number"   # a note is required
ark-angel lead show C1 3        # evidence, reasons, review history
ark-angel lead reopen C1 3

# Results
ark-angel report C1 --top 10    # verified first, then open; --all includes dismissed
ark-angel report C1 --format html --out C1.html   # also: md, json
```

Case data is stored as JSON files under `.ark_angel/cases` by default
(override with `--data-dir`).

## Reviewing leads

`enrich` rebuilds the analysis leads every time it runs. Review decisions live in
their own append-only log on the case, so they survive that. The log is also
the audit trail: who decided, when, why, and what evidence the lead had then.

- **Numbers come from score order**, so verifying or dismissing a lead never
  renumbers the others. They only shift when new evidence changes the scores.
- **Dismissals aren't final.** If evidence the lead didn't have at dismissal
  time joins it, the lead goes back to the open list marked RESURFACED, with
  the new items listed. A verified lead stays verified but also shows what's new.
- **Location clusters drift.** A cluster's id follows its centre, which moves
  as evidence arrives. A review of a cluster that no longer exists carries over
  to the current cluster sharing the most evidence with it.

Set `ARK_ANGEL_REVIEWER` (or pass `--by`) to record who made each call.

## Place lookups and privacy

Matching place names is offline. `place lookup --online` is the only command that
touches the network, and it sends nothing but the query text you typed to
OpenStreetMap's Nominatim. Results go into the gazetteer only when you `--pick`
one. Nominatim asks for no more than about one request per second.

## How leads are scored

Each correlated entity scores `kind weight × evidence items + 2 × (distinct sources − 1)`.
Kind weights are phone/email/plate 3, handle 2.5, town/city-sized places 2, URL
1.5, and location clusters 2.5 per item. Landmark-sized places (radius up to the
1 km cluster radius) are scored through their cluster, so they aren't counted twice. The weights live in `src/ark_angel/analysis/correlate.py`. Change
them, re-run `enrich`, and see what moves.

Scores order your attention. They are not conclusions. Verify everything before
acting on it.

## Documentation

- Architecture: `docs/architecture.md`
- Roadmap: `docs/roadmap.md`
