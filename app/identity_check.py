"""
A failed identity check (Shawn, Oct. 20, 2026). Where the custodian verifies identity itself (a firm's owners and
control persons, a Path B account's holders, a Path B change's new parties), onboarding or CIP records why a check
failed:

- insufficient information or the wrong type of document: the case goes back to the RIA to resubmit, with the reason
  in the request; new identity documents settle it, and verification starts again;
- a suspected fraudulent identity: the case goes to AML compliance, held (IDFR), and compliance confirms it (the case
  is declined and stays held; compliance decides whether a SAR is due) or clears it (verification continues).

The engine replays the open issue (onboarding/pipeline.py, _identity_issue); this module records it.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline import IDENTITY_FAILURES, IDENTITY_RESUBMIT

from . import audit
from .orm import Case, Document, User, utcnow

KINDS = {"insufficient_information": "Insufficient information: the RIA resubmits",
         "wrong_document": "Wrong type of document: the RIA resubmits",
         "suspected_fraud": "Suspected fraudulent identity: AML compliance decides"}
MIN_NOTE = 10
CLOSED_FOR_CHECKS = {"DECLINED", "BLOCKED", "REJECTED", "RETURNED", "APPLIED", "REDIRECTED", "PENDING_AI_REVIEW",
                     "PENDING_FRAUD_REVIEW", "PENDING_SANCTIONS"}


def may_record(user: User) -> bool:
    return user.role == "ops_supervisor" or (user.role == "ops_analyst" and user.team in ("onboarding", "cip"))


def unverified(case: Case) -> list[str]:
    """The people whose identity the custodian still has to verify on this case."""
    app = case.application or {}
    if case.kind == "firm":
        return [p["name"] for p in app.get("people", []) if not p.get("identity_verified")
                and (p.get("ownership_pct", 0) >= 25 or p.get("role") == "control_person")]
    if case.kind == "account":
        if (case.result or {}).get("path") != "B":
            return []                            # Path A relies on the RIA's CIP
        return [p["name"] for p in app.get("holders", []) if not p.get("identity_verified")
                and p.get("role") in ("account_holder", "control_person", "owner", "trustee")]
    if case.kind == "change" and case.status == "PENDING_VERIFICATION":
        return [p["name"] for p in app.get("new_parties", []) if not p.get("identity_verified")]
    return []


def issue_for_engine(case: Case) -> dict | None:
    """What the engine replays: an open resubmission request, or a suspected fraud (its decision settles it)."""
    issue = (case.extra or {}).get("identity_issue")
    if not issue:
        return None
    if issue["kind"] == "suspected_fraud" or issue.get("status") == "open":
        return {k: issue[k] for k in ("kind", "person", "note")}
    return None


def state(case: Case, user: User) -> dict | None:
    if user.role not in ("ops_analyst", "ops_supervisor", "aml_compliance", "sanctions", "ria_service",
                         "ria_service_lead", "periodic_review"):
        return None
    people = unverified(case)
    issue = (case.extra or {}).get("identity_issue")
    can = bool(people) and may_record(user) and case.status not in CLOSED_FOR_CHECKS
    if not people and not issue:
        return None
    return {"can_record": can, "people": people, "kinds": KINDS, "issue": issue,
            "history": (case.extra or {}).get("identity_checks", [])}


def record(db: Session, case: Case, user: User, kind: str, person: str, note: str) -> None:
    from .documents import mark
    from .runner import run_case
    if not may_record(user):
        audit.refuse(db, user, "identity_check", "Onboarding or CIP records an identity check", case_id=case.id)
    if kind not in IDENTITY_FAILURES:
        raise HTTPException(422, "Choose why the check failed")
    if case.status in CLOSED_FOR_CHECKS or person not in unverified(case):
        raise HTTPException(409, "That person isn't waiting for identity verification on this case")
    note = (note or "").strip()
    if len(note) < MIN_NOTE:
        raise HTTPException(422, "Say what you found (a sentence is enough)")
    now = utcnow().isoformat()
    issue = {"kind": kind, "person": person, "note": note, "status": "open", "by": user.display_name, "at": now}
    extra = dict(case.extra or {})
    extra["identity_issue"] = issue
    extra["identity_checks"] = [*extra.get("identity_checks", []), issue]
    case.extra = extra
    if kind == "suspected_fraud":                # a new suspicion gets a new decision
        case.decisions = {k: v for k, v in (case.decisions or {}).items()
                          if k not in ("identity_fraud", "identity_fraud_reference")}
    # For a resubmission, the identity documents waiting to be checked are the ones that failed, and the reason goes
    # to the RIA with them. A fraud suspicion touches no document: the RIA sees a document's review note, and nothing
    # may tell the RIA or the client that the custodian suspects fraud.
    if kind in IDENTITY_RESUBMIT:
        for doc in db.scalars(select(Document).where(Document.case_id == case.id,
                                                     Document.doc_type == "identity_documents",
                                                     Document.status == "received")).all():
            mark(db, doc, "not_in_good_order", user.display_name, user, f"{IDENTITY_FAILURES[kind].capitalize()}: {note}")
    audit.record(db, user, "identity_check_failed", "case", case.id, case_id=case.id, kind=kind, person=person,
                 note=note, routed_to="AML compliance" if kind == "suspected_fraud" else "the RIA, to resubmit")
    case.manual_steps += 1
    run_case(db, case, user)


def resubmitted(db: Session, case: Case, user: User, doc: Document) -> bool:
    """New identity documents settle an open resubmission request; verification then starts again."""
    issue = (case.extra or {}).get("identity_issue")
    if doc.doc_type != "identity_documents" or not issue or issue.get("status") != "open" \
            or issue["kind"] not in IDENTITY_RESUBMIT:
        return False
    extra = dict(case.extra)
    extra["identity_issue"] = {**issue, "status": "resubmitted", "document": doc.id, "resubmitted_at": utcnow().isoformat()}
    case.extra = extra
    audit.record(db, user, "identity_documents_resubmitted", "case", case.id, case_id=case.id, document=doc.id,
                 person=issue["person"])
    return True
