"""
The risk event register: operational, regulatory, and financial events, each with an owner who records the
remediation path and closes it.

Events come from the platform (an approved or requested rescission, a document reopened on an open account, a
sanctions true match, a case past its SLA, and access and authentication events) or from any team member who reports
one (Shawn, Oct. 13, 2026).

Each event belongs to the area it pertains to, and each person sees only their own area's events, plus any they
reported: an onboarding analyst sees onboarding's, the platform administrator sees access and authentication.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .orm import OPS_TEAMS, Case, OpsRiskEvent, User
from .queues import QUEUES

RISK_TYPES = {"operational": "Operational", "regulatory": "Regulatory", "financial": "Financial"}
SOURCES = {"rescission": "Rescission", "document": "Document reopened", "sanctions": "Sanctions true match",
           "sla": "SLA breach", "access": "Access control", "manual": "Reported by a team member",
           "recon": "Reconciliation break (aged or large)",
           "protection": "Customer protection (Rule 15c3-3): reserve or possession-or-control deficit"}
# The function a risk pertains to: every operations team, the second-line and service teams, and access
AREAS = {**OPS_TEAMS, "aml": "AML compliance (EDD and compliance review)", "sanctions": "Sanctions",
         "periodic_review": "Periodic review", "ria_service": "RIA service (principal acceptance)",
         "access": "Access and authentication"}
# Who owns the follow-up: the lead or second-line team for the area
OWNERS = {"ops_supervisor": "Operations (supervisor)", "aml_compliance": "AML compliance",
          "sanctions": "Sanctions team", "periodic_review": "Periodic review team",
          "ria_service_lead": "RIA service team (lead)", "platform_admin": "Platform administration"}
AREA_OWNER = {**{t: "ops_supervisor" for t in OPS_TEAMS}, "aml": "aml_compliance", "sanctions": "sanctions",
              "periodic_review": "periodic_review", "ria_service": "ria_service_lead", "access": "platform_admin"}
QUEUE_AREA = {"items_requested": "onboarding", "rework": "onboarding", "verification": "cip",
              "confirmation": "client_service", "maintenance_items": "client_service", "compliance_review": "aml",
              "edd_review": "aml", "sanctions_review": "sanctions", "principal_acceptance": "ria_service",
              "money_release": "asset_transfer", "callbacks": "asset_transfer", "transfer_exceptions": "asset_transfer",
              "in_transit": "asset_transfer", "transfers_waiting": "asset_transfer", "product_review": "product",
              "lifecycle": "client_service", "transitions": "transitions"}
ROLE_AREAS = {"aml_compliance": {"aml"}, "sanctions": {"sanctions"}, "periodic_review": {"periodic_review"},
              "ria_service": {"ria_service"}, "ria_service_lead": {"ria_service"}, "platform_admin": {"access"},
              "ops_supervisor": set(OPS_TEAMS)}       # the supervisor oversees every operations team


def areas_for(user: User) -> set[str]:
    """The areas whose risk events this person sees: an analyst's own team, or the role's function."""
    if user.role == "ops_analyst":
        return {user.team} if user.team else set()
    return ROLE_AREAS.get(user.role, set())


def visible(user: User, e: OpsRiskEvent) -> bool:
    return e.area in areas_for(user) or e.reported_by == user.id


def area_for_case(case: Case) -> str:
    """Account changes are account maintenance, which sits with client service; openings are onboarding's."""
    return "client_service" if case.kind == "change" else "onboarding"


def log_event(db: Session, actor: User | None, *, category: str, risk_type: str, source: str, summary: str,
              area: str, detected_by: str, owner: str | None = None, case: Case | None = None,
              target_id: str | None = None, approved_by: str | None = None, dedupe_key: str | None = None,
              reported_by: User | None = None) -> OpsRiskEvent | None:
    """Add an event to the register (once per dedupe key) and log it. The owner defaults to the area's lead."""
    if dedupe_key and db.scalar(select(OpsRiskEvent).where(OpsRiskEvent.dedupe_key == dedupe_key)):
        return None
    e = OpsRiskEvent(category=category, risk_type=risk_type, source=source, summary=summary, area=area,
                     owner=owner or AREA_OWNER[area], detected_by=detected_by, approved_by=approved_by,
                     case_id=case.id if case else None, target_id=target_id or (case.target_id if case else None),
                     dedupe_key=dedupe_key, reported_by=reported_by.id if reported_by else None)
    db.add(e)
    db.flush()
    audit.record(db, actor, "ops_risk_event_logged", "ops_risk_event", e.id, case_id=e.case_id, category=category,
                 risk_type=risk_type, source=source, area=area)
    return e


def log_access_event(db: Session, actor: User | None, *, category: str, summary: str, dedupe_key: str,
                     target_id: str | None = None) -> OpsRiskEvent | None:
    """An access and authentication event, for the platform administrator: logged once per dedupe key."""
    return log_event(db, actor, category=category, risk_type="operational", source="access", summary=summary,
                     area="access", detected_by="Platform (access control)", target_id=target_id,
                     dedupe_key=dedupe_key)


FAILED_SIGN_INS = 5          # failed password sign-ins for one username within the window raise an event
FAILED_WINDOW_MINUTES = 15


def by_key(db: Session, key: str) -> OpsRiskEvent | None:
    return db.scalar(select(OpsRiskEvent).where(OpsRiskEvent.dedupe_key == key))


def sweep_sla_breaches(db: Session) -> int:
    """Log an operational event, once, for every case waiting past its queue's SLA."""
    now = datetime.now(timezone.utc)
    added = 0
    for case in db.scalars(select(Case).where(Case.queue.is_not(None), Case.sla_due.is_not(None))).all():
        due = case.sla_due if case.sla_due.tzinfo else case.sla_due.replace(tzinfo=timezone.utc)
        if due >= now or case.queue not in QUEUE_AREA:
            continue
        spec = QUEUES[case.queue]
        e = log_event(db, None, category=f"SLA breach: {spec['label']}", risk_type="operational", source="sla",
                      summary=f"{case.title} has waited in {spec['label']} past its {spec['sla_hours']}-hour SLA "
                              f"(due {due:%b %d, %H:%M} UTC).",
                      area=QUEUE_AREA[case.queue], detected_by="Platform (SLA clock)", case=case,
                      dedupe_key=f"sla:{case.id}:{case.queue}")
        added += e is not None
    return added
