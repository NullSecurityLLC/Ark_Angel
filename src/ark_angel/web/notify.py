"""Email notifications: something needs you, here's the link. Never the content itself.

Emails pass through mail providers and show up on lock screens, so none of
them carries tip text, tippers' contact details, names, or case titles. Each
says what happened and links to the page behind the sign-in.

Mail goes out over SMTP on a background thread, so a slow or broken mail
server never holds up a tip or a sign-up. Notifications are throttled per
person (one tip email per case every 15 minutes, one admin alert per kind every
10), so a flood of spam can't become a flood of email.

Configured from the environment; with no ``ARK_ANGEL_SMTP_HOST`` the site runs
exactly as before, just without email.
"""
from __future__ import annotations

import logging
import queue
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Mapping, Optional, Sequence

log = logging.getLogger("ark_angel.web.mail")

TIP_WINDOW_S = 15 * 60
ADMIN_WINDOW_S = 10 * 60
SECURITY_MODES = ("starttls", "ssl", "none")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# The extra address for admin alerts (ARK_ANGEL_ADMIN_EMAIL) throttles under this pseudo user id.
OPS_USER_ID = 0


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    user: str = ""
    password: str = field(default="", repr=False)  # never printed or logged
    security: str = "starttls"
    admin_emails: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Optional["SmtpConfig"]:
        """Settings from ``ARK_ANGEL_SMTP_*``; None when email isn't set up.

        Raises:
            ValueError: settings that are present but unusable.
        """
        from ark_angel.web.db import validate_email

        host = env.get("ARK_ANGEL_SMTP_HOST", "").strip()
        if not host:
            return None
        security = env.get("ARK_ANGEL_SMTP_SECURITY", "starttls").strip().lower() or "starttls"
        if security not in SECURITY_MODES:
            raise ValueError(f"ARK_ANGEL_SMTP_SECURITY must be one of {', '.join(SECURITY_MODES)}")
        if security == "none" and host not in _LOCAL_HOSTS:
            # A password or tip notices in plain text across the internet: only for a relay on this machine.
            raise ValueError("ARK_ANGEL_SMTP_SECURITY=none is only allowed for a mail relay on localhost")
        default_port = {"starttls": 587, "ssl": 465, "none": 25}[security]
        try:
            port = int(env.get("ARK_ANGEL_SMTP_PORT", "") or default_port)
        except ValueError:
            raise ValueError("ARK_ANGEL_SMTP_PORT must be a number") from None
        sender = validate_email(env.get("ARK_ANGEL_SMTP_FROM", ""))
        if not sender:
            raise ValueError("set ARK_ANGEL_SMTP_FROM to the address emails come from")
        admins = tuple(validate_email(a) for a in env.get("ARK_ANGEL_ADMIN_EMAIL", "").split(",") if a.strip())
        return cls(host=host, port=port, sender=sender, user=env.get("ARK_ANGEL_SMTP_USER", "").strip(),
                   password=env.get("ARK_ANGEL_SMTP_PASSWORD", ""), security=security, admin_emails=admins)


def build_message(config: SmtpConfig, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = config.sender
    msg["To"] = to  # one recipient per message: members never see each other's addresses
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=config.sender.rsplit("@", 1)[1])
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body)
    return msg


def deliver(config: SmtpConfig, messages: Sequence[EmailMessage]) -> None:
    """Send now over one connection. Raises smtplib/OSError on failure."""
    context = ssl.create_default_context()  # verifies the server's certificate
    if config.security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=20)
    with server:
        if config.security == "starttls":
            server.starttls(context=context)
        if config.user:
            server.login(config.user, config.password)
        for msg in messages:
            server.send_message(msg)


class Mailer:
    """Queues emails and sends them from one background thread."""

    def __init__(self, config: SmtpConfig, max_queue: int = 1000) -> None:
        self.config = config
        self._queue: "queue.Queue[EmailMessage]" = queue.Queue(maxsize=max_queue)
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return True

    def send(self, to: str, subject: str, body: str) -> None:
        try:
            self._queue.put_nowait(build_message(self.config, to, subject, body))
        except queue.Full:
            log.warning("email queue full; dropped a notification")
            return
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="ark-angel-mail", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            msg = self._queue.get()
            try:
                deliver(self.config, [msg])
            except (smtplib.SMTPException, OSError) as exc:
                # No addresses or bodies in the log: just that it failed and why.
                log.error("could not send an email notification: %s", exc.__class__.__name__)
            finally:
                self._queue.task_done()


