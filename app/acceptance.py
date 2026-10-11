"""
Principal acceptance (FINRA Rule 4512).

The custodian runs the trading platform, so it is a FINRA member broker-dealer, and every account it opens,
an RIA's master account included, is accepted by a registered principal. Operations prepares the case and
needs no license; the RIA service team, which works with RIAs on trading, transfers, and account acceptance,
holds the registrations. The app checks the person's registration, not just their role, and the principal
can't be someone who prepared the case.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .runner import run_case
from .orm import (PRINCIPAL_REGISTRATIONS, SHORT_REGISTRATION, AuditEvent, Case, PendingDecision, User,
                  utcnow)

PREPARING_ACTIONS = ("case_created", "items_received", "document_uploaded")


def holds_principal(user: User) -> bool:
    return user.active and bool(PRINCIPAL_REGISTRATIONS & set(user.registrations or []))


def registration_text(user: User) -> str:
    regs = ", ".join(SHORT_REGISTRATION.get(r, r) for r in (user.registrations or []))
    return regs or "none"


def principal_label(user: User) -> str:
    """How the acceptance is signed: name, principal registration, and individual CRD."""
    principal = [SHORT_REGISTRATION[r] for r in user.registrations or [] if r in PRINCIPAL_REGISTRATIONS]
    crd = f", CRD {user.individual_crd}" if user.individual_crd else ""
    return f"{user.display_name} ({' and '.join(principal) or 'no principal registration'}{crd})"


def preparers(db: Session, case: Case) -> set[int]:
    ids = {case.created_by} if case.created_by else set()
    rows = db.scalars(select(AuditEvent.actor_id).where(AuditEvent.case_id == case.id,
                                                        AuditEvent.action.in_(PREPARING_ACTIONS))).all()
    return (ids | set(rows)) - {None}


def refusal(db: Session, case: Case, user: User) -> str | None:
    """Why this person can't accept this case, or None if they can."""
    if case.status != "PENDING_ACCEPTANCE":
        return "The case isn't waiting for principal acceptance"
    if user.role != "ria_service":
        return "A registered principal on the RIA service team accepts accounts"
    if not holds_principal(user):
        return (f"Accepting an account needs a principal registration (Series 24 or Series 9/10). "
                f"Yours: {registration_text(user)}")
    if user.id in preparers(db, case):
        return "You prepared this case, so another principal accepts it"
    return None


def acceptance_state(db: Session, case: Case, user: User) -> dict | None:
    if case.status != "PENDING_ACCEPTANCE":
        return None
    why = refusal(db, case, user)
    return {"can_accept": why is None, "reason": why,
            "signs_as": principal_label(user) if why is None else None}


def accept_case(db: Session, case: Case, user: User, value: str, note: str | None = None) -> None:
    """Record the principal's acceptance or decline and re-run the case. Raises 403 with a logged refusal."""
    if value not in ("accepted", "declined"):
        raise HTTPException(422, "Accept or decline")
    why = refusal(db, case, user)
    if why:
        if case.status == "PENDING_ACCEPTANCE":
            audit.record(db, user, "acceptance_refused", "case", case.id, case_id=case.id, reason=why,
                         registrations=list(user.registrations or []))
            db.commit()
            raise HTTPException(403, why)
        raise HTTPException(409, why)
    signed = principal_label(user)
    now = utcnow()
    db.add(PendingDecision(case_id=case.id, decision_type="acceptance", value=value, reference=signed,
                           note=note, status="approved", proposed_by=user.id, proposed_at=now,
                           decided_by=user.id, decided_at=now))
    decisions = dict(case.decisions or {})
    decisions["acceptance"], decisions["acceptance_by"] = value, signed
    case.decisions = decisions
    audit.record(db, user, "principal_accepted" if value == "accepted" else "principal_declined", "case", case.id,
                 case_id=case.id, signed_as=signed, registrations=list(user.registrations or []),
                 individual_crd=user.individual_crd, note=note)
    run_case(db, case, user)
