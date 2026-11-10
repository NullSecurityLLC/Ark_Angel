"""Succession: if an operator goes down, a trusted successor takes over their cases.

A member names a successor (an approved member with two-factor sign-in on),
an inactivity limit and a waiting period. A handover starts when they've been
inactive past the limit, or early when the successor asks. Either way it only
completes after the waiting period. They're emailed right away, and anything
they do on the site (signing in, a page load) cancels it. When it completes,
the successor owns their cases (they stay on as an editor) and gets the admin
role if they had it, so a site can't be left without anyone to run it.

Like all of the site's email, these messages carry no case content: who, what,
when, and a link.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("ark_angel.web.succession")


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _send(site, to: str, subject: str, body: str) -> None:
    if to and site.mailer.enabled:
        site.mailer.send(to, f"[ArkAngel] {subject}", body + "\n\nNothing about any case is put in email.\n")


def notify_named(site, row, account_link: str) -> None:
    _send(site, row["successor_email"], f"{row['primary_name']} named you their successor",
          f"{row['primary_name']} named you as the person who takes over their cases if they go down. If they're "
          f"inactive for {row['inactive_days']} days, or you ask for it, their cases pass to you after a "
          f"{row['wait_days']}-day waiting period that they can cancel.\n\nSee it, or step down: {account_link}")


def notify_requested(site, row, due: float, account_link: str) -> None:
    why = ("you've been inactive for over " f"{row['inactive_days']} days" if row["requested_by"] == "inactivity"
           else f"{row['requested_by']} asked for it")
    _send(site, row["primary_email"], "Your cases will be handed over unless you act",
          f"A handover of your cases to {row['successor_name']} has started, because {why}. It completes on "
          f"{_when(due)}.\n\nIf you're fine, just sign in: that cancels it. Or cancel it here: {account_link}\n\n"
          "If you didn't expect this, change your password and check who your successor is.")
    _send(site, row["successor_email"], f"Handover from {row['primary_name']} started",
          f"The handover of {row['primary_name']}'s cases to you completes on {_when(due)} unless they sign in or "
          f"cancel it. {account_link}")


def notify_canceled(site, row, account_link: str) -> None:
    _send(site, row["successor_email"], f"Handover from {row['primary_name']} canceled",
          f"{row['primary_name']} is active again (or canceled it), so the handover of their cases to you is off. "
          f"You're still their successor. {account_link}")


def notify_completed(site, row, cases: list[str], account_link: str) -> None:
    admin = " and their admin role" if row["primary_role"] == "admin" else ""
    _send(site, row["successor_email"], f"You now own {row['primary_name']}'s cases",
          f"The waiting period ended without {row['primary_name']} signing in, so their {len(cases)} case(s){admin} "
          f"are now yours. They stay on each case as an editor.\n\n{account_link}")
    _send(site, row["primary_email"], "Your cases were handed over",
          f"Your {len(cases)} case(s){admin} passed to {row['successor_name']}. You're still an editor on each. If "
          f"you didn't expect this, contact an administrator. {account_link}")


def sweep_succession(site, base_url: str) -> dict[str, list[str]]:
    """Start handovers for inactive primaries, and complete the ones whose waiting period is over."""
    link = f"{base_url.rstrip('/')}/app/account#succession"
    started, completed = [], []
    for row in site.db.inactive_designations():
        try:
            due = site.db.request_handover(row["user_id"], "inactivity")
        except ValueError:
            continue
        notify_requested(site, {**dict(row), "requested_by": "inactivity"}, due, link)
        started.append(row["primary_name"])
        log.warning("succession: handover of %s's cases to %s started (inactive)", row["primary_name"],
                    row["successor_name"])
    for row in site.db.due_handovers():
        try:
            cases = site.db.complete_handover(row["user_id"])
        except ValueError:
            log.warning("succession: couldn't complete %s's handover (successor not active?)", row["primary_name"])
            continue
        notify_completed(site, row, cases, link)
        completed.append(row["primary_name"])
        log.warning("succession: %s's %d case(s) handed to %s", row["primary_name"], len(cases), row["successor_name"])
    return {"started": started, "completed": completed}
