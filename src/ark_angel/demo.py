"""Build a fully synthetic demo case that shows the correlation engine working."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ark_angel.analysis.places import suggest_places

from ark_angel import pipeline
from ark_angel.cases.store import FileCaseStore
from ark_angel.geo.exif import decimal_to_dms
from ark_angel.ingest.csv_source import CsvTipSource
from ark_angel.ingest.username_source import UsernameHit
from ark_angel.models import Case

DEMO_CASE_ID = "DEMO"
DEMO_TITLE = "Riley Tamsin (synthetic demo case)"
DEMO_NOTICE = ("Synthetic demo. Every person, number, handle and photo in this case is invented, "
               "and the username lookups and investigator reviews are simulated.")
DEMO_REVIEWER = "Det. Demo (simulated)"
TEXT_SOURCES = ("investigator_notes.txt", "social_posts.txt")


def _data_text(name: str) -> str:
    return (resources.files("ark_angel") / "demo_data" / name).read_text(encoding="utf-8")


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


def make_email(path: Path, photo: Path) -> Path:
    """Write Sam's synthetic tip-line email, with a geotagged photo attached."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = "Sam K <sam.k.demo@example.org>"
    message["To"] = "Tip Line <tips@example.gov>"
    message["Date"] = "Mon, 05 Oct 2026 07:42:00 -0600"
    message["Subject"] = "Photo from the night I last saw Riley"
    message["Message-ID"] = "<sam-photo-1@example.org>"
    message.set_content(_data_text("sam_email.txt"))
    message.add_attachment(photo.read_bytes(), maintype="image", subtype="jpeg", filename=photo.name)
    path.write_bytes(bytes(message))
    return path


def build_demo_case(store: FileCaseStore, workdir: Path, case_id: str = DEMO_CASE_ID, log=print) -> Case:
    """(Re)create the demo case: ingest text, photos and simulated hits, then enrich."""
    store.delete_case(case_id)
    store.create_case(Case(identifier=case_id, title=DEMO_TITLE))
    log(f"Created case {case_id}: {DEMO_TITLE}")

    text_dir = workdir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    export = text_dir / "tips.csv"
    export.write_text(_data_text("tips.csv"), encoding="utf-8")
    tips = pipeline.ingest_csv(store, case_id, export, CsvTipSource())
    log(f"  imported {len(tips.evidence):>2} tips from the tip-line export tips.csv ({tips.mapping.describe()})")
    chat = text_dir / "volunteer_chat.txt"
    chat.write_text(_data_text("volunteer_chat.txt"), encoding="utf-8")
    messages = pipeline.ingest_chat(store, case_id, chat)
    log(f"  imported {len(messages.evidence):>2} messages from the volunteers' WhatsApp export "
        f"({messages.skipped} system/media lines skipped)")

    for name in TEXT_SOURCES:
        path = text_dir / name
        path.write_text(_data_text(name), encoding="utf-8")
        count = len(pipeline.ingest_text(store, case_id, path))
        log(f"  ingested {count:>2} lines from {name}")

    for photo in json.loads(_data_text("photos.json"))["photos"]:
        path = make_photo(workdir / "photos" / photo["name"], photo["latitude"], photo["longitude"],
                          photo["timestamp"], photo["make"], photo["model"])
        evidence, _ = pipeline.ingest_image(store, case_id, path)
        log(f"  ingested photo {photo['name']} (GPS + timestamp + device)" if evidence else
            f"  no EXIF in {photo['name']}")

    attached = make_photo(workdir / "mail" / "bench_3am.jpg", 39.7521, -104.9993, "2026:10:01 02:58:00",
                          "Apple", "iPhone 12")
    mail = pipeline.ingest_email(store, case_id, make_email(workdir / "mail" / "sam_tip.eml", attached))
    log(f"  imported {mail.messages} email with {mail.attachments_with_gps} geotagged photo attachment "
        "(quoted reply text dropped)")

    hits = [UsernameHit(**hit) for hit in json.loads(_data_text("username_hits.json"))["hits"]]
    pipeline.ingest_username_hits(store, case_id, hits, simulated=True)
    log(f"  added {len(hits)} simulated username hits")

    unresolved = suggest_places(store.get_case(case_id).evidence)
    log(f"  evidence names {len(unresolved)} unresolved place(s): {', '.join(s.name for s in unresolved)}")
    gazetteer = workdir / "places.csv"
    gazetteer.write_text(_data_text("places.csv"), encoding="utf-8")
    places = pipeline.add_places(store, case_id, pipeline.read_places_csv(gazetteer))
    log(f"  added {len(places)} landmarks from the case gazetteer: {', '.join(p.name for p in places)}")
    resolved = [r for r in pipeline.resolve_unmatched_places(store, case_id, ["CO"]) if r.status == "resolved"]
    pipeline.add_places(store, case_id, [r.entry.to_place(r.name) for r in resolved])
    for r in resolved:
        log(f"  resolved {r.name} from the built-in US gazetteer: {r.entry.label}")

    leads, analysis = pipeline.enrich(store, case_id)
    correlated = sum(1 for lead in leads if not lead.identifier.startswith("tag-"))
    log(f"Correlated {len(analysis.entity_index)} entities and {len(analysis.points)} geo points "
        f"into {correlated} cross-source leads")

    log("Simulated investigator review:")
    for ref, status, note in (
        ("corr-phone-3035550147", "verified", "matches the subpoenaed phone records"),
        ("corr-place-Boulder", "dismissed", "the Boulder photo was taken two weeks before she went missing"),
    ):
        item, _ = pipeline.review_lead(store, case_id, ref, status, note, DEMO_REVIEWER)
        log(f"  {status} #{item.rank}: {item.lead.summary}")

    late = text_dir / "late_tips.txt"
    late.write_text(_data_text("late_tips.txt"), encoding="utf-8")
    pipeline.ingest_text(store, case_id, late)
    pipeline.enrich(store, case_id)
    log("A late tip arrives (Boulder bus driver) and enrich re-runs: the dismissed Boulder lead resurfaces")
    return store.get_case(case_id)
