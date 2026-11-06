"""God's Eye: a principal-defender case's standby emergency protocol.

It sits on standby until something activates it:

- **a missed check-in**: the principal (or their team) checks in with a code at
  least every N hours, and a deadline that passes activates it;
- **duress**: a second code that looks exactly like a normal check-in to anyone
  watching the screen, but activates it;
- **by hand**, by someone working the case.

Activation seals a SHA-256 fingerprint of the case as it stood, logs who and
why, alerts the emergency contacts, and produces an emergency briefing: where
to look first (time-aware: a routine that falls *now* goes to the top), who the
threats are and which sensitive places each one knows, the principal's
identifiers, and what to do in the next hour.

It never tracks anyone. Every word of the briefing comes from evidence the case
already holds, which the principal's own authorization covers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from ark_angel.analysis.correlate import analyze_case, entities_of, source_of
from ark_angel.analysis.defend import (
    _Identity, _coords, _when, analyze_principal, describe_slot, routine_slot, touches_sensitive,
)
from ark_angel.models import Case, Protocol

STATES = ("off", "standby", "active")
TRIGGERS = ("manual", "missed check-in", "duress")
MIN_CODE = 4
MAX_CODE = 128
NOW_WINDOW_MIN = 180  # a routine within 3 hours of now counts as "around now"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


class ProtocolError(ValueError):
    """An action the protocol's state doesn't allow, or a wrong code. Safe to show."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def hash_code(code: str) -> str:
    """A salted scrypt hash of a check-in or duress code (the case file never holds the code itself)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _matches(code: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(code.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def _validate_code(code: str, label: str) -> str:
    if not MIN_CODE <= len(code) <= MAX_CODE:
        raise ProtocolError(f"the {label} must be {MIN_CODE} to {MAX_CODE} characters")
    return code


def _log(protocol: Protocol, event: str, by: str, now: datetime, **details: Any) -> None:
    protocol.events.append({"event": event, "at": _iso(now), "by": by, **details})


def _protocol(case: Case) -> Protocol:
    if case.principal is None:
        raise ProtocolError(f"case {case.identifier!r} isn't a principal-defender case")
    return case.principal.protocol


def fingerprint(case: Case) -> str:
    """SHA-256 of the case's contents (without the protocol itself), as an integrity record."""
    data = asdict(case)
    data["created_at"] = case.created_at.isoformat()
    data["principal"].pop("protocol", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# State changes


def arm(case: Case, code: str, duress_code: str = "", checkin_hours: float = 0, contacts: tuple[str, ...] = (),
        by: str = "", now: Optional[datetime] = None) -> Protocol:
    """Put the protocol on standby (or re-arm it with new settings)."""
    from ark_angel.web.db import validate_email

    protocol = _protocol(case)
    if protocol.state == "active":
        raise ProtocolError("God's Eye is active; stand it down before re-arming")
    _validate_code(code, "check-in code")
    if duress_code:
        _validate_code(duress_code, "duress code")
        if duress_code == code:
            raise ProtocolError("the duress code must differ from the check-in code")
    if checkin_hours < 0 or checkin_hours > 24 * 30:
        raise ProtocolError("the check-in interval must be between 0 (none) and 720 hours")
    emails = []
    for contact in contacts:
        email = validate_email(contact)
        if email and email not in emails:
            emails.append(email)
    now = now or _utcnow()
    protocol.state = "standby"
    protocol.code_hash = hash_code(code)
    protocol.duress_hash = hash_code(duress_code) if duress_code else ""
    protocol.checkin_hours = float(checkin_hours)
    protocol.contacts = emails
    protocol.last_checkin = _iso(now)
    protocol.activated_at = protocol.trigger = protocol.reason = protocol.snapshot_sha256 = ""
    _log(protocol, "armed", by, now, checkin_hours=protocol.checkin_hours, contacts=len(emails),
         duress=bool(duress_code))
    return protocol


def check_in(case: Case, code: str, by: str = "", now: Optional[datetime] = None) -> bool:
    """Record a check-in. Returns True if it was the **duress** code (and activates the protocol).

    Callers must show the same result for both: whoever forced the check-in must not be able to tell.
    """
    protocol = _protocol(case)
    if protocol.state not in ("standby", "active"):
        raise ProtocolError("God's Eye isn't armed")
    now = now or _utcnow()
    # Both codes are checked every time, so not even the time taken hints at which one was typed.
    is_duress = bool(protocol.duress_hash) and _matches(code, protocol.duress_hash)
    is_normal = _matches(code, protocol.code_hash)
    if is_duress:
        protocol.last_checkin = _iso(now)
        if protocol.state != "active":
            activate(case, "duress", "duress code used at check-in", by, now)
        else:
            _log(protocol, "duress check-in", by, now)
        return True
    if not is_normal:
        _log(protocol, "failed check-in", by, now)
        raise ProtocolError("that code isn't right")
    protocol.last_checkin = _iso(now)
    _log(protocol, "checked in", by, now)
    return False


def activate(case: Case, trigger: str, reason: str, by: str = "", now: Optional[datetime] = None) -> Protocol:
    """Activate: seal a fingerprint of the case and record who, why and when."""
    protocol = _protocol(case)
    if trigger not in TRIGGERS:
        raise ProtocolError(f"trigger must be one of {', '.join(TRIGGERS)}")
    if protocol.state == "active":
        return protocol
    if trigger == "manual" and not reason.strip():
        raise ProtocolError("say why you're activating it (it goes in the log and the briefing)")
    now = now or _utcnow()
    protocol.state = "active"
    protocol.activated_at = _iso(now)
    protocol.trigger = trigger
    protocol.reason = reason.strip()[:500]
    protocol.snapshot_sha256 = fingerprint(case)
    _log(protocol, "activated", by, now, trigger=trigger, reason=protocol.reason,
         snapshot_sha256=protocol.snapshot_sha256)
    return protocol


def stand_down(case: Case, note: str, by: str = "", now: Optional[datetime] = None) -> Protocol:
    """Stand down after an activation (the principal is safe), back to standby. A note is required."""
    protocol = _protocol(case)
    if protocol.state != "active":
        raise ProtocolError("God's Eye isn't active")
    if not note.strip():
        raise ProtocolError("say why you're standing down (it goes in the log)")
    now = now or _utcnow()
    protocol.state = "standby"
    protocol.last_checkin = _iso(now)
    _log(protocol, "stood down", by, now, note=note.strip()[:500])
    return protocol


def disarm(case: Case, by: str = "", now: Optional[datetime] = None) -> Protocol:
    protocol = _protocol(case)
    if protocol.state == "active":
        raise ProtocolError("God's Eye is active; stand it down first")
    protocol.state = "off"
    protocol.code_hash = protocol.duress_hash = ""
    _log(protocol, "disarmed", by, now or _utcnow())
    return protocol


def deadline(protocol: Protocol) -> Optional[datetime]:
    last = _parse(protocol.last_checkin)
    if protocol.state != "standby" or not protocol.checkin_hours or last is None:
        return None
    return last + timedelta(hours=protocol.checkin_hours)


def check_deadline(case: Case, now: Optional[datetime] = None) -> bool:
    """Activate if the check-in deadline has passed. Returns True if this call activated it."""
    protocol = case.principal.protocol if case.principal else None
    due = deadline(protocol) if protocol else None
    now = now or _utcnow()
    if due is None or now < due:
        return False
    activate(case, "missed check-in", f"no check-in since {protocol.last_checkin} "
             f"(due every {protocol.checkin_hours:g} h)", "God's Eye", now)
    return True


def sweep(store, case_ids, alert: Optional[Callable[[Case], None]] = None, now: Optional[datetime] = None,
          lock: Optional[Callable[[str], Any]] = None) -> list[str]:
    """Check every armed defender case's deadline; activate and alert the overdue ones. Returns their ids."""
    from contextlib import nullcontext

    activated = []
    for case_id in case_ids:
        with (lock(case_id) if lock else nullcontext()):
            try:
                case = store.get_case(case_id)
            except (KeyError, ValueError, OSError):
                continue
            if case.principal is None or not check_deadline(case, now):
                continue
            store.save_case(case)
        activated.append(case_id)
        if alert:
            alert(case)
    return activated


# The emergency briefing


def _maps(lat: float, lon: float) -> str:
    from ark_angel.report.gis import google_maps_url

    return google_maps_url(lat, lon)


def _minute_gap(slot_minute: int, now: datetime) -> int:
    return abs(slot_minute - (now.hour * 60 + now.minute))


def briefing(case: Case, now: Optional[datetime] = None) -> dict[str, Any]:
    """Everything a protection team or police need first, from the case's own evidence.

    ``now`` is local wall-clock time (like evidence timestamps); it decides which routines fall "around now".
    """
    principal = case.principal
    if principal is None:
        raise ProtocolError(f"case {case.identifier!r} isn't a principal-defender case")
    protocol = principal.protocol
    now = now or datetime.now()
    identity = _Identity.of(principal)
    defended = analyze_principal(principal, case.evidence, case.places)
    by_id = {item.identifier: item for item in case.evidence}

    def linked(item) -> bool:
        keys = {(e.kind, e.value) for e in entities_of(item)}
        return bool(keys & identity.keys) or identity.names_principal(str(item.content))

    threats = [lead for lead in defended.leads if lead.identifier.startswith("threat-")]
    known_by_threats = {e.value: [lead.summary for lead in threats if e in lead.entities]
                        for lead in threats for e in lead.entities if e.kind == "place"}
    threat_ids = {eid for lead in threats for eid in lead.evidence_ids}

    places: list[dict[str, Any]] = []

    # 1. Places their routine puts them around now.
    clusters = analyze_case(case.evidence, places=case.places).clusters
    for cluster in clusters:
        ids = list(dict.fromkeys(p.evidence_id for p in cluster.points))
        times = [t for t in (_when(by_id[i]) for i in ids if i in by_id) if t]
        slot = routine_slot(times)
        if not slot:
            continue
        name = cluster.place_names[0] if cluster.place_names else None
        for place in principal.places:
            if touches_sensitive(cluster.latitude, cluster.longitude, cluster.radius_km * 1000, [place]):
                name = place.name
        entry = {"label": name or f"Regular spot at {cluster.latitude:.5f}, {cluster.longitude:.5f}",
                 "latitude": cluster.latitude,
                 "longitude": cluster.longitude, "map": _maps(cluster.latitude, cluster.longitude),
                 "routine": describe_slot(slot), "evidence_ids": ids}
        if slot[0] == now.weekday() and _minute_gap(slot[1], now) <= NOW_WINDOW_MIN:
            entry.update(priority=1, why=f"their routine puts them here around now ({describe_slot(slot)})")
        else:
            entry.update(priority=5, why=f"a regular place of theirs ({describe_slot(slot)})")
        places.append(entry)

    # 2. Their last known location: the latest dated, located evidence tied to them (not a threat's).
    located = [(when, item, lat, lon) for item in case.evidence if item.identifier not in threat_ids and linked(item)
               for lat, lon in _coords(item) if (when := _when(item))]
    if located:
        when, item, lat, lon = max(located, key=lambda row: row[0])
        places.append({"label": f"Last known location ({when:%Y-%m-%d %H:%M})", "latitude": lat, "longitude": lon,
                       "map": _maps(lat, lon), "priority": 2, "evidence_ids": [item.identifier],
                       "why": f"latest located evidence tied to them: {source_of(item)} ({item.identifier})"})

    # 3 and 4. Sensitive places, those a threatening sender knows first.
    for place in principal.places:
        knowers = known_by_threats.get(place.name, [])
        places.append({"label": place.name, "latitude": place.latitude, "longitude": place.longitude,
                       "map": _maps(place.latitude, place.longitude), "priority": 3 if knowers else 4,
                       "evidence_ids": [],
                       "why": (f"a threatening sender knows it: {'; '.join(knowers)}" if knowers
                               else "one of their sensitive places")})

    for entry in places:  # a routine at a sensitive place is the same place: keep the higher priority once
        entry["key"] = (round(entry["latitude"], 3), round(entry["longitude"], 3))
    best: dict[tuple, dict] = {}
    for entry in sorted(places, key=lambda e: e["priority"]):
        if entry["key"] in best:
            best[entry["key"]]["why"] += f"; also {entry['why']}"
            continue
        best[entry["key"]] = entry
    ordered = sorted(best.values(), key=lambda e: (e["priority"], e["label"]))
    for entry in ordered:
        entry.pop("key")

    # Threats, most serious first, with what's known about each sender.
    people = []
    for lead in threats:
        identifiers = []
        latest = None
        for eid in lead.evidence_ids:
            item = by_id.get(eid)
            if item is None:
                continue
            when = _when(item)
            latest = max(latest, when) if latest and when else (when or latest)
            sender = str((item.metadata or {}).get("sender") or "").strip()
            if sender and sender not in identifiers:
                identifiers.append(sender)
        name = identifiers[0] if identifiers else lead.summary.split(":")[0].removeprefix("Threats from ")
        identifiers = identifiers[1:]
        for eid in lead.evidence_ids:
            item = by_id.get(eid)
            if item is None:
                continue
            for entity in entities_of(item):
                value = f"{entity.kind} {entity.value}"
                if (entity.kind in ("phone", "email", "handle", "plate") and (entity.kind, entity.value)
                        not in identity.keys and value not in identifiers):
                    identifiers.append(value)
        people.append({"name": name, "summary": lead.summary, "score": lead.score, "identifiers": identifiers,
                       "knows": [e.value for e in lead.entities if e.kind == "place"],
                       "escalating": any(r.startswith("ESCALATING") for r in lead.reasons),
                       "latest": f"{latest:%Y-%m-%d %H:%M}" if latest else "", "evidence_ids": lead.evidence_ids})

    exposed = [lead.summary for lead in defended.leads if lead.identifier.startswith("exp-")]
    actions = ["Call the police (911 in the US) and say this is a protected person with a documented threat. "
               "Give them this briefing."]
    if ordered:
        actions.append(f"Check the places below in order, starting with {ordered[0]['label']}. Don't go alone "
                       "to a place a threatening sender knows.")
    for person in people[:3]:
        actions.append(f"Give police everything on {person['name']}"
                       + (f" ({', '.join(person['identifiers'][:4])})" if person["identifiers"] else "")
                       + (". They know " + ", ".join(person["knows"]) if person["knows"] else "") + ".")
    actions += ["Ask businesses near those places to preserve camera footage now; it's often overwritten within "
                "days (`ark-angel cameras CASE --online` lists mapped cameras near each location lead).",
                "Try the principal's phone, and their emergency contacts. Write down every attempt and its time.",
                "Don't post about this publicly until police agree: a public post tells the threat what you know."]

    p = principal
    return {
        "case": {"identifier": case.identifier, "title": case.title, "evidence_count": len(case.evidence)},
        "generated_local": now.isoformat(sep=" ", timespec="minutes"),
        "protocol": {"state": protocol.state, "trigger": protocol.trigger, "reason": protocol.reason,
                     "activated_at": protocol.activated_at, "last_checkin": protocol.last_checkin,
                     "snapshot_sha256": protocol.snapshot_sha256, "current_sha256": fingerprint(case)},
        "principal": {"name": p.name, "aliases": list(p.aliases), "phones": list(p.phones), "emails": list(p.emails),
                      "handles": list(p.handles), "plates": list(p.plates), "authorized_by": p.authorized_by},
        "places": ordered,
        "threats": people,
        "exposed": exposed,
        "actions": actions,
    }


# Alerts


def alert_contacts(case: Case, env: Optional[dict] = None, extra: tuple[str, ...] = (),
                   link: str = "") -> tuple[int, str]:
    """Email the emergency contacts that God's Eye fired. Returns ``(emails sent, problem or "")``.

    Like every ArkAngel email, it carries no case content: mail sits on lock screens and in providers'
    servers. It says that it fired, for which case and how, and where to read the briefing.
    """
    import os

    from ark_angel.web.notify import SmtpConfig, alert_now

    protocol = _protocol(case)
    recipients = list(dict.fromkeys([*protocol.contacts, *extra]))
    if not recipients:
        return 0, "no emergency contacts set"
    try:
        config = SmtpConfig.from_env(os.environ if env is None else env)
    except ValueError as exc:
        return 0, f"email settings unusable: {exc}"
    if config is None:
        return 0, "email isn't set up (ARK_ANGEL_SMTP_*)"
    where = link or f"run `ark-angel defend godseye brief {case.identifier}`"
    body = (f"ArkAngel God's Eye was activated for case {case.identifier}, which lists you as an emergency "
            f"contact.\n\nHow: {protocol.trigger}\nWhen: {protocol.activated_at} (UTC)\n\n"
            f"Read the emergency briefing now: {where}\n\nNothing about the case is put in email. If you believe "
            "someone is in danger, call the police first.\n")
    try:
        sent = alert_now(config, recipients, f"[ArkAngel] EMERGENCY: God's Eye activated ({case.identifier})", body)
    except OSError as exc:  # smtplib errors are OSErrors
        return 0, f"sending failed: {exc.__class__.__name__}"
    return sent, ""
