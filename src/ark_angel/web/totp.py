"""Time-based one-time passwords (RFC 6238), the codes authenticator apps show.

Standard library only: HMAC-SHA1, 6 digits, 30-second steps, which is what
Google Authenticator, 1Password, Authy, Bitwarden, Aegis and the rest expect.
A code is accepted for one step either side of now, for clock drift, and
never twice: the caller keeps the last step used and passes it back.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Optional
from urllib.parse import quote, urlencode

STEP_S = 30
DIGITS = 6
DRIFT_STEPS = 1
SECRET_BYTES = 20  # 160 bits, as RFC 4226 recommends


def new_secret() -> str:
    """A fresh base32 secret (no padding), the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    """The code for one 30-second step (RFC 4226 HOTP over the step counter)."""
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def step_at(now: float) -> int:
    return int(now // STEP_S)


def verify(secret: str, code: str, now: float, last_step: int = 0) -> Optional[int]:
    """The step ``code`` belongs to, if it's valid now and newer than ``last_step``; otherwise None.

    Every candidate step is compared in constant time, so timing doesn't say how close a guess was.
    """
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = step_at(now)
    match = None
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, step), code) and step > last_step and match is None:
            match = step
    return match


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    """The ``otpauth://`` link a QR code carries (Google Authenticator key-URI format)."""
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                                    "digits": DIGITS, "period": STEP_S})


def qr_svg_data_uri(text: str) -> str:
    """A QR code as an inline SVG data URI (the site's CSP allows ``data:`` images, and no scripts)."""
    import segno

    return segno.make(text, error="m").svg_data_uri(scale=5, dark="#1b1915", light="#ffffff", border=2)


def grouped(secret: str) -> str:
    """``ABCD EFGH ...`` for typing a secret in by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
