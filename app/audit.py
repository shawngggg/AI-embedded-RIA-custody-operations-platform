"""The append-only audit log."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .orm import AuditEvent, User


def record(db: Session, actor: User | None, action: str, target_type: str, target_id, *,
           case_id: int | None = None, **detail) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_name=actor.display_name if actor else "system",
        actor_role=actor.role if actor else "system",
        action=action, target_type=target_type, target_id=str(target_id), case_id=case_id,
        detail={k: v for k, v in detail.items() if v is not None},
    )
    db.add(event)
    return event


def to_dict(e: AuditEvent) -> dict:
    return {"id": e.id, "at": e.at.isoformat() if e.at else None, "actor": e.actor_name, "role": e.actor_role,
            "action": e.action, "target_type": e.target_type, "target_id": e.target_id, "case_id": e.case_id,
            "detail": e.detail}
