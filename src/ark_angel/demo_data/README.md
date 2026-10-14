# Demo case data (synthetic)

Everything here is invented for demonstration. "Riley Tamsin" is not a real
person, phone numbers use the reserved 555-01xx range, email addresses use
reserved example domains, and the usernames are made up. Do not treat any of it
as real.

The files deliberately plant overlaps for ArkAngel to find:

- one phone number written three different ways across three sources
- a handle that shows up in tips, investigator notes, social posts and a
  (simulated) username lookup
- a vehicle plate mentioned by two tipsters and in the notes
- a dropped pin and two geotagged photos within a few hundred metres
- noise that should *not* rank: a vague Boulder tip, a psychic, an unrelated post

Photos are generated at run time by `ark-angel demo` with synthetic EXIF data,
so no binary files live in the repository.
