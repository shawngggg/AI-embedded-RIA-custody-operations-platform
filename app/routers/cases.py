"""Cases, queues, corrections, and maker-checker decisions."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ai_suggestions, audit
from ..acceptance import accept_case
from ..config import settings
from ..documents import CORRECTABLE, DOC_TYPES, field_docs, mark, supersede_others
from .. import rescission
from ..db import get_db
from ..orm import PORTAL_ROLES, AiSuggestion, Case, Document, PendingDecision, Review, User, utcnow
from .. import portal_view
from ..access import capabilities, require_capability
from ..queues import DECISIONS, QUEUES, works_queue
from ..risk_register import log_event
from ..runner import run_case
from ..security import current_user, require_internal, require_roles
from ..traceability import stamp
from ..views import (CORRECTIONS, case_detail, case_summary, decision_dict, get_case_for, review_dict,
                     sla_state)

router = APIRouter(prefix="/api", tags=["cases"])


@router.get("/queues")
def queues(user: User = Depends(require_capability("cases")), db: Session = Depends(get_db)):
    """The queues this person works (a supervisor oversees every operations queue), with counts and SLA breaches."""
    now = datetime.now(timezone.utc)
    out = []
    for key, spec in QUEUES.items():
        if not works_queue(user, key):
            continue
        cases = db.scalars(select(Case).where(Case.queue == key)).all()
        states = [sla_state(c, now) for c in cases]
        out.append({"key": key, "label": spec["label"], "owner": spec["owner"], "roles": spec["roles"],
                    "teams": spec.get("teams", []), "sla_hours": spec["sla_hours"], "count": len(cases),
                    "breached": sum(1 for s in states if s["breached"]), "mine": works_queue(user, key)})
    today = settings.today()
    reviews = [review_dict(r, today) for r in db.scalars(select(Review)).all()]
    if capabilities(user)["reviews"]:
        out.append({"key": "reviews", "label": "Reviews due", "owner": "Periodic review team",
                    "roles": ["periodic_review"], "sla_hours": None,
                    "count": sum(1 for r in reviews if r["due"] or r["overdue"] or r["status"] == "restricted"),
                    "breached": sum(1 for r in reviews if r["overdue"]), "mine": user.role == "periodic_review"})
    pending = db.scalars(select(PendingDecision).where(PendingDecision.status == "pending")).all()
    mine = sum(1 for d in pending if user.role in DECISIONS[d.decision_type]["approver"] and d.proposed_by != user.id)
    return {"queues": out, "pending_approvals": mine}


@router.get("/cases")
def list_cases(queue: str | None = None, status: str | None = None, kind: str | None = None,
               open_only: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Case).order_by(Case.updated_at.desc())
    if queue:
        q = q.where(Case.queue == queue)
    if status:
        q = q.where(Case.status == status)
    if kind:
        q = q.where(Case.kind == kind)
    if open_only:
        q = q.where(Case.closed_at.is_(None))
    if user.role in ("ria_admin", "ria_user"):
        q = q.where(Case.firm_id == user.firm_id, Case.kind.in_(["account", "change"]))
    elif user.role == "client":
        q = q.where(Case.account_id == user.client_account_id, Case.kind == "change")
    elif not capabilities(user)["cases"]:
        audit.refuse(db, user, "access", "Case files aren't part of your role", target_type="endpoint",
                     target_id="GET /api/cases")
    rows = db.scalars(q).all()
    if queue:
        rows = sorted(rows, key=lambda c: c.sla_due or c.updated_at)
    if user.role in PORTAL_ROLES:
        return [portal_view.summary(db, c) for c in rows]
    return [case_summary(db, c) for c in rows]


@router.get("/cases/{case_id}")
def get_case(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return case_detail(db, get_case_for(db, user, case_id), user)


class Corrections(BaseModel):
    fields: dict = Field(default_factory=dict)
    documents: dict[str, int] = Field(default_factory=dict)   # field -> id of the document that evidences it
    overrides: dict[str, str] = Field(default_factory=dict)   # field -> why a document that failed a check is fine
    verify_people: bool = False
    control_person: str | None = None
    note: str | None = None


OPS = ("ops_analyst", "ops_supervisor")


@router.post("/cases/{case_id}/corrections")
def submit_corrections(case_id: int, body: Corrections, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    case = get_case_for(db, user, case_id)
    if user.role not in OPS + ("ria_admin", "ria_user", "client"):
        audit.refuse(db, user, "items_received", "Operations or the requester resolves missing items", case_id=case.id)
    waiting = case.status in CORRECTABLE or (case.status == "RETURNED" and case.kind == "change")
    if not waiting or not (case.result or {}).get("findings", {}).get("decisions"):
        raise HTTPException(409, "This case isn't waiting for items")
    allowed = {f for rows in CORRECTIONS.values() for f, _l, kind in rows if kind in ("bool", "text", "bank")}
    unknown = set(body.fields) - allowed
    if unknown:
        raise HTTPException(422, f"Not a correctable field: {', '.join(sorted(unknown))}")
    fields = dict(body.fields)

    # Items backed by a document need the document, and a person in operations to check it
    claimed = [f for f, v in fields.items() if v and field_docs(case.kind, f)]
    if body.verify_people:
        claimed.append("verify_people")
    if claimed and user.role not in OPS:
        audit.refuse(db, user, "items_received", "Operations checks each document before the item is marked received. "
                     "Upload the document, and operations records it.", case_id=case.id, fields=sorted(claimed))
    evidence = []
    for f in claimed:
        doc = db.get(Document, body.documents.get(f, -1))
        types = field_docs(case.kind, f)
        label = "tax form (W-9 or W-8)" if f == "tax_form" else DOC_TYPES[types[0]]["label"]
        if doc is None or doc.case_id != case.id or doc.doc_type not in types:
            raise HTTPException(422, f"Attach the {label} to this case first, then record it")
        if doc.status == "not_in_good_order":
            raise HTTPException(422, f"{doc.filename} was marked not in good order")
        failed = [c["check"] for c in doc.checks or [] if c["result"] == "fail"]
        if failed and len((body.overrides.get(f) or "").strip()) < 10:
            raise HTTPException(422, f"{doc.filename} failed: {', '.join(failed)}. Explain why it's in good order "
                                     "anyway, or mark it not in good order and request a new one.")
        if f == "tax_form":
            fields["tax_form"] = DOC_TYPES[doc.doc_type]["tax_form"]
        evidence.append(doc)

    app = dict(case.application)
    app.update(fields)
    if body.verify_people:
        app["people"] = [{**p, "identity_verified": True} for p in app.get("people", [])]
    if body.control_person:
        app["people"] = list(app.get("people", [])) + [
            {"name": body.control_person, "role": "control_person", "ownership_pct": 0.0, "identity_verified": False}]
    case.application = app
    case.manual_steps += 1
    for doc in evidence:
        field = next(f for f in claimed if body.documents.get(f) == doc.id)
        if doc.status != "in_good_order":
            mark(db, doc, "in_good_order", user.display_name, user, body.overrides.get(field))
        supersede_others(db, doc, field_docs(case.kind, field), user.display_name, user)
    audit.record(db, user, "items_received", "case", case.id, case_id=case.id, fields=sorted(fields),
                 verify_people=body.verify_people or None, control_person=body.control_person, note=body.note,
                 documents=[{"id": d.id, "doc_type": d.doc_type, "sha256": d.sha256} for d in evidence] or None)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class SuggestionReview(BaseModel):
    action: str                                  # accept | edit | reject
    value: str | dict | None = None              # the reviewer's value for an edit; a person is {name, role, ownership_pct}
    reason: str | None = Field(default=None, max_length=500)


@router.post("/cases/{case_id}/ai-suggestions/{suggestion_id}")
def review_suggestion(case_id: int, suggestion_id: int, body: SuggestionReview,
                      user: User = Depends(require_capability("intake")), db: Session = Depends(get_db)):
    """The onboarding team checks each value the AI read against the package: accept, edit, or reject."""
    case = get_case_for(db, user, case_id)
    s = db.get(AiSuggestion, suggestion_id)
    if s is None or s.case_id != case.id:
        raise HTTPException(404, "No such suggestion on this case")
    ai_suggestions.review(db, case, s, user, body.action, body.value, body.reason)
    db.commit()
    return case_detail(db, case, user)


@router.post("/cases/{case_id}/rerun")
def rerun(case_id: int, user: User = Depends(require_roles("ops_analyst", "ops_supervisor")),
          db: Session = Depends(get_db)):
    case = get_case_for(db, user, case_id)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class Propose(BaseModel):
    decision_type: str
    value: str
    reference: str = Field(min_length=3, max_length=80)
    note: str | None = None


@router.post("/cases/{case_id}/decisions")
def propose(case_id: int, body: Propose, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    case = get_case_for(db, user, case_id)
    spec = DECISIONS.get(body.decision_type)
    if spec is None or body.value not in spec["values"]:
        raise HTTPException(422, "Unknown decision or value")
    if spec.get("direct"):
        raise HTTPException(422, "A registered principal records acceptance directly: POST /api/cases/{id}/acceptance")
    if spec.get("custom"):
        raise HTTPException(422, "Ask for a rescission with POST /api/cases/{id}/rescission")
    if user.role not in spec["proposer"]:
        audit.record(db, user, "decision_refused", "case", case.id, case_id=case.id,
                     decision_type=body.decision_type, reason="wrong role")
        db.commit()
        raise HTTPException(403, f"Only {', '.join(spec['proposer'])} can propose a {spec['label'].lower()}")
    if case.status not in spec["statuses"]:
        raise HTTPException(409, f"The case isn't waiting for a {spec['label'].lower()}")
    if db.scalar(select(PendingDecision).where(PendingDecision.case_id == case.id,
                                               PendingDecision.decision_type == body.decision_type,
                                               PendingDecision.status == "pending")):
        raise HTTPException(409, "A proposal is already waiting for approval")
    d = PendingDecision(case_id=case.id, decision_type=body.decision_type, value=body.value,
                        reference=body.reference, note=body.note, proposed_by=user.id)
    db.add(d)
    db.flush()
    case.manual_steps += 1
    audit.record(db, user, "decision_proposed", "case", case.id, case_id=case.id, decision_type=body.decision_type,
                 value=body.value, reference=body.reference, **stamp(case, body.decision_type))
    db.commit()
    return decision_dict(db, d)


@router.get("/decisions")
def pending_decisions(status: str = "pending", user: User = Depends(require_capability("approvals")),
                      db: Session = Depends(get_db)):
    rows = db.scalars(select(PendingDecision).where(PendingDecision.status == status)
                      .order_by(PendingDecision.proposed_at)).all()
    out = []
    for d in rows:
        item = decision_dict(db, d)
        item["can_approve"] = user.role in DECISIONS[d.decision_type]["approver"] and user.id != d.proposed_by
        item["case_title"] = db.get(Case, d.case_id).title
        out.append(item)
    return out


class Acceptance(BaseModel):
    value: str = Field(pattern="^(accepted|declined)$")
    note: str | None = None
    reviewed: bool = False      # the principal confirms they reviewed the documents, findings, and risk


@router.post("/cases/{case_id}/acceptance")
def accept(case_id: int, body: Acceptance, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    """A registered principal on the RIA service team accepts or declines the account (FINRA Rule 4512)."""
    case = get_case_for(db, user, case_id)
    accept_case(db, case, user, body.value, body.note, body.reviewed)
    db.commit()
    return case_detail(db, case, user)


class RescissionRequest(BaseModel):
    kind: str            # accepted_before_review | regulatory_issue
    reason: str
    return_to: str       # review | operations


@router.post("/cases/{case_id}/rescission")
def request_rescission(case_id: int, body: RescissionRequest, user: User = Depends(require_internal),
                       db: Session = Depends(get_db)):
    """A principal asks to rescind an acceptance. The account is restricted at once; the team lead decides."""
    case = get_case_for(db, user, case_id)
    rescission.request(db, case, user, body.kind, body.reason, body.return_to)
    db.commit()
    return case_detail(db, case, user)


class ReworkDone(BaseModel):
    note: str = Field(min_length=5)


@router.post("/cases/{case_id}/rework-complete")
def rework_complete(case_id: int, body: ReworkDone, user: User = Depends(require_roles("ops_analyst", "ops_supervisor")),
                    db: Session = Depends(get_db)):
    """Operations finished the rework a rescission asked for; the case runs again and goes back to the principal."""
    case = get_case_for(db, user, case_id)
    if case.status != "PENDING_REWORK":
        raise HTTPException(409, "This case isn't in rework")
    rework = dict((case.extra or {}).get("rework") or {})
    case.extra = {k: v for k, v in (case.extra or {}).items() if k != "rework"}
    case.manual_steps += 1
    audit.record(db, user, "rework_completed", "case", case.id, case_id=case.id, reference=rework.get("reference"),
                 note=body.note)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class Decide(BaseModel):
    note: str | None = None


def _decide(decision_id: int, approve: bool, body: Decide, user: User, db: Session):
    d = db.get(PendingDecision, decision_id)
    if d is None or d.status != "pending":
        raise HTTPException(404, "No pending decision with that id")
    spec = DECISIONS[d.decision_type]
    case = db.get(Case, d.case_id)
    if user.role not in spec["approver"]:
        audit.record(db, user, "decision_refused", "case", case.id, case_id=case.id,
                     decision_type=d.decision_type, reason="wrong role for approval")
        db.commit()
        raise HTTPException(403, f"Only {', '.join(spec['approver'])} can approve a {spec['label'].lower()}")
    if user.id == d.proposed_by:
        audit.record(db, user, "decision_refused", "case", case.id, case_id=case.id,
                     decision_type=d.decision_type, reason="maker cannot be checker")
        db.commit()
        raise HTTPException(403, "The person who proposed a decision can't approve it")
    d.status, d.decided_by, d.decided_at = ("approved" if approve else "rejected"), user.id, utcnow()
    audit.record(db, user, "decision_approved" if approve else "decision_rejected", "case", case.id,
                 case_id=case.id, decision_type=d.decision_type, value=d.value, reference=d.reference, note=body.note,
                 proposed_by=d.proposed_by, **stamp(case, d.decision_type))
    if d.decision_type == "sanctions" and approve and d.value == "true_match":
        proposer = db.get(User, d.proposed_by)
        log_event(db, user, category="Sanctions true match", risk_type="regulatory", source="sanctions",
                  summary=f"{case.title}: the sanctions team confirmed a true match ({d.reference}). The case is "
                          "blocked; the sanctions team decides whether a blocked-property or rejected-transaction "
                          "report to OFAC is due within 10 business days (31 CFR 501.603 and 501.604).",
                  area="sanctions", detected_by=proposer.display_name if proposer else "Sanctions team",
                  approved_by=user.display_name, case=case, dedupe_key=f"sanctions:{d.id}")
    if d.decision_type == "rescission":
        (rescission.approve if approve else rescission.reject)(db, d, case, user)
    elif approve:
        decisions = dict(case.decisions or {})
        decisions[d.decision_type] = True if d.decision_type == "compliance_signoff" else d.value
        if d.decision_type in ("sanctions", "edd"):
            decisions[f"{d.decision_type}_reference"] = d.reference
        case.decisions = decisions
        run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


@router.post("/decisions/{decision_id}/approve")
def approve(decision_id: int, body: Decide = Decide(), user: User = Depends(require_internal),
            db: Session = Depends(get_db)):
    return _decide(decision_id, True, body, user, db)


@router.post("/decisions/{decision_id}/reject")
def reject(decision_id: int, body: Decide = Decide(), user: User = Depends(require_internal),
           db: Session = Depends(get_db)):
    return _decide(decision_id, False, body, user, db)
