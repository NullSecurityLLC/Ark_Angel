# Demo case data (synthetic)

Everything here is invented for demonstration. "Riley Tamsin" is not a real
person, phone numbers use the reserved 555-01xx range, email addresses use
reserved example domains, and the usernames are made up. Do not treat any of it
as real.

`volunteer_chat.txt` is a WhatsApp group export, and `sam_email.txt` is the
body of an email that the demo sends with a generated, geotagged photo
attached. The quoted reply in that email holds a phone number that must *not*
be counted.

`tips.csv` is shaped like a tip-line export: US-style dates, a callback number
and caller location per tip, and a dropped pin in the Lat/Lon columns.

The files deliberately plant overlaps for ArkAngel to find:

- one phone number written three different ways across three sources
- a handle that shows up in tips, investigator notes, social posts and a
  (simulated) username lookup
- a vehicle plate mentioned twice on the tip line and once in the notes, by a
  repeat caller whose callback number matches across both tips
- a dropped pin and two geotagged photos within a few hundred metres, plus
  tips and notes that only *name* the spot ("Union Station", "the station",
  "Cherry Creek trail"); `places.csv` maps those landmarks to coordinates so
  they join the same location cluster
- a vague "she's in Boulder" tip. Boulder is resolved from the built-in US
  gazetteer (with Colorado as the case region), which links it to an older
  photo geotagged inside Boulder
- noise that should *not* rank: a psychic, an unrelated post

Photos are generated at run time by `ark-angel demo` with synthetic EXIF data,
so no binary files live in the repository.

## Principal defender demo (`ark-angel defend demo`)

The `defend_*` files are a second synthetic case, for the principal-defender
mode. "Dana Whitlock" is invented too, as are "1187 Demo Lane", "Demo Tower",
"Rook" and every listing site (all on `.example` domains).

- `defend_broker_listings.txt`: people-search listings that expose the
  principal's home address, phone and email, the way they really get aggregated
- `defend_posts.txt`: the principal's own posts. Three geotagged runs on
  Tuesdays around 06:15 give away a routine, and one names their plate
- `defend_messages.txt`: a WhatsApp export. "Rook" escalates over three weeks
  from a grievance to naming the principal's street address. A coworker's
  "you killed it at the presentation" must *not* count as a threat
- `defend_late.txt`: a new listing that re-exposes the phone after the demo
  marks it remediated, so the exposure resurfaces

The demo also generates a photo geotagged 20 m from the principal's home.
