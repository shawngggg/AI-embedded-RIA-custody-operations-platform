"""
Field encryption at rest (PRD NF-4): tax IDs and dates of birth are stored encrypted wherever they sit in an
application, a case's result, or an account or firm record.

The JSON columns that carry people use ProtectedJSON: on the way into the database every value under a sensitive key
(tax_id, date_of_birth) is sealed with AES-256-GCM, the key name bound in as associated data so a value can't be moved
to another field; on the way out it's opened again. The app works with plain values; the database, its backups, and
anyone reading it directly see "enc:v1:..." tokens. A plain value (data written before this) reads as it is.

The key is FIELD_KEY (32 bytes, base64, from Secret Manager). Without it the key is derived from SECRET_KEY, which is
fine for the demo but ties the two together: set FIELD_KEY in production (docs/DEPLOY.md).
"""
from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

from .config import settings

PREFIX = "enc:v1:"
UNREADABLE = "(sealed with another key)"
SENSITIVE = frozenset({"tax_id", "date_of_birth"})


@lru_cache(maxsize=4)
def _key(field_key: str, secret_key: str) -> bytes:
    if field_key:
        raw = base64.urlsafe_b64decode(field_key + "=" * (-len(field_key) % 4))
        if len(raw) != 32:
            raise RuntimeError("FIELD_KEY must be 32 bytes, base64-encoded")
        return raw
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ria-custody field encryption v1") \
        .derive(secret_key.encode())


def key() -> bytes:
    return _key(os.environ.get("FIELD_KEY", ""), settings.secret_key)


def seal(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.startswith(PREFIX):
        return value
    nonce = os.urandom(12)
    box = AESGCM(key()).encrypt(nonce, value.encode(), field.encode())
    return PREFIX + base64.urlsafe_b64encode(nonce + box).decode().rstrip("=")


def open_(value, field: str):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    raw = base64.urlsafe_b64decode(value[len(PREFIX):] + "=" * (-len(value[len(PREFIX):]) % 4))
    try:
        return AESGCM(key()).decrypt(raw[:12], raw[12:], field.encode()).decode()
    except InvalidTag:                     # sealed under another key (FIELD_KEY changed): say so, don't break the page
        return UNREADABLE


def _walk(obj, fn):
    if isinstance(obj, dict):
        return {k: (fn(v, k) if k in SENSITIVE else _walk(v, fn)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(x, fn) for x in obj]
    return obj


def seal_all(obj):
    return _walk(obj, seal)


def open_all(obj):
    return _walk(obj, open_)


class ProtectedJSON(TypeDecorator):
    """JSON whose tax IDs and dates of birth are encrypted in the database."""
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return seal_all(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return open_all(value) if value is not None else None


def mask_tax_id(value: str | None) -> str | None:
    """What most screens show of a tax ID: the last 4."""
    if not value:
        return value
    digits = [c for c in value if c.isdigit()]
    return f"•••-••-{''.join(digits[-4:])}" if len(digits) >= 4 else "••••"


def mask_tax_ids(obj):
    """The same structure with every tax ID cut to its last 4 (for people who don't verify identity)."""
    if isinstance(obj, dict):
        return {k: (mask_tax_id(v) if k == "tax_id" and isinstance(v, str) else mask_tax_ids(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_tax_ids(x) for x in obj]
    return obj


def sees_tax_ids(user) -> bool:
    """Full tax IDs for the people who verify identity or screen it: onboarding, CIP, the operations supervisor, AML
    compliance, and the sanctions team. Everyone else sees the last 4."""
    return user.role in ("ops_supervisor", "aml_compliance", "sanctions") or (
        user.role == "ops_analyst" and user.team in ("onboarding", "cip"))