class NullMailer:
    """Email not configured: notifications are skipped."""

    enabled = False

    def send(self, to: str, subject: str, body: str) -> None:
        return None


def mailer_from_env(env: Mapping[str, str]):
    config = SmtpConfig.from_env(env)
    return Mailer(config) if config else NullMailer()


# The notifications themselves. Each needs a request context (links use the site's own address).


def _send(site, user_id: int, email: str, key: str, window_s: float, subject: str, body: str) -> None:
    if not email or not site.mailer.enabled:
        return
    # Throttled per person and address: the extra ARK_ANGEL_ADMIN_EMAIL addresses all share OPS_USER_ID.
    if window_s and not site.db.claim_notification(user_id, f"{key}|{email}", window_s):
        return
    site.mailer.send(email, subject, body)


def _subject(text: str) -> str:
    from flask import current_app

    return f"[{current_app.config['SITE_NAME']}] {text}"


def _footer() -> str:
    from flask import url_for

    return ("\n\n--\nYou get this because of your account on this site. Change your email or tip notifications "
            f"on your account page: {url_for('workspace.account', _external=True)}\n")


def _admins(site) -> list[tuple[int, str]]:
    recipients = [(row["id"], row["email"]) for row in site.db.admin_recipients()]
    config = getattr(site.mailer, "config", None)
    if config is not None:
        recipients += [(OPS_USER_ID, address) for address in config.admin_emails
                       if address not in {email for _, email in recipients}]
    return recipients


def account_requested(site) -> None:
    from flask import url_for

    body = ("Someone asked for an account and is waiting for approval.\n\n"
            f"Review requests: {url_for('admin.users', _external=True)}\n\n"
            "You get at most one of these every 10 minutes, so there may be more than one waiting.")
    for user_id, email in _admins(site):
        _send(site, user_id, email, "admin:signup", ADMIN_WINDOW_S, _subject("New account request"),
              body + _footer())


def profile_requested(site) -> None:
    from flask import url_for

    body = ("A missing-person profile is waiting for review before it can go public.\n\n"
            f"Review it: {url_for('admin.publications', _external=True)}\n\n"
            "You get at most one of these every 10 minutes, so there may be more than one waiting.")
    for user_id, email in _admins(site):
        _send(site, user_id, email, "admin:profile", ADMIN_WINDOW_S, _subject("Profile waiting for review"),
              body + _footer())


def account_approved(site, user_id: int) -> None:
    from flask import url_for

    user = site.db.user_by_id(user_id)
    if user is None:
        return
    body = ("An administrator approved your account. You can sign in now.\n\n"
            f"Sign in: {url_for('public.login', _external=True)}")
    _send(site, user["id"], user["email"], "", 0, _subject("Your account is approved"), body + _footer())


def profile_decided(site, case_id: str, approved: bool) -> None:
    from flask import url_for

    owner = site.db.case_owner(case_id)
    if owner is None or owner["status"] != "active":
        return
    link = url_for("workspace.profile", case_id=case_id, _external=True)
    if approved:
        subject, body = "A profile you sent is now public", f"An administrator approved it. See it here: {link}"
    else:
        subject, body = "A profile you sent was sent back", f"An administrator left you a note: {link}"
    _send(site, owner["id"], owner["email"], "", 0, _subject(subject), body + _footer())


def tip_received(site, case_id: str) -> None:
    from flask import url_for

    body = ("A new tip came in on one of your cases. Tip details are never put in email.\n\n"
            f"Read it: {url_for('workspace.tips', case_id=case_id, _external=True)}\n\n"
            "You get at most one of these per case every 15 minutes.")
    for row in site.db.tip_recipients(case_id):
        _send(site, row["id"], row["email"], f"tip:{case_id}", TIP_WINDOW_S, _subject("New tip"), body + _footer())


def alert_now(config: SmtpConfig, recipients: Sequence[str], subject: str, body: str) -> int:
    """Send straight away (for the backup job, which has no web request or background mailer). Returns the count."""
    messages = [build_message(config, to, subject, body) for to in dict.fromkeys(recipients)]
    if messages:
        deliver(config, messages)
    return len(messages)
