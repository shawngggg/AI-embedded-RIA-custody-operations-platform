"""
The second factor for password sign-in: time-based one-time passwords (RFC 6238; SHA-1, 6 digits, 30 seconds), the
codes any authenticator app makes. Demo sign-in by role, which exists only in demo mode, has no password and no second
factor.

    secret = new_secret()                 # base32, shown once at enrollment
    verify(secret, code, last_step) -> the time step used, or None (a code from the step before or after counts,
                                         and a step already used doesn't, so a code works once)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt

from .config import settings

STEP, DIGITS, WINDOW = 30, 6, 1
ISSUER = "RIA Custody Operations (synthetic demo)"


def new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def now_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // STEP)


def verify(secret: str, code: str, last_step: int | None = None, at: float | None = None) -> int | None:
    code = "".join(c for c in (code or "") if c.isdigit())
    if len(code) != DIGITS:
        return None
    current = now_step(at)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, username: str) -> str:
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(username)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def challenge(user_id: int, purpose: str) -> str:
    """A short-lived token that carries a password sign-in to its second step. It isn't a session (security.py)."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "purpose": purpose, "iat": now, "exp": now + timedelta(minutes=5)},
                      settings.secret_key, algorithm="HS256")


def read_challenge(token: str, purpose: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return int(payload["sub"]) if payload.get("purpose") == purpose else None
