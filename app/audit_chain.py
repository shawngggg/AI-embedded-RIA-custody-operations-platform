"""
A hash chain over the audit log, so an export proves what it held and a later export proves nothing earlier changed.

Each event's hash is SHA-256 over the previous event's hash and the event's canonical JSON (keys sorted, times in UTC).
An export carries every event with its hash and the head (the last id and hash), and records that head in the log
(`audit_exported`). The next export recomputes the chain and checks it against every head recorded since: if any
earlier event was changed, removed, or reordered, the recomputed hash at that head differs. `tools/verify_audit_export.py`
checks a downloaded export on its own.

This complements the database guard (app/audit_guard.py), which stops changes in the first place.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .orm import AuditEvent

FORMAT = "sha256-chain-v1"
GENESIS = hashlib.sha256(b"ria-custody audit log: genesis").hexdigest()


def canonical(e: dict) -> str:
    """The bytes an event's hash covers: the exported fields, keys sorted, no whitespace."""
    body = {k: e[k] for k in ("id", "at", "actor", "role", "action", "target_type", "target_id", "case_id", "detail")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def event_dict(e: AuditEvent) -> dict:
    at = e.at if e.at is None or e.at.tzinfo else e.at.replace(tzinfo=timezone.utc)
    return {"id": e.id, "at": at.astimezone(timezone.utc).isoformat() if at else None, "actor": e.actor_name,
            "role": e.actor_role, "action": e.action, "target_type": e.target_type, "target_id": e.target_id,
            "case_id": e.case_id, "detail": e.detail}


def link(prev: str, e: dict) -> str:
    return hashlib.sha256((prev + canonical(e)).encode()).hexdigest()


def chain(events: list[dict]) -> list[dict]:
    prev, out = GENESIS, []
    for e in events:
        prev = link(prev, e)
        out.append({**e, "hash": prev})
    return out


def verify(events: list[dict]) -> int | None:
    """The id of the first event whose hash doesn't follow from the chain, or None when every link holds."""
    prev = GENESIS
    for e in events:
        prev = link(prev, e)
        if e.get("hash") != prev:
            return e["id"]
    return None


def export(db: Session) -> dict:
    events = chain([event_dict(e) for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()])
    at = {e["id"]: e["hash"] for e in events}
    checkpoints = []
    for e in events:
        if e["action"] == "audit_exported" and e["detail"].get("head_id") is not None:
            hid, hh = e["detail"]["head_id"], e["detail"]["head_hash"]
            checkpoints.append({"recorded_in": e["id"], "head_id": hid, "head_hash": hh, "ok": at.get(hid) == hh})
    head = {"id": events[-1]["id"], "hash": events[-1]["hash"]} if events else {"id": None, "hash": GENESIS}
    return {"format": FORMAT, "genesis": GENESIS, "head": head, "events": events, "checkpoints": checkpoints,
            "verified": all(c["ok"] for c in checkpoints)}
