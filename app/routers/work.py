"""The Work page: everyone's landing page in the workbench, built around what needs that person now."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import capabilities, require_capability
from ..config import settings
from ..db import get_db
from ..orm import OPS_TEAMS, Case, OpsRiskEvent, PendingDecision, Restriction, Review, RoleGrant, User
from ..queues import DECISIONS, QUEUES, works_queue
from ..risk_register import sweep_sla_breaches, visible
from ..views import case_summary, decision_dict, review_dict, sla_state

router = APIRouter(prefix="/api", tags=["work"])


def _sla_key(c: Case) -> datetime:
    due = c.sla_due or datetime.max.replace(tzinfo=timezone.utc)
    return due if due.tzinfo else due.replace(tzinfo=timezone.utc)


def _titled(db: Session, d: PendingDecision) -> dict:
    case = db.get(Case, d.case_id)
    return {**decision_dict(db, d), "case_title": case.title if case else None}


@router.get("/work")
def work(user: User = Depends(require_capability("work")), db: Session = Depends(get_db)):
    can = capabilities(user)
    mine = [k for k in QUEUES if works_queue(user, k)]
    cases = sorted(db.scalars(select(Case).where(Case.queue.in_(mine))).all(), key=_sla_key) if mine else []
    now = datetime.now(timezone.utc)
    pending = db.scalars(select(PendingDecision).where(PendingDecision.status == "pending")
                         .order_by(PendingDecision.proposed_at)).all()
    to_approve = [d for d in pending if user.role in DECISIONS[d.decision_type]["approver"] and d.proposed_by != user.id]
    my_proposals = [d for d in pending if d.proposed_by == user.id]
    if sweep_sla_breaches(db):
        db.commit()
    events = [e for e in db.scalars(select(OpsRiskEvent).where(OpsRiskEvent.status != "closed")).all()
              if visible(user, e)]
    restricted = {r.target_id for r in db.scalars(select(Restriction).where(Restriction.active.is_(True))).all()}
    out = {
        "greeting_name": user.display_name.split()[0],
        "role_label": user.role,
        "team_label": "Every operations" if user.role == "ops_supervisor" else OPS_TEAMS.get(user.team) if user.team else None,
        "queues": [{"key": k, "label": QUEUES[k]["label"], "count": sum(c.queue == k for c in cases),
                    "breached": sum(c.queue == k and sla_state(c, now)["breached"] for c in cases)} for k in mine],
        "needs_you": [case_summary(db, c) for c in cases[:12]],
        "needs_you_total": len(cases),
        "breached": sum(sla_state(c, now)["breached"] for c in cases),
        "to_approve": [_titled(db, d) for d in to_approve] if can["approvals"] else [],
        "my_proposals": [_titled(db, d) for d in my_proposals],
        "risk_events_open": len(events),
        "risk_events_mine": sum(e.owner == user.role for e in events),
        "restricted_targets": len(restricted),
    }
    if can["messages"]:
        from .messages import unread_for
        out["messages_unread"] = sum(unread_for(db, user).values())
    if user.role == "periodic_review":
        today = settings.today()
        reviews = [review_dict(r, today) for r in db.scalars(select(Review)).all()]
        out["reviews_due"] = sum(1 for r in reviews if r["due"] or r["overdue"])
        out["reviews_overdue"] = sum(1 for r in reviews if r["overdue"])
    if user.role == "platform_admin":
        out["grants_waiting"] = len([g for g in db.scalars(select(RoleGrant).where(RoleGrant.status == "pending")).all()
                                     if g.requested_by != user.id])
    return out
