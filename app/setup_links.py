"""
One-time setup links (walkthrough note 18, Nov. 1, 2026). An administrator never sets or learns a person's password,
and never enrolls their second factor:

- A new user gets a link by email when their access is active (for a decision role, when the second administrator
  approves the grant). The link sets their password, then enrolls their authenticator.
- A forgotten password: the administrator sends a reset link; the person sets a new password and signs in with their
  authenticator as usual.
- A lost authenticator: the administrator resets it and the person gets a link; with the link and their current
  password they enroll a new one. A password sign-in never enrolls a second factor.

A link works once and expires after LINK_HOURS. Only a SHA-256 hash of the token is stored, and the outbox withholds
the link from everyone who reads it there: it goes to the person's email (Microsoft 365 when configured).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .orm import SetupLink, User, utcnow

LINK_HOURS = 72
MIN_PASSWORD = 12
PURPOSES = {"new_user": "Set up your account", "password_reset": "Reset your password",
            "mfa_reset": "Set up your authenticator again"}
TEMPLATE = "account_setup"
_TOKEN = re.compile(r"token=[A-Za-z0-9_-]+")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def redact(body: str) -> str:
    """What the outbox shows: the link without its token."""
    return _TOKEN.sub("token=[withheld: only the recipient gets the link]", body)


def needs_setup(db: Session, user: User) -> bool:
    """A person who has never finished setting up (no password of their own and no authenticator)."""
    return not user.is_demo and user.mfa_enabled_at is None and not user.password_hash


def issue(db: Session, user: User, actor: User | None, purpose: str) -> SetupLink:
    """Email the person a new link; any earlier unused link of theirs stops working."""
    from .mail import PORTAL_URL, queue
    if purpose not in PURPOSES:
        raise ValueError(purpose)
    if not user.email:
        raise HTTPException(422, f"{user.display_name} has no email address: add one so the setup link can reach them")
    now = utcnow()
    for old in db.scalars(select(SetupLink).where(SetupLink.user_id == user.id, SetupLink.used_at.is_(None))).all():
        if _aware(old.expires_at) > now:
            old.expires_at = now
    token = secrets.token_urlsafe(32)
    link = SetupLink(user_id=user.id, purpose=purpose, token_hash=_hash(token), sent_to=user.email,
                     created_by=actor.display_name if actor else "system", created_at=now,
                     expires_at=now + timedelta(hours=LINK_HOURS))
    db.add(link)
    db.flush()
    base = PORTAL_URL.rsplit("/portal", 1)[0]
    what = {"new_user": "set your password, then add the platform to your authenticator app",
            "password_reset": "set a new password; you'll sign in with your authenticator app as usual",
            "mfa_reset": "add the platform to your authenticator app again (you'll need your current password)"}[purpose]
    queue(db, to=user.email, to_name=user.display_name, template=TEMPLATE, subject=PURPOSES[purpose],
          body=f"Hello {user.display_name.split()[0]},\n\nUse this link to {what}:\n{base}/setup?token={token}\n\n"
               f"It works once and expires in {LINK_HOURS} hours. Your username is {user.username}. Nobody at the "
               "custodian will ask you for your password or a code from your authenticator.\n\nIf you didn't expect "
               "this email, tell your administrator.")
    audit.record(db, actor, "setup_link_sent", "user", user.id, purpose=purpose, sent_to=user.email,
                 expires_at=link.expires_at.isoformat())
    return link


def find(db: Session, token: str) -> tuple[SetupLink, User]:
    link = db.scalar(select(SetupLink).where(SetupLink.token_hash == _hash(token or "")))
    if link is None or link.used_at is not None or _aware(link.expires_at) <= utcnow():
        raise HTTPException(410, "This link has expired or was already used: ask your administrator for a new one")
    user = db.get(User, link.user_id)
    if user is None or not user.active:
        raise HTTPException(410, "This account isn't active: ask your administrator")
    return link, user


def check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(422, f"Use at least {MIN_PASSWORD} characters")
