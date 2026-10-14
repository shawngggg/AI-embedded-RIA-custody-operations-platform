"""
Undoing a principal's acceptance.

Nothing is erased. A registered principal asks to rescind, with a written reason, and the account is
restricted at once (code RSCN, no activity), because a regulatory problem can't wait for an approval queue.
The RIA service team lead, never the requester, approves or rejects the rescission (segregation of duties).

Approved: the acceptance is withdrawn, the account or relationship is no longer open, and the case goes back
to the principal for review or to operations for rework. Every approved rescission is logged as an
operational risk event, so the team agrees a remediation path: training, a technology change, or a procedure
update. Rejected: the restriction comes off and the acceptance stands.

An issue that isn't regulatory doesn't need a rescission: reopening the document behind a non-regulatory item
keeps the account open, restricted from trading until the item is fixed (see routers/documents.py).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from restrictions import CATALOG

from . import audit
from .acceptance import holds_principal
from .orm import LICENSED_ROLES, Account, Case, Firm, OpsRiskEvent, PendingDecision, Restriction, User, utcnow
from .risk_register import by_key, log_event
from .runner import run_case
from .traceability import stamp

OPENED = ("OPEN", "OPEN_RESTRICTED", "ACTIVE")
KINDS = {"accepted_before_review": "Accepted before the review was finished",
         "regulatory_issue": "Regulatory issue found after acceptance (KYC, CDD, EDD, KYB, CIP, or AML)"}
RETURN_TO = {"review": "back to the principal for review", "operations": "back to operations for rework"}


def pending(db: Session, case: Case) -> PendingDecision | None:
    return db.scalar(select(PendingDecision).where(PendingDecision.case_id == case.id,
                                                   PendingDecision.decision_type == "rescission",
                                                   PendingDecision.status == "pending"))


def refusal(db: Session, case: Case, user: User) -> str | None:
    if case.kind not in ("firm", "account") or case.status not in OPENED:
        return "Only an accepted, open account or relationship can be rescinded"
    if user.role not in LICENSED_ROLES or not holds_principal(user):
        return "A registered principal on the RIA service team asks to rescind an acceptance"
    if pending(db, case):
        return "A rescission is already waiting for the RIA service team lead"
    if case.kind == "firm":
        open_accounts = db.scalar(select(Account).where(Account.firm_id == case.firm_id, Account.status == "OPEN"))
        if open_accounts is not None:
            return ("The firm has open accounts, so its relationship can't be un-opened: restrict the firm "
                    "and its accounts instead")
    return None


def request(db: Session, case: Case, user: User, kind: str, reason: str, return_to: str) -> PendingDecision:
    if kind not in KINDS or return_to not in RETURN_TO:
        raise HTTPException(422, "Choose why, and where the case goes")
    if len(reason.strip()) < 10:
        raise HTTPException(422, "Write the reason (at least a sentence)")
    why = refusal(db, case, user)
    if why:
        if case.status in OPENED:
            audit.record(db, user, "rescission_refused", "case", case.id, case_id=case.id, reason=why)
            db.commit()
        raise HTTPException(403 if "principal" in why else 409, why)
    n = db.scalar(select(PendingDecision).where(PendingDecision.decision_type == "rescission")
                  .order_by(PendingDecision.id.desc()))
    ref = f"RSC-{case.id:04d}-{(n.id + 1) if n else 1}"
    hold = Restriction(target_id=case.target_id, code="RSCN", owner=CATALOG["RSCN"].owner.value, source="manual",
                       reason=f"{ref}: {KINDS[kind]}. {reason.strip()}", placed_by=user.display_name)
    db.add(hold)
    d = PendingDecision(case_id=case.id, decision_type="rescission", value=return_to, reference=ref,
                        note=f"{KINDS[kind]}: {reason.strip()}", proposed_by=user.id)
    db.add(d)
    db.flush()
    audit.record(db, user, "restriction_placed", "restriction", hold.id, case_id=case.id, code="RSCN",
                 target=case.target_id, owner=hold.owner, reason=hold.reason)
    audit.record(db, user, "rescission_requested", "case", case.id, case_id=case.id, reference=ref, kind=kind,
                 return_to=return_to, reason=reason.strip(), **stamp(case, "rescission"))
    log_event(db, user, category="Rescission requested",
              risk_type="regulatory" if kind == "regulatory_issue" else "operational", source="rescission",
              summary=f"{ref}: {KINDS[kind]}. {reason.strip()}", area="ria_service",
              detected_by=user.display_name, case=case, dedupe_key=f"rescission:{ref}")
    return d


def _release_hold(db: Session, case: Case, user: User, ref: str, why: str) -> None:
    for r in db.scalars(select(Restriction).where(Restriction.target_id == case.target_id, Restriction.code == "RSCN",
                                                  Restriction.active.is_(True))).all():
        if not r.reason.startswith(ref):
            continue
        r.active, r.removed_at, r.removed_by = False, utcnow(), user.display_name
        r.removal_reason, r.authorization = why, ref
        audit.record(db, user, "restriction_removed", "restriction", r.id, case_id=case.id, code="RSCN",
                     target=r.target_id, reason=why, authorization=ref)


def approve(db: Session, d: PendingDecision, case: Case, lead: User) -> None:
    requester = db.get(User, d.proposed_by)
    decisions = {k: v for k, v in (case.decisions or {}).items() if k not in ("acceptance", "acceptance_by")}
    case.decisions = decisions
    if d.value == "operations":
        case.extra = {**(case.extra or {}), "rework": {
            "reason": d.note, "reference": d.reference, "requested_by": requester.display_name,
            "approved_by": lead.display_name, "at": utcnow().isoformat()}}
    if case.kind == "account":
        account = db.get(Account, case.account_id)
        account.status, account.opened_at = "PENDING", None
    else:
        firm = db.get(Firm, case.firm_id)
        firm.status, firm.activated_at = "ONBOARDING", None
    _release_hold(db, case, lead, d.reference, f"Rescission approved; the {case.kind} is no longer open")
    event = by_key(db, f"rescission:{d.reference}") or log_event(
        db, lead, category="Rescission requested", risk_type="operational", source="rescission",
        summary=f"{d.reference}: {d.note}", area="ria_service", detected_by=requester.display_name, case=case,
        dedupe_key=f"rescission:{d.reference}")
    event.category, event.approved_by = "Principal acceptance rescinded", lead.display_name
    audit.record(db, lead, "acceptance_rescinded", "case", case.id, case_id=case.id, reference=d.reference,
                 returned=RETURN_TO[d.value], requested_by=requester.display_name)
    audit.record(db, lead, "ops_risk_event_updated", "ops_risk_event", event.id, case_id=case.id,
                 category=event.category)
    run_case(db, case, lead)


def reject(db: Session, d: PendingDecision, case: Case, lead: User) -> None:
    _release_hold(db, case, lead, d.reference, "Rescission rejected by the RIA service team lead; the acceptance stands")
    event = by_key(db, f"rescission:{d.reference}")
    if event is not None:
        event.category = "Rescission rejected"
        event.summary += f" Rejected by {lead.display_name}: the acceptance stands."
        audit.record(db, lead, "ops_risk_event_updated", "ops_risk_event", event.id, case_id=case.id,
                     category=event.category)
