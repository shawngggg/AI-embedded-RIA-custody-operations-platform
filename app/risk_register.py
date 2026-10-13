"""
The risk event register: operational, regulatory, and financial events, each with an owner who records the
remediation path and closes it.

Events come from the platform (an approved or requested rescission, a document reopened on an open account, a
sanctions true match, a case past its SLA) or from any team member who reports one (Shawn, Oct. 13, 2026).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .orm import Case, OpsRiskEvent, User
from .queues import QUEUES

RISK_TYPES = {"operational": "Operational", "regulatory": "Regulatory", "financial": "Financial"}
SOURCES = {"rescission": "Rescission", "document": "Document reopened", "sanctions": "Sanctions true match",
           "sla": "SLA breach", "manual": "Reported by a team member"}
# Who owns the follow-up: the lead or second-line team for the area
OWNERS = {"ops_supervisor": "Operations (supervisor)", "aml_compliance": "AML compliance",
          "sanctions": "Sanctions team", "ria_service_lead": "RIA service team (lead)"}
QUEUE_OWNER = {"items_requested": "ops_supervisor", "confirmation": "ops_supervisor", "verification": "ops_supervisor",
               "rework": "ops_supervisor", "compliance_review": "aml_compliance", "edd_review": "aml_compliance",
               "sanctions_review": "sanctions", "principal_acceptance": "ria_service_lead"}


def log_event(db: Session, actor: User | None, *, category: str, risk_type: str, source: str, summary: str,
              owner: str, detected_by: str, case: Case | None = None, target_id: str | None = None,
              approved_by: str | None = None, dedupe_key: str | None = None) -> OpsRiskEvent | None:
    """Add an event to the register (once per dedupe key) and log it."""
    if dedupe_key and db.scalar(select(OpsRiskEvent).where(OpsRiskEvent.dedupe_key == dedupe_key)):
        return None
    e = OpsRiskEvent(category=category, risk_type=risk_type, source=source, summary=summary, owner=owner,
                     detected_by=detected_by, approved_by=approved_by, case_id=case.id if case else None,
                     target_id=target_id or (case.target_id if case else None), dedupe_key=dedupe_key)
    db.add(e)
    db.flush()
    audit.record(db, actor, "ops_risk_event_logged", "ops_risk_event", e.id, case_id=e.case_id, category=category,
                 risk_type=risk_type, source=source)
    return e


def by_key(db: Session, key: str) -> OpsRiskEvent | None:
    return db.scalar(select(OpsRiskEvent).where(OpsRiskEvent.dedupe_key == key))


def sweep_sla_breaches(db: Session) -> int:
    """Log an operational event, once, for every case waiting past its queue's SLA."""
    now = datetime.now(timezone.utc)
    added = 0
    for case in db.scalars(select(Case).where(Case.queue.is_not(None), Case.sla_due.is_not(None))).all():
        due = case.sla_due if case.sla_due.tzinfo else case.sla_due.replace(tzinfo=timezone.utc)
        if due >= now or case.queue not in QUEUE_OWNER:
            continue
        spec = QUEUES[case.queue]
        e = log_event(db, None, category=f"SLA breach: {spec['label']}", risk_type="operational", source="sla",
                      summary=f"{case.title} has waited in {spec['label']} past its {spec['sla_hours']}-hour SLA "
                              f"(due {due:%b %d, %H:%M} UTC).",
                      owner=QUEUE_OWNER[case.queue], detected_by="Platform (SLA clock)", case=case,
                      dedupe_key=f"sla:{case.id}:{case.queue}")
        added += e is not None
    return added
