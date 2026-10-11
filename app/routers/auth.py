"""Sign-in: password accounts, and demo sign-in by role when demo mode is on."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..orm import User
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


@router.post("/login")
def login(body: Login, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Username or password is wrong")
    _set_cookie(response, user)
    audit.record(db, user, "signed_in", "user", user.id, method="password")
    db.commit()
    return user_dict(db, user)


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
