# Demo case data (synthetic)

Everything here is invented for demonstration. "Riley Tamsin" is not a real
person, phone numbers use the reserved 555-01xx range, email addresses use
reserved example domains, and the usernames are made up. Do not treat any of it
as real.

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
