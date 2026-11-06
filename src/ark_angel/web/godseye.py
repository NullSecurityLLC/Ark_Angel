"""God's Eye on the website: the deadline watcher and activation alerts.

The site watches check-in deadlines by itself (a background thread started by
``ark-angel web serve``), so a missed check-in activates the protocol even when
nobody is looking. Activation emails the case's emergency contacts and every
member of the case who gave an address. Like all of the site's email, the
message has no case content in it: just that it fired, how, and a link to the
briefing behind the sign-in.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from ark_angel.models import Case

log = logging.getLogger("ark_angel.web.godseye")
WATCH_INTERVAL_S = 60


def notify_activation(site, case: Case, link: str) -> int:
    """Email the emergency contacts and the case team that God's Eye fired. Returns how many were queued."""
    if not site.mailer.enabled:
        return 0
    protocol = case.principal.protocol
    recipients = list(dict.fromkeys([*protocol.contacts, *(row["email"] for row in _team(site, case.identifier))]))
    body = (f"God's Eye was activated for case {case.identifier}.\n\nHow: {protocol.trigger}\n"
            f"When: {protocol.activated_at} (UTC)\n\nRead the emergency briefing now: {link}\n\n"
            "Nothing about the case is put in email. If you believe someone is in danger, call the police first.\n")
    subject = f"[{_site_name()}] EMERGENCY: God's Eye activated"
    for email in recipients:
        site.mailer.send(email, subject, body)
    return len(recipients)


def _site_name() -> str:
    try:
        from flask import current_app

        return current_app.config["SITE_NAME"]
    except RuntimeError:  # the watcher thread has no app context
        return "ArkAngel"


def _team(site, case_id: str):
    with site.db._connect() as db:
        return db.execute("SELECT u.email FROM case_members m JOIN users u ON u.id = m.user_id "
                          "WHERE m.case_id = ? AND u.status = 'active' AND u.email != ''", (case_id,)).fetchall()


def sweep_site(site, base_url: str, now=None) -> list[str]:
    """Check every case on the site once; activate and alert the ones whose check-in deadline passed."""
    from ark_angel import godseye

    case_ids = [row["case_id"] for row in site.db.all_cases()]

    def alert(case: Case) -> None:
        sent = notify_activation(site, case, f"{base_url.rstrip('/')}/app/cases/{case.identifier}/godseye")
        log.warning("God's Eye activated for case %s (missed check-in); %d alert(s) queued", case.identifier, sent)

    return godseye.sweep(site.store, case_ids, alert=alert, now=now, lock=site.case_lock)


def start_watcher(site, base_url: str, interval_s: int = WATCH_INTERVAL_S,
                  stop: Optional[threading.Event] = None) -> threading.Thread:
    """Run :func:`sweep_site` every ``interval_s`` seconds on a daemon thread."""
    stop = stop or threading.Event()

    def run() -> None:
        while not stop.wait(interval_s):
            try:
                sweep_site(site, base_url)
            except Exception:  # never let one bad case file stop the watching
                log.exception("God's Eye sweep failed; trying again next round")

    thread = threading.Thread(target=run, name="ark-angel-godseye", daemon=True)
    thread.start()
    return thread
