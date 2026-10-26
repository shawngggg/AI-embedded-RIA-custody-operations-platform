"""Password hashing, session tokens, and role guards."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from . import audit
from .orm import INTERNAL_ROLES, User

COOKIE = "session"
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, digest_b64 = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=_N, r=_R, p=_P, dklen=32)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def make_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "role": user.role, "iat": now,
               "exp": now + timedelta(hours=settings.session_hours)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    if not token:
        raise HTTPException(401, "Sign in first")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired; sign in again")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(401, "Account not active")
    return user


def optional_user(request: Request, db: Session) -> User | None:
    """The signed-in user, or None (for an endpoint a scheduler can also call with its own token)."""
    try:
        return current_user(request, db)
    except HTTPException:
        return None


def require_roles(*roles: str):
    def guard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if user.role not in roles:
            audit.refuse(db, user, "access", f"This needs one of these roles: {', '.join(roles)}",
                         target_type="endpoint", target_id=f"{request.method} {request.url.path}")
        return user
    return guard


def require_internal(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if user.role not in INTERNAL_ROLES:
        audit.refuse(db, user, "access", "Internal teams only", target_type="endpoint",
                     target_id=f"{request.method} {request.url.path}")
    return user
