"""Build a fully synthetic principal-defender case: exposures, a routine and an escalating threat."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from ark_angel import pipeline
from ark_angel.cases.store import FileCaseStore
from ark_angel.demo import make_photo
from ark_angel.models import Case, Place

DEFEND_DEMO_ID = "DEFEND-DEMO"
DEFEND_DEMO_NOTICE = ("Synthetic demo. The principal, their address, numbers, handles, the listing sites and the "
                      "threatening sender are all invented, and the review is simulated.")
DEFEND_DEMO_REVIEWER = "Protective analyst (simulated)"


def _data_text(name: str) -> str:
    return (resources.files("ark_angel") / "demo_data" / name).read_text(encoding="utf-8")


def _write(workdir: Path, name: str) -> Path:
    path = workdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_data_text(name), encoding="utf-8")
    return path


def build_defend_demo(store: FileCaseStore, workdir: Path, case_id: str = DEFEND_DEMO_ID, log=print) -> Case:
    """(Re)create the defender demo case, enrich it, simulate a remediation, and re-expose it."""
    from ark_angel.safety import ensure_safe_target

    ensure_safe_target(workdir, "write the demo's input files")
    store.delete_case(case_id)
    case = pipeline.create_defender_case(
        store, case_id, "Dana Whitlock", "the principal (simulated engagement letter, synthetic demo)",
        title="Dana Whitlock (synthetic defender demo)", aliases=["Dana M Whitlock", "D. Whitlock"],
        identifiers=[("phone", "303-555-0182"), ("email", "dana.w.demo@example.com"),
                     ("handle", "@dana_runs_demo"), ("plate", "DMO-4821")])
    log(f"Created defender case {case_id}: {case.title}")
    pipeline.add_sensitive_place(store, case_id, Place("Home", 39.70600, -104.95300, ["1187 Demo Lane"], 150.0,
                                                       source="sensitive"))
    pipeline.add_sensitive_place(store, case_id, Place("Work", 39.74800, -104.99200, ["Demo Tower"], 200.0,
                                                       source="sensitive"))
    log("  sensitive places: Home (1187 Demo Lane), Work (Demo Tower)")

    for name in ("defend_broker_listings.txt", "defend_posts.txt"):
        count = len(pipeline.ingest_text(store, case_id, _write(workdir, name)))
        log(f"  ingested {count} lines from {name}")
    chat = pipeline.ingest_chat(store, case_id, _write(workdir, "defend_messages.txt"))
    log(f"  imported {len(chat.evidence)} messages the principal received (WhatsApp export)")
    photo = make_photo(workdir / "photos" / "new_couch.jpg", 39.70612, -104.95288, "2026:09:27 20:15:00",
                       "Apple", "iPhone 15")
    pipeline.ingest_image(store, case_id, photo)
    log("  ingested new_couch.jpg, a photo the principal posted (EXIF GPS left on)")

    leads, _ = pipeline.enrich(store, case_id)
    log(f"Found {len(leads)} threats, exposures and patterns")

    item, _ = pipeline.review_lead(store, case_id, "exp-phone-3035550182", "dismissed",
                                   "opt-outs filed with PeopleLookupDemo and FindAnyoneDemo; both listings removed",
                                   DEFEND_DEMO_REVIEWER)
    log(f"Simulated remediation: dismissed #{item.rank} ({item.lead.summary})")
    pipeline.ingest_text(store, case_id, _write(workdir, "defend_late.txt"))
    pipeline.enrich(store, case_id)
    log("A new people-search site re-lists the phone and enrich re-runs: the exposure resurfaces")
    return store.get_case(case_id)
