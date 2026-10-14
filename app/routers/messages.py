"""
Team channels: chat-style messages between internal team members, with links to the cases and accounts being
worked on. Write "#16" or "case 16" for a case, an account or firm id such as ACC-2001 or FIRM-310001, and
@username to mention someone.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..access import require_capability
from ..db import get_db
from ..orm import Account, Case, Channel, ChannelMessage, ChannelRead, Firm, User
from ..views import iso

router = APIRouter(prefix="/api", tags=["messages"])
messenger = require_capability("messages")

# The channels the platform starts with: one per team in the workbench, plus a general channel
CHANNELS = [
    ("general", "General", "Everyone on the internal teams", []),
    ("onboarding", "Onboarding", "New RIA firms and client accounts, items requested, rework", []),
    ("cip", "CIP and identity verification", "Identity verification of owners, control persons, and new parties", []),
    ("client-service", "Client service", "Account maintenance: changes, returned changes, and fraud-pattern confirmations",
     []),
    ("aml-compliance", "AML compliance", "EDD, compliance reviews, risk escalations", []),
    ("sanctions", "Sanctions", "Flagged-list escalations (sanctions team only)", ["sanctions"]),
    ("rolling-review", "Rolling review", "Periodic and trigger-event reviews", []),
    ("ria-service", "RIA service team", "Principal acceptance, rescissions, RIA relationships", []),
]

CASE_RE = re.compile(r"(?:\bcase\s*#?|(?<![\w&])#)(\d{1,6})\b", re.I)
TARGET_RE = re.compile(r"\b(ACC-\d{3,8}|FIRM-\d{3,10})\b")
MENTION_RE = re.compile(r"(?<![\w.])@([a-z0-9._-]{3,80})", re.I)


def ensure_channels(db: Session) -> None:
    have = set(db.scalars(select(Channel.key)).all())
    for key, name, description, roles in CHANNELS:
        if key not in have:
            db.add(Channel(key=key, name=name, description=description, roles=roles))
    db.flush()


def can_read(user: User, ch: Channel) -> bool:
    return not ch.roles or user.role in ch.roles


def parse_refs(db: Session, body: str) -> tuple[list[dict], list[int]]:
    refs, seen = [], set()
    for m in CASE_RE.finditer(body):
        cid = int(m.group(1))
        case = db.get(Case, cid)
        if case and ("case", cid) not in seen:
            seen.add(("case", cid)); refs.append({"type": "case", "id": cid, "label": case.title})
    for m in TARGET_RE.finditer(body):
        tid = m.group(1).upper()
        if ("target", tid) in seen:
            continue
        if tid.startswith("FIRM-"):
            firm = db.scalar(select(Firm).where(Firm.crd == tid[5:]))
            if firm:
                opening = db.scalar(select(Case.id).where(Case.kind == "firm", Case.firm_id == firm.id).order_by(Case.id))
                seen.add(("target", tid))
                refs.append({"type": "firm", "id": tid, "label": firm.legal_name, "case_id": opening})
        else:
            account = db.get(Account, tid)
            if account:
                opening = db.scalar(select(Case.id).where(Case.kind == "account", Case.account_id == account.id)
                                    .order_by(Case.id))
                seen.add(("target", tid))
                refs.append({"type": "account", "id": tid, "label": account.display_name, "case_id": opening})
    mentions = []
    for m in MENTION_RE.finditer(body):
        u = db.scalar(select(User).where(User.username == m.group(1).lower()))
        if u and u.id not in mentions:
            mentions.append(u.id)
    return refs, mentions


def message_dict(db: Session, msg: ChannelMessage, channel: Channel | None = None, me: User | None = None) -> dict:
    author = db.get(User, msg.author_id)
    names = {u.id: (u.username, u.display_name) for u in db.scalars(select(User).where(User.id.in_(msg.mentions or []))).all()}
    return {"id": msg.id, "channel": channel.key if channel else None, "channel_name": channel.name if channel else None,
            "author": author.display_name if author else "?", "author_username": author.username if author else None,
            "author_role": author.role if author else None, "body": msg.body, "refs": msg.refs or [],
            "mentions": [{"username": names[i][0], "name": names[i][1]} for i in msg.mentions or [] if i in names],
            "mentions_me": bool(me and me.id in (msg.mentions or [])), "created_at": iso(msg.created_at)}


def _read_marker(db: Session, user: User, ch: Channel) -> ChannelRead:
    mark = db.scalar(select(ChannelRead).where(ChannelRead.user_id == user.id, ChannelRead.channel_id == ch.id))
    if mark is None:
        mark = ChannelRead(user_id=user.id, channel_id=ch.id, last_read_id=0)
        db.add(mark)
    return mark


def unread_for(db: Session, user: User) -> dict[str, int]:
    out = {}
    marks = {m.channel_id: m.last_read_id for m in db.scalars(select(ChannelRead).where(ChannelRead.user_id == user.id))}
    for ch in db.scalars(select(Channel)).all():
        if not can_read(user, ch):
            continue
        out[ch.key] = db.scalar(select(func.count()).select_from(ChannelMessage).where(
            ChannelMessage.channel_id == ch.id, ChannelMessage.id > marks.get(ch.id, 0),
            ChannelMessage.author_id != user.id)) or 0
    return out


def _channel(db: Session, user: User, key: str) -> Channel:
    ch = db.scalar(select(Channel).where(Channel.key == key))
    if ch is None:
        raise HTTPException(404, "No such channel")
    if not can_read(user, ch):
        audit.refuse(db, user, "channel_access", f"#{ch.key} is for {', '.join(ch.roles)} only",
                     target_type="channel", target_id=ch.key)
    return ch


@router.get("/channels")
def channels(user: User = Depends(messenger), db: Session = Depends(get_db)):
    ensure_channels(db)
    db.commit()
    unread = unread_for(db, user)
    out = []
    for ch in db.scalars(select(Channel).order_by(Channel.id)).all():
        if not can_read(user, ch):
            continue
        last = db.scalar(select(ChannelMessage).where(ChannelMessage.channel_id == ch.id).order_by(ChannelMessage.id.desc()))
        out.append({"key": ch.key, "name": ch.name, "description": ch.description, "restricted": bool(ch.roles),
                    "unread": unread.get(ch.key, 0), "last": message_dict(db, last, ch, user) if last else None})
    return {"channels": out, "unread_total": sum(unread.values())}


@router.get("/channels/unread")
def unread(user: User = Depends(messenger), db: Session = Depends(get_db)):
    counts = unread_for(db, user)
    return {"unread_total": sum(counts.values()), "by_channel": counts}


@router.get("/channels/{key}/messages")
def channel_messages(key: str, after: int = 0, user: User = Depends(messenger), db: Session = Depends(get_db)):
    ch = _channel(db, user, key)
    rows = db.scalars(select(ChannelMessage).where(ChannelMessage.channel_id == ch.id, ChannelMessage.id > after)
                      .order_by(ChannelMessage.id.desc()).limit(200)).all()[::-1]
    if rows:
        mark = _read_marker(db, user, ch)
        mark.last_read_id = max(mark.last_read_id, rows[-1].id)
        db.commit()
    return [message_dict(db, m, ch, user) for m in rows]


class Post(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/channels/{key}/messages")
def post_message(key: str, body: Post, user: User = Depends(messenger), db: Session = Depends(get_db)):
    ch = _channel(db, user, key)
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "Write a message")
    refs, mentions = parse_refs(db, text)
    msg = ChannelMessage(channel_id=ch.id, author_id=user.id, body=text, refs=refs, mentions=mentions)
    db.add(msg)
    db.flush()
    mark = _read_marker(db, user, ch)
    mark.last_read_id = msg.id
    db.commit()
    return message_dict(db, msg, ch, user)


@router.get("/messages/about")
def messages_about(case_id: int | None = None, target_id: str | None = None, user: User = Depends(messenger),
                   db: Session = Depends(get_db)):
    """Messages that reference a case or an account, across the channels this person can read."""
    channels_by_id = {c.id: c for c in db.scalars(select(Channel)).all() if can_read(user, c)}
    out = []
    for m in db.scalars(select(ChannelMessage).order_by(ChannelMessage.id.desc()).limit(2000)).all():
        if m.channel_id not in channels_by_id:
            continue
        hit = any((r["type"] == "case" and r["id"] == case_id) or (target_id and r["id"] == target_id) for r in m.refs or [])
        if hit:
            out.append(message_dict(db, m, channels_by_id[m.channel_id], user))
    return out[:50]
