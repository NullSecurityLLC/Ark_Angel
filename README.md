# ArkAngel

<p align="center"><img src="packaging/icons/ark-angel-512.png" width="128" height="128" alt="ArkAngel mark: an amber map pin under a halo"></p>

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

- **Email and chat import**: `.eml`, `.mbox` or a folder of `.eml`, one item per message. Image attachments with EXIF become their own items with GPS, time and device. Recipients and quoted replies are left out so the tip line's own address and re-quoted numbers don't skew the leads. WhatsApp and Telegram exports come in one item per message, with sender and time.
- **Tip-line import**: CSV/TSV exports become one evidence item per row. Column roles are guessed from the headers (tip text, id, time received, lat/lon) and can be overridden. Every other column, like a callback number or caller location, is still scanned for leads. It copes with Excel BOMs, `;`/tab delimiters and US or day-first dates.
- **Entity extraction**: phones, emails, handles, plates, URLs, coordinates and dates pulled from free text and normalized, so `(303) 555-0147`, `303.555.0147` and `+1 303 555 0147` are the same number.
- **Cross-source correlation**: anything that shows up in more than one evidence item becomes a lead, and agreement across *different* sources (a tip and phone records and a social post) ranks higher than repetition within one.
- **Geo clustering**: EXIF GPS from photos and coordinates dropped into tips are clustered by distance, so three sightings within a few hundred metres surface together.
- **Place names**: each case has a gazetteer mapping names like "Union Station" or "the station" to coordinates. Tips that only *name* a spot join the same location cluster as the photos taken there. A vague "she's in Boulder" gets linked to any photo geotagged inside Boulder. `place suggest` lists the place names your evidence uses that the gazetteer doesn't cover yet.
- **Built-in US gazetteer**: 21,454 cities and towns, 3,139 counties and all 50 states plus DC, offline. `place resolve` maps the unmatched names in your evidence to them, using state hints like "Boulder, CO" or the case's `--state`. It shows its picks before saving anything. Bigger places count for less: a state-sized match weighs a quarter of a town-sized one.
- **Maps (GIS export)**: `ark-angel map` writes the case's location leads, evidence points and places as KML (Google Earth, Google My Maps) or GeoJSON (QGIS, ArcGIS, geojson.io), with lead rank, score and review status on every pin. Reports link each location cluster to Google Maps and Street View. No API key.
- **Video canvass**: `ark-angel cameras` lists the surveillance cameras mapped in OpenStreetMap around each location lead (shop, street and traffic cameras, licence-plate readers), nearest first, with who runs them and whether they point at the spot. It's a list of places to ask for footage before it's overwritten. It never connects to a camera (opt-in, network required).
- **Website**: `ark-angel web serve` puts it online. Anyone can browse missing-person profiles and send a tip, with an optional photo, and no account. Tips land in the case and are correlated like any other evidence. Approved members run private case workspaces and share them with investigators. Admins approve every account and every public profile. Ships with a Docker + Caddy setup for automatic HTTPS. Needs `ark-angel[web]`.
- **Timeline**: every dated item, in order, with the most recent activity flagged.
- **Lead review**: verify or dismiss leads, with a note on why. Every decision is kept as an audit trail. A dismissed lead comes back by itself, flagged as resurfaced, when new evidence joins it.
- **Username lookup**: check a handle against public platforms (opt-in, network required). Hits are labelled unverified.
- **Browsing recorder**: the ArkAngel Recorder browser extension logs your investigation as you browse, with no copying and pasting. Every page, search and right-click "Add to case" clip goes into the case on your own computer. A page that mentions a phone, handle or plate already in the case is flagged while you're on it. `ark-angel trail` replays what you did: pivots ("you searched this because it was in tip T-1003"), threads you followed, and loose ends you saw but never looked up. Forgot to hit Record? `ingest-history` pulls a time window from Chrome, Edge, Brave, Firefox or Safari history.
- **Principal defender mode**: the engine turned around to protect someone. Instead of finding a missing person, a defender case shows how findable the *principal* is (a stalking or domestic-violence survivor, an executive, a public figure) and who is threatening them. It ranks exposures of their phone, email, handles, plate and home or work address across people-search listings, posts and geotagged photos, catches routines ("Tuesdays around 06:15 at the park"), and groups threatening messages by sender, flagging escalation and any sender who knows a real address. Every finding comes with a fix. `ark-angel defend demo` shows it.
- **Reports**: terminal, Markdown, JSON, or a self-contained HTML case file that opens offline. The HTML numbers every piece of evidence as a court-style exhibit (EX-001, EX-002...), opens with the latest dated activity, shows a timeline strip, and prints cleanly on white paper (or "Print / save as PDF") for handing to partners. Terminal output uses one amber accent and respects `NO_COLOR` / `--no-color`.

