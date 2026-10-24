"""
Principal acceptance (FINRA Rule 4512).

The custodian runs the trading platform, so it is a FINRA member broker-dealer, and every account it opens,
an RIA's master account included, is accepted by a registered principal. Operations prepares the case and
needs no license; the RIA service team, which works with RIAs on trading, transfers, and account acceptance,
holds the registrations. The app checks the person's registration, not just their role, and the principal
can't be someone who prepared the case.

The principal reviews every document in good order before accepting (Shawn, Oct. 23, 2026): each is linked on the
acceptance panel with a Reviewed tick, Accept stays off until every one is ticked, and the server refuses an
acceptance that leaves one out. Operations does the first check; the principal can't mark a document in good order,
but can send the case back to operations with a reason and the documents in question (rework).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit
from .runner import run_case
from .traceability import stamp
from .orm import (LICENSED_ROLES, PRINCIPAL_REGISTRATIONS, SHORT_REGISTRATION, AuditEvent, Case, Document,
                  PendingDecision, User, utcnow)

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
    if user.role not in LICENSED_ROLES:
        return "A registered principal on the RIA service team accepts accounts"
    if not holds_principal(user):
        return (f"Accepting an account needs a principal registration (Series 24 or Series 9/10). "
                f"Yours: {registration_text(user)}")
    if user.id in preparers(db, case):
        return "You prepared this case, so another principal accepts it"
    return None


def to_review(db: Session, case: Case) -> list[Document]:
    """The documents the principal reviews before accepting: every one operations found in good order."""
    from .documents import case_documents
    return [d for d in case_documents(db, case) if d.status == "in_good_order"]


def opened_by(db: Session, case: Case, user: User) -> set[int]:
    """The documents on this case this person has opened (app/routers/documents.py logs every internal open)."""
    rows = db.scalars(select(AuditEvent.target_id).where(AuditEvent.case_id == case.id, AuditEvent.actor_id == user.id,
                                                         AuditEvent.action == "document_opened")).all()
    return {int(x) for x in rows if str(x).isdigit()}


def acceptance_state(db: Session, case: Case, user: User) -> dict | None:
    if case.status != "PENDING_ACCEPTANCE":
        return None
    why = refusal(db, case, user)
    return {"can_accept": why is None, "reason": why,
            "signs_as": principal_label(user) if why is None else None,
            "to_review": [d.id for d in to_review(db, case)], "opened": sorted(opened_by(db, case, user))}


def accept_case(db: Session, case: Case, user: User, value: str, note: str | None = None,
                reviewed: bool = False, documents_reviewed: list[int] | None = None) -> None:
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
    if value == "accepted" and not reviewed:
        raise HTTPException(422, "Confirm you reviewed the documents, findings, and risk before accepting")
    docs = to_review(db, case) if value == "accepted" else []
    missing = [d for d in docs if d.id not in set(documents_reviewed or [])]
    if missing:
        from .documents import DOC_TYPES
        raise HTTPException(422, "Review every document in good order before accepting: "
                            + "; ".join(f"{DOC_TYPES[d.doc_type]['label']} ({d.filename})" for d in missing))
    opened = opened_by(db, case, user)
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
                 individual_crd=user.individual_crd, note=note, confirmed_review=reviewed or None,
                 outstanding=(case.result or {}).get("requested_items") or None,
                 documents_reviewed=[{"id": d.id, "type": d.doc_type, "sha256": d.sha256, "opened": d.id in opened}
                                     for d in docs] or None, **stamp(case, "acceptance"))
    run_case(db, case, user)


def send_back(db: Session, case: Case, user: User, reason: str, document_ids: list[int]) -> str:
    """The principal sends the case back to operations before accepting, naming the documents to look at again. The
    case goes to rework (onboarding's queue) and comes back to the principal when operations is done."""
    from .documents import DOC_TYPES, case_documents
    why = refusal(db, case, user)
    if why:
        if case.status == "PENDING_ACCEPTANCE":
            audit.record(db, user, "send_back_refused", "case", case.id, case_id=case.id, reason=why)
            db.commit()
            raise HTTPException(403, why)
        raise HTTPException(409, why)
    docs = [d for d in case_documents(db, case) if d.id in set(document_ids)]
    if not docs:
        raise HTTPException(422, "Pick the documents operations should look at again")
    n = 1 + db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.case_id == case.id, AuditEvent.action == "sent_back_to_operations"))
    ref = f"SB-{case.id}-{n}"
    case.extra = {**(case.extra or {}), "rework": {
        "kind": "send_back", "reason": reason, "reference": ref, "requested_by": user.display_name, "approved_by": None,
        "at": utcnow().isoformat(),
        "documents": [{"id": d.id, "label": DOC_TYPES[d.doc_type]["label"], "filename": d.filename} for d in docs]}}
    audit.record(db, user, "sent_back_to_operations", "case", case.id, case_id=case.id, reference=ref, reason=reason,
                 documents=[d.id for d in docs])
    run_case(db, case, user)
    return ref
