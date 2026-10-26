"""Sign-in: password accounts, and demo sign-in by role when demo mode is on."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, totp
from ..crypto import UNREADABLE, open_, seal
from ..config import settings
from ..db import get_db
from ..orm import User, utcnow
from ..security import COOKIE, current_user, make_token, verify_password
from ..views import user_dict

router = APIRouter(prefix="/api/auth", tags=["auth"])


class DemoSignIn(BaseModel):
    username: str


class Login(BaseModel):
    username: str
    password: str


def _set_cookie(response: Response, user: User) -> None:
    response.set_cookie(COOKIE, make_token(user), httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600, path="/")


@router.get("/demo-users")
def demo_users(db: Session = Depends(get_db)):
    if not settings.demo_mode:
        return {"demo_mode": False, "users": []}
    users = db.scalars(select(User).where(User.is_demo.is_(True), User.active.is_(True)).order_by(User.id)).all()
    return {"demo_mode": True, "users": [user_dict(db, u) for u in users]}


@router.post("/demo")
def demo_sign_in(body: DemoSignIn, response: Response, db: Session = Depends(get_db)):
    if not settings.demo_mode:
        raise HTTPException(403, "Demo sign-in is off")
    user = db.scalar(select(User).where(User.username == body.username, User.is_demo.is_(True)))
    if user is None or not user.active:
        raise HTTPException(404, "No such demo user")
    _set_cookie(response, user)
    audit.record(db, user, "signed_in", "user", user.id, method="demo")
    db.commit()
    return user_dict(db, user)


def _failed_sign_in(db: Session, username: str) -> None:
    """Log the failure; repeated failures for one username raise an access and authentication risk event."""
    from datetime import timedelta
    from ..orm import AuditEvent, utcnow
    from ..risk_register import FAILED_SIGN_INS, FAILED_WINDOW_MINUTES, log_access_event
    name = username[:60]
    audit.record(db, None, "sign_in_failed", "user", name, username=name)
    db.flush()
    since = utcnow() - timedelta(minutes=FAILED_WINDOW_MINUTES)
    recent = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "sign_in_failed",
                                                             AuditEvent.target_id == name)).all()
              if (e.at if e.at.tzinfo else e.at.replace(tzinfo=since.tzinfo)) >= since]
    if len(recent) >= FAILED_SIGN_INS:
        log_access_event(db, None, category="Repeated failed sign-ins", target_id=name,
                         summary=f"{len(recent)} failed password sign-ins for '{name}' within {FAILED_WINDOW_MINUTES} "
                                 "minutes. Check whether it's the person or someone guessing.",
                         dedupe_key=f"access:sign-in:{name}:{since:%Y-%m-%d}")
    db.commit()


@router.post("/login")
def login(body: Login, db: Session = Depends(get_db)):
    """Step one of a password sign-in. A right password never signs in by itself: it returns a challenge for the
    second factor, or, the first time, the secret to put in an authenticator app."""
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        _failed_sign_in(db, body.username)
        raise HTTPException(401, "Username or password is wrong")
    if user.mfa_enabled_at:
        audit.record(db, user, "password_verified", "user", user.id, next="totp")
        db.commit()
        return {"mfa_required": True, "challenge": totp.challenge(user.id, "mfa")}
    secret = totp.new_secret()
    user.mfa_secret = seal(secret, "mfa_secret")
    audit.record(db, user, "mfa_enrollment_started", "user", user.id)
    db.commit()
    return {"mfa_setup_required": True, "challenge": totp.challenge(user.id, "mfa_setup"), "secret": secret,
            "otpauth": totp.otpauth_uri(secret, user.username)}


class SecondFactor(BaseModel):
    challenge: str
    code: str


def _second_factor(body: SecondFactor, purpose: str, response: Response, db: Session) -> dict:
    uid = totp.read_challenge(body.challenge, purpose)
    user = db.get(User, uid) if uid else None
    if user is None or not user.active or not user.mfa_secret:
        raise HTTPException(401, "The sign-in expired; start again")
    secret = open_(user.mfa_secret, "mfa_secret")
    if secret == UNREADABLE:
        raise HTTPException(401, "Your second factor needs resetting: ask your administrator")
    step = totp.verify(secret, body.code, user.mfa_last_step)
    if step is None:
        _failed_sign_in(db, user.username)
        raise HTTPException(401, "That code didn't match; try the current one")
    user.mfa_last_step = step
    if purpose == "mfa_setup":
        user.mfa_enabled_at = utcnow()
        audit.record(db, user, "mfa_enrolled", "user", user.id)
    _set_cookie(response, user)
    audit.record(db, user, "signed_in", "user", user.id, method="password+totp")
    db.commit()
    return user_dict(db, user)


@router.post("/mfa")
def mfa(body: SecondFactor, response: Response, db: Session = Depends(get_db)):
    """Step two: the code from the authenticator app."""
    return _second_factor(body, "mfa", response, db)


@router.post("/mfa/enroll")
def mfa_enroll(body: SecondFactor, response: Response, db: Session = Depends(get_db)):
    """First sign-in: the first code from the new authenticator entry turns the second factor on."""
    return _second_factor(body, "mfa_setup", response, db)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return user_dict(db, user)


@router.get("/session")
def session(request: Request, db: Session = Depends(get_db)):
    """Who is signed in, or null. Lets the screens check without a 401 on every first visit."""
    try:
        user = current_user(request, db)
    except HTTPException:
        return {"user": None}
    return {"user": user_dict(db, user)}