---

## Use Cases

- Missing persons investigations  
- Protecting a stalking or abuse survivor, or anyone else who asked for it (principal defender mode)  
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


## Download

**Standalone app, no Python needed.** Grab the file for your system from the
[Releases page](https://github.com/NullSecurityLLC/Ark_Angel/releases):

| System | File |
|---|---|
| Windows (64-bit) | `ark-angel-windows-x86_64.exe` |
| macOS (Apple Silicon) | `ark-angel-macos-arm64` |
| Linux (x86-64) | `ark-angel-linux-x86_64` |

It's a command-line tool: open a terminal (PowerShell on Windows) in the folder
you saved it to and run it with `demo` to see it work:

```bash
./ark-angel-linux-x86_64 demo          # Linux (run `chmod +x ark-angel-linux-x86_64` first)
./ark-angel-macos-arm64 demo           # macOS, see the note below
.\ark-angel-windows-x86_64.exe demo    # Windows
```

The executables aren't code-signed yet, so the OS will warn you the first time:

- **Windows**: SmartScreen says "Windows protected your PC". Click *More info*, then *Run anyway*.
- **macOS**: run `xattr -d com.apple.quarantine ark-angel-macos-arm64` once, or right-click, *Open*, then *Open* again.

Check your download against `SHA256SUMS.txt` on the release page
(`sha256sum -c SHA256SUMS.txt --ignore-missing`, or `Get-FileHash` on Windows).

**With Python 3.10+**, install it as a command with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/NullSecurityLLC/Ark_Angel.git   # latest
pipx install ./ark_angel-0.2.0-py3-none-any.whl                      # or the wheel from a release
```

## Quickstart

```bash
pip install -e .        # from a clone, for development
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
ark-angel ingest-email C1 tipline_inbox.mbox          # .eml, .mbox, or a folder of .eml; photo attachments' GPS too
ark-angel ingest-chat C1 "WhatsApp Chat with Volunteers.txt"   # WhatsApp .txt or Telegram result.json
ark-angel ingest-csv C1 hotline_export.csv --dry-run   # check the guessed columns first
ark-angel ingest-csv C1 hotline_export.csv             # or: --text Narrative --when "Date Received" --dayfirst
ark-angel ingest C1 investigator_notes.txt
ark-angel ingest-image C1 photo.jpg

# Map place names in the evidence to coordinates
ark-angel place suggest C1                         # names the evidence uses that aren't resolved yet
ark-angel place resolve C1 --state CO              # match them to US cities/counties/states (preview)
ark-angel place resolve C1 --state CO --apply      # save the ones that resolved unambiguously
ark-angel place find Springfield --state IL        # search the built-in gazetteer
ark-angel place add C1 Boulder --state CO          # a US city/county/state, no coordinates needed
ark-angel place add C1 "Union Station" 39.7527 -104.9997 --alias "the station"   # landmarks: real coordinates
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

# Maps and video canvass
ark-angel map C1 --out C1.kml                      # Google Earth / Google My Maps (or .geojson for QGIS)
ark-angel cameras C1                               # the spots it would search (offline)
ark-angel cameras C1 --online --out canvass.csv    # mapped cameras near each location lead, as a checklist
ark-angel cameras C1 --online --out C1-canvass.kml # or the whole case map plus a cameras layer

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

## Recording your browsing

Logging an investigation by hand is the part everybody skips. Let the browser do it.

```bash
ark-angel browse C1           # prints an address and a token; Ctrl+C stops and re-runs enrich
```

Load the extension from `extension/` (or `ark-angel-recorder.zip` on a release), paste the
address and token into it, and press **Record**. See [extension/README.md](extension/README.md)
for installing it and exactly what it sends. Then browse the way you normally would:

```text
  14:01  search search "303-555-0147"
  14:02  visit  Room for rent near Union Station (classifieds.example)
           MATCH phone 3035550147: also in tips (tips.csv:3)
  14:06  visit  rt_demo_owl on Instagram (instagram.com)
```

```bash
ark-angel trail C1                          # what you did, and what it means
ark-angel trail C1 --format md --out trail.md
```

The trail splits your browsing into sessions, with time spent per page, and marks:

- **MATCH**: a page that mentions something already in the case.
- **pivot**: a search or a profile you opened for something you'd already seen, either in the
  evidence or on an earlier page. The chain of pivots is your reasoning, written down for you.
- **Threads you followed**: one phone, handle or plate across several steps.
- **Loose ends**: something you clipped, or a match you saw, that you never looked up. This is
  the 2am find you'd otherwise forget.

**Your own browsing never props up a lead.** Only what a page *says* counts toward scores. The
search you typed, the link you followed and the profile you opened are kept for the trail but
never corroborate anything. Otherwise googling a number would make it rank higher. Browsing also
stays out of the case timeline, which is about the person, not your clicks.

Forgot to record? Pull the window from your browser's history:

```bash
ark-angel ingest-history C1 "<history file>" --since "2026-11-02 13:00" --until "2026-11-02 18:00" --dry-run
```

| Browser | History file |
|---|---|
| Chrome | Windows `%LOCALAPPDATA%\Google\Chrome\User Data\Default\History` · macOS `~/Library/Application Support/Google/Chrome/Default/History` · Linux `~/.config/google-chrome/Default/History` |
| Edge | Windows `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\History` · macOS `~/Library/Application Support/Microsoft Edge/Default/History` |
| Brave | `…/BraveSoftware/Brave-Browser/Default/History` (same places as Chrome) |
| Firefox | `places.sqlite` in your profile folder (`about:profiles` shows it) |
| Safari | `~/Library/Safari/History.db` (the terminal needs Full Disk Access) |

History has no page text, so it fills in the trail (searches, profiles, order) but adds nothing
to the leads. `--since` is required: your whole history isn't case evidence.

In a [defender case](#principal-defender-mode) the same recorder doubles as an exposure audit.
Browse the people-search sites, and every page showing the principal's phone or address becomes an
exposure that names the site to send the opt-out to.

## Principal defender mode

A missing-person case asks "where is this person?". A defender case asks the
same question from the other side: how easily could someone hostile answer it
about the person you protect, and is anyone trying? Same evidence pipeline,
same explained scores, pointed the other way.

```bash
ark-angel defend demo        # synthetic: exposures, a routine, an escalating threat, a re-listed phone

ark-angel defend init P1 "Dana Whitlock" --authorized-by "the principal, signed engagement 2026-10-12" \
    --alias "D. Whitlock" --phone 303-555-0182 --email dana@example.com --handle @dana_runs --plate DMO-4821
ark-angel defend place P1 Home 39.706 -104.953 --alias "1187 Demo Lane"   # the label isn't searched; the alias is
ark-angel defend place P1 Work 39.748 -104.992 --alias "Demo Tower" --radius-m 200

ark-angel ingest P1 people_search_listings.txt     # whatever is out there: listings, posts, photos
ark-angel ingest-image P1 posted_photo.jpg
ark-angel ingest-chat P1 "WhatsApp Chat with Unknown.txt"   # messages the principal received
ark-angel enrich P1
ark-angel defend brief P1                          # risk level, threats, exposures, each with a fix
ark-angel defend brief P1 --format md --out P1-brief.md

ark-angel lead dismiss P1 exp-phone-3035550182 --note "opt-outs filed, listings removed"
```

What it looks for:

- **Exposures**: each of the principal's identifiers wherever it shows up, however it's written. A single people-search listing is already an exposure. An item that also names the principal, or carries another of their identifiers, counts for more, because that's what lets a stranger tie it to them. A sensitive place is exposed when its address is written out or when a photo is geotagged inside its radius.
- **Pattern of life and routines**: places the evidence keeps putting the principal that aren't registered as sensitive, and repeats of the same place on the same weekday within two hours of the same time.
- **Threats**: threatening phrases (violence, location knowledge, approach, fixation) grouped by sender. A sender is flagged *escalating* when severity climbs or messages come faster, and *knows a sensitive location* when a threat names or is geotagged at one of the principal's sensitive places. Violence patterns need a target, so "you killed it at the presentation" doesn't count.

Scoring, like everything else, is arithmetic you can read:

```text
exposure:  weight × items + 2 × (sources − 1) + 1 × linked items      (+3 for a routine)
           weights: sensitive place 5, phone 4, plate 3.5, email 3, handle 2
pattern:   2 × items + 2 × (sources − 1)                              (+3 for a routine)
threat:    5 + Σ phrase weights (violence 6, location 5, approach 4, fixation 3)
           +5 knows a sensitive location, +3 escalating
```

Risk is **CRITICAL** when an open threat knows a sensitive location, **HIGH** for violent or escalating threats, **ELEVATED** for other threats or an exposed sensitive place, and **GUARDED** when only exposures remain.

Review works as it does on any case, and it fits this job well. Dismiss an exposure once it's fixed, with a note saying what you did. When a new listing re-exposes it, it comes back as RESURFACED with the new item named. Data brokers re-list people all the time, and this catches it.

Guard rails:

- `--authorized-by` is required and goes on the record. Defender mode is for people who asked to be protected, or for whoever is responsible for them.
- Sensitive places never go into the case gazetteer, and `map` and `cameras` leave out any location on or next to one. An exported map of the case doesn't put a pin on the principal's house.
- On the website, a defender case has no public profile tab, and the admin approval refuses to publish one even if a draft gets in some other way.
- The threat patterns triage the messages. They don't decide anything. Preserve the messages and take real threats to law enforcement.

## Place lookups and privacy

The built-in gazetteer is a resolver, not a matcher. It never scans evidence for
its 25,000 names, because plenty of town names are also people's names (Riley,
Kansas is a real town, and our demo's missing person is called Riley). It only resolves names that `place suggest` already found
in location phrasing ("near X", "in X", "moved to X"). Resolved places go into
the case gazetteer, where normal matching takes over. If a name exists in
several states and nothing narrows it down, it stays unresolved and the
candidates are listed for you to pick.

Matching place names is offline. `place lookup --online` is the only command that
touches the network, and it sends nothing but the query text you typed to
OpenStreetMap's Nominatim. Results go into the gazetteer only when you `--pick`
one. Nominatim asks for no more than about one request per second.

## Maps and cameras

**Google Maps, without an API key.** `ark-angel map C1 --out C1.kml` and import
it into [Google My Maps](https://www.google.com/mymaps) (*Create a new map >
Import*) for a map the whole team can open, or open it in Google Earth. Each
location lead is a pin with its rank, score and review status plus a circle for
its spread. Evidence points and gazetteer places get their own layers. GeoJSON
(`--out C1.geojson`) does the same for QGIS, ArcGIS or geojson.io. The export
is offline, but importing into an online map uploads it, and it carries evidence
excerpts unless you pass `--no-content`.

**Video canvass.** `ark-angel cameras C1 --online` searches OpenStreetMap for
mapped surveillance devices (`man_made=surveillance`) within 300 m of each open
or verified location lead (plus its spread), and around the latest geotagged
evidence. For each camera it shows the distance, the operator if mapped, what it
watches, and whether its mapped direction points at the spot. Licence-plate
readers (ALPR) are flagged: their operator can search plate reads for a plate in
the case, through the investigating agency. Footage is often overwritten within
weeks, so ask for preservation first.

The query sends the search circles (centre and radius) to the Overpass API and
nothing else, but those circles are the places your case cares about. Point
`--endpoint` at a self-hosted Overpass if that matters. Narrow it with
`--lead N` or add spots with `--at LAT,LON`. Map data © OpenStreetMap
contributors (ODbL).

## The website

The website is ArkAngel for everyone working a case, not just whoever has the command line:

- **Public, no account:** a list of missing people and a page for each, with a tip form. Tips can include a photo; its GPS and time feed the analysis. Tippers can stay anonymous. Their IP address is never stored, only a scrambled code used for rate limiting, which is deleted after 24 hours.
- **Members, approved by an admin:** private case workspaces. Members upload tips, tip-line CSVs, email, chats and photos, get ranked leads, verify or dismiss them, and share a case with others as editor or viewer.
- **Admins:** approve sign-ups and every public profile, manage users, and see the sign-in log.

**Profiles go public only after an admin approves them, with a police report reference, and so does every later edit.** While an edit is in review, visitors keep seeing the last approved version. Public profile photos are re-saved with every bit of hidden data removed. These rules exist because a public "missing person" page is also a tool for finding someone who fled an abuser. Approval is what stops the site being used that way.

Run it on a server with Docker (automatic HTTPS via Caddy):

```bash
cp .env.example .env && nano .env                    # your domain
docker compose up -d --build
docker compose run --rm app ark-angel web create-admin YOURNAME
```

Or locally, for just this computer:

```bash
pip install -e ".[web]"
ark-angel web create-admin graves
ark-angel web serve                                  # http://127.0.0.1:8765/
ark-angel web import-case C1 --owner graves          # put a CLI case on the site
```

Security, in brief:

- Sessions are server-side; the browser holds only a random token and the database keeps its hash.
- Passwords are hashed with scrypt, and repeated failed sign-ins lock the account or address out.
- Every form has a CSRF token, and requests for the wrong host name are refused.
- Pages run no JavaScript and carry a strict Content-Security-Policy.
- The container runs unprivileged on a read-only filesystem.

Optional email notifications (new account requests and profiles to review for admins; approvals and new tips for members) work with any SMTP provider. They never contain tip details, names or contact information, just a link.

Nightly backups are encrypted with your own [age](https://age-encryption.org) key (the server can lock them but never open them) and copied off-site with rclone (Backblaze B2, S3, R2, SFTP...). A failed backup emails the admins, and `web restore` brings one back.

Full guide, including email, backups and updates: [docs/deploy.md](docs/deploy.md).

## How leads are scored

Each correlated entity scores `kind weight × evidence items + 2 × (distinct sources − 1)`.
Kind weights are phone/email/plate 3, handle 2.5, town/city-sized places 2, URL
1.5, and location clusters 2.5 per item. Landmark-sized places (radius up to the
1 km cluster radius) are scored through their cluster, so they aren't counted twice. The weights live in `src/ark_angel/analysis/correlate.py`. Change
them, re-run `enrich`, and see what moves.

Scores order your attention. They are not conclusions. Verify everything before
acting on it.

## Data sources

The built-in gazetteer uses [GeoNames](https://www.geonames.org/) city data
(CC BY 4.0, via [geonamescache](https://pypi.org/project/geonamescache/)) and
US Census Bureau 2010 county centres (public domain). Radii are estimates. See
`src/ark_angel/geo/data/README.md` for details and `scripts/build_gazetteer.py`
to rebuild it.

## Documentation

- Architecture: `docs/architecture.md`
- Roadmap: `docs/roadmap.md`
- Running the website: `docs/deploy.md`
- Principal defender mode: [above](#principal-defender-mode)
- Browsing recorder extension: `extension/README.md`
