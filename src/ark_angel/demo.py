"""Build a fully synthetic demo case that shows the correlation engine working."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ark_angel.analysis.places import suggest_places

from ark_angel import pipeline
from ark_angel.cases.store import FileCaseStore
from ark_angel.geo.exif import decimal_to_dms
from ark_angel.ingest.username_source import UsernameHit
from ark_angel.models import Case

DEMO_CASE_ID = "DEMO"
DEMO_TITLE = "Riley Tamsin (synthetic demo case)"
DEMO_NOTICE = ("Synthetic demo. Every person, number, handle and photo in this case is invented, "
               "and the username lookups are simulated.")
TEXT_SOURCES = ("tips.txt", "investigator_notes.txt", "social_posts.txt")


def _data_text(name: str) -> str:
    return (resources.files("ark_angel") / "demo_data" / name).read_text()


def make_photo(path: Path, latitude: float, longitude: float, timestamp: str, make: str, model: str) -> Path:
    """Write a placeholder JPEG with synthetic GPS/timestamp/device EXIF tags."""
    import piexif
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 320), (38, 36, 31))
    draw = ImageDraw.Draw(img)
    draw.text((16, 16), f"SYNTHETIC DEMO PHOTO\n{path.name}\n{latitude:.4f}, {longitude:.4f}\n{timestamp}",
              fill=(224, 178, 92))
    exif = {
        "0th": {piexif.ImageIFD.Make: make.encode(), piexif.ImageIFD.Model: model.encode()},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: timestamp.encode()},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if latitude >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: decimal_to_dms(latitude),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if longitude >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: decimal_to_dms(longitude),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", exif=piexif.dump(exif))
    return path


def build_demo_case(store: FileCaseStore, workdir: Path, case_id: str = DEMO_CASE_ID, log=print) -> Case:
    """(Re)create the demo case: ingest text, photos and simulated hits, then enrich."""
    store.delete_case(case_id)
    store.create_case(Case(identifier=case_id, title=DEMO_TITLE))
    log(f"Created case {case_id}: {DEMO_TITLE}")

    text_dir = workdir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    for name in TEXT_SOURCES:
        path = text_dir / name
        path.write_text(_data_text(name))
        count = len(pipeline.ingest_text(store, case_id, path))
        log(f"  ingested {count:>2} lines from {name}")

    for photo in json.loads(_data_text("photos.json"))["photos"]:
        path = make_photo(workdir / "photos" / photo["name"], photo["latitude"], photo["longitude"],
                          photo["timestamp"], photo["make"], photo["model"])
        evidence, _ = pipeline.ingest_image(store, case_id, path)
        log(f"  ingested photo {photo['name']} (GPS + timestamp + device)" if evidence else
            f"  no EXIF in {photo['name']}")

    hits = [UsernameHit(**hit) for hit in json.loads(_data_text("username_hits.json"))["hits"]]
    pipeline.ingest_username_hits(store, case_id, hits, simulated=True)
    log(f"  added {len(hits)} simulated username hits")

    unresolved = suggest_places(store.get_case(case_id).evidence)
    log(f"  evidence names {len(unresolved)} unresolved place(s): {', '.join(s.name for s in unresolved)}")
    gazetteer = workdir / "places.csv"
    gazetteer.write_text(_data_text("places.csv"))
    places = pipeline.add_places(store, case_id, pipeline.read_places_csv(gazetteer))
    log(f"  resolved them from the demo gazetteer ({len(places)} places)")

    leads, analysis = pipeline.enrich(store, case_id)
    correlated = sum(1 for lead in leads if not lead.identifier.startswith("tag-"))
    log(f"Correlated {len(analysis.entity_index)} entities and {len(analysis.points)} geo points "
        f"into {correlated} cross-source leads")
    return store.get_case(case_id)
