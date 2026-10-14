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

- **Entity extraction**: phones, emails, handles, plates, URLs, coordinates and dates pulled from free text and normalized, so `(303) 555-0147`, `303.555.0147` and `+1 303 555 0147` are the same number.
- **Cross-source correlation**: anything that shows up in more than one evidence item becomes a lead, and agreement across *different* sources (a tip and phone records and a social post) ranks higher than repetition within one.
- **Geo clustering**: EXIF GPS from photos and coordinates dropped into tips are clustered by distance, so three sightings within a few hundred metres surface together.
- **Place names**: each case has a gazetteer mapping names like "Union Station" or "the station" to coordinates. Tips that only *name* a spot join the same location cluster as the photos taken there. A vague "she's in Boulder" gets linked to any photo geotagged inside Boulder. `place suggest` lists the place names your evidence uses that the gazetteer doesn't cover yet.
- **Timeline**: every dated item, in order, with the most recent activity flagged.
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
Top leads
  1. [ 23.5] Location cluster around Union Station: 7 items within 295 m
              named places: Union Station, Cherry Creek Trail
  2. [ 21.0] Handle rt_demo_owl appears in 6 items across 4 sources
  3. [ 13.0] Phone 3035550147 appears in 3 items across 3 sources
              written 3 different ways: (303) 555-0147 | 303-555-0147 | 303.555.0147
  4. [ 11.0] Plate ABC1234 appears in 3 items across 2 sources
  5. [  8.0] Email sam.k.demo@example.org appears in 2 items across 2 sources
```

The cluster at the top is a dropped pin, two geotagged photos, and four tips,
notes and posts that only name the spot. The noise in the data (a psychic, an
unrelated post) stays at the bottom.

## Working a real case

```bash
ark-angel case create C1 "Missing Person Case"

# Evidence in: text files are one item per line; photos contribute EXIF GPS/time/device
ark-angel ingest C1 tips.txt
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

# Results
ark-angel report C1 --top 10
ark-angel report C1 --format html --out C1.html   # also: md, json
```

Case data is stored as JSON files under `.ark_angel/cases` by default
(override with `--data-dir`).

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
