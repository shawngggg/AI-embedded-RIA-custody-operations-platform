"""Cases, queues, corrections, and maker-checker decisions."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ai_suggestions, audit
from .. import acceptance
from ..acceptance import accept_case
from ..config import settings
from ..documents import CORRECTABLE, DOC_TYPES, case_documents, field_docs, mark, supersede_others
from .. import rescission
from ..db import get_db
from ..orm import PORTAL_ROLES, Account, AiSuggestion, Case, Document, PendingDecision, Review, User, utcnow
from .. import portal_view
from ..access import approval_refusal, capabilities, may_propose, require_capability
from ..queues import DECISIONS, QUEUES, works_queue
from ..risk_register import log_event
from ..runner import run_case
from ..security import current_user, require_internal, require_roles
from ..traceability import stamp
from ..views import (CHOICES, CLIENT_KINDS, CORRECTIONS, PER_PERSON, RIA_KINDS, related_documents, case_detail, case_summary, decision_dict, get_case_for,
                     review_dict, sla_state)

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
    mine = sum(1 for d in pending if d.proposed_by != user.id
               and approval_refusal(user, d.decision_type, db.get(Case, d.case_id)) is None)
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
        q = q.where(Case.firm_id == user.firm_id, Case.kind.in_(RIA_KINDS))
    elif user.role == "client":
        q = q.where(Case.account_id == user.client_account_id, Case.kind.in_(CLIENT_KINDS))
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


def _checked_extras(fields: dict) -> dict[str, dict]:
    """Validate the structured items (a choice, a value per person, beneficiaries) in place. Per-person values come
    back separately, because they land on the holders rather than as a field of their own."""
    for f, options in CHOICES.items():
        if f in fields and fields[f] not in {v for v, _l in options}:
            raise HTTPException(422, f"{f.replace('_', ' ').capitalize()} must be one of: "
                                     + ", ".join(l for _v, l in options))
    per_person = {}
    for f in PER_PERSON:
        if f not in fields:
            continue
        values = fields.pop(f)
        if not isinstance(values, dict) or not values:
            raise HTTPException(422, "Give a value for each person, by name")
        if f == "holder_phones":
            if any(not str(v or "").strip() for v in values.values()):
                raise HTTPException(422, "Give a phone number for each person")
            values = {k: str(v).strip() for k, v in values.items()}
        else:
            try:
                values = {k: float(v) for k, v in values.items()}
            except (TypeError, ValueError):
                raise HTTPException(422, "Each share is a number from 0 to 100")
            if any(not 0 <= v <= 100 for v in values.values()):
                raise HTTPException(422, "Each share is a number from 0 to 100")
        per_person[f] = values
    if "beneficiaries" in fields:
        rows = fields["beneficiaries"]
        if not isinstance(rows, list) or not all(isinstance(b, dict) and str(b.get("name") or "").strip() for b in rows):
            raise HTTPException(422, "Name each beneficiary")
        try:
            fields["beneficiaries"] = [{"name": str(b["name"]).strip(), "relationship": b.get("relationship") or None,
                                        "kind": "contingent" if b.get("kind") == "contingent" else "primary",
                                        "share": float(b.get("share") or 0)} for b in rows]
        except (TypeError, ValueError):
            raise HTTPException(422, "Each share is a number from 0 to 100")
    return per_person


@router.post("/cases/{case_id}/corrections")
def submit_corrections(case_id: int, body: Corrections, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    case = get_case_for(db, user, case_id)
    if user.role not in OPS + ("ria_admin", "ria_user", "client"):
        audit.refuse(db, user, "items_received", "Operations or the requester resolves missing items", case_id=case.id)
    waiting = case.status in CORRECTABLE or (case.status == "RETURNED" and case.kind == "change")
    if not waiting or not (case.result or {}).get("findings", {}).get("decisions"):
        raise HTTPException(409, "This case isn't waiting for items")
    allowed = {f for rows in CORRECTIONS.values() for f, _l, kind in rows
               if kind in ("bool", "text", "bank", "choice", "per_person", "beneficiaries")}
    unknown = set(body.fields) - allowed
    if unknown:
        raise HTTPException(422, f"Not a correctable field: {', '.join(sorted(unknown))}")
    fields = dict(body.fields)
    per_person = _checked_extras(fields)

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
    for field, values in per_person.items():
        attr = PER_PERSON[field]
        app["holders"] = [{**h, attr: values[h["name"]]} if h.get("name") in values else h for h in app.get("holders", [])]
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
    audit.record(db, user, "items_received", "case", case.id, case_id=case.id, fields=sorted([*fields, *per_person]),
                 verify_people=body.verify_people or None, control_person=body.control_person, note=body.note,
                 documents=[{"id": d.id, "doc_type": d.doc_type, "sha256": d.sha256} for d in evidence] or None)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class IdentityCheck(BaseModel):
    kind: str                     # insufficient_information | wrong_document | suspected_fraud
    person: str
    note: str = Field(max_length=500)


@router.post("/cases/{case_id}/identity-check")
def identity_check_failed(case_id: int, body: IdentityCheck, user: User = Depends(require_capability("identity_check")),
                          db: Session = Depends(get_db)):
    """Record why an identity check failed: back to the RIA to resubmit, or to AML compliance for a suspected fraud."""
    from .. import identity_check
    case = get_case_for(db, user, case_id)
    identity_check.record(db, case, user, body.kind, body.person, body.note)
    db.commit()
    return case_detail(db, case, user)


class SuggestionReview(BaseModel):
    action: str                                  # accept | edit | reject
    value: str | dict | None = None              # the reviewer's value for an edit; a person is {name, role, ownership_pct}
    reason: str | None = Field(default=None, max_length=500)


@router.post("/cases/{case_id}/ai-suggestions/{suggestion_id}")
def review_suggestion(case_id: int, suggestion_id: int, body: SuggestionReview,
                      user: User = Depends(require_capability("ai_review")), db: Session = Depends(get_db)):
    """Onboarding and CIP check each value the AI read against the package: accept, edit, or reject."""
    case = get_case_for(db, user, case_id)
    s = db.get(AiSuggestion, suggestion_id)
    if s is None or s.case_id != case.id:
        raise HTTPException(404, "No such suggestion on this case")
    ai_suggestions.review(db, case, s, user, body.action, body.value, body.reason)
    db.commit()
    return case_detail(db, case, user)


class TakeAction(BaseModel):
    data: dict = Field(default_factory=dict)


@router.post("/cases/{case_id}/actions/{key}")
def take_action(case_id: int, key: str, body: TakeAction, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """A people-step on a release 2 case (app/actions.py): a fix, a callback, a recommendation, a consent."""
    from .. import actions
    case = get_case_for(db, user, case_id)
    message = actions.take(db, case, user, key, body.data)
    db.commit()
    return {"message": message, "case": case_detail(db, case, user)}


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


def _hold_refusal(db: Session, case: Case, value: str, note: str | None) -> str | None:
    """FINRA Rule 2165 covers a specified adult: 65 or older, or 18 or older with an impairment (custody MM-014)."""
    from custody import policy
    if not (note or "").strip() or len(note.strip()) < 15:
        return "Say what makes you believe the client is being exploited (the internal review starts from it)"
    if value == "placed_impaired":
        return None
    account = db.get(Account, case.account_id)
    today, age = settings.today(), policy.param("MM-014", "age")
    for h in (account.application or {}).get("holders", []) if account else []:
        dob = h.get("date_of_birth")
        if dob:
            born = date.fromisoformat(dob)
            if today.year - born.year - ((today.month, today.day) < (born.month, born.day)) >= age:
                return None
    return f"No account holder is {age} or older on the record; if the holder is 18 or older with an impairment, " \
           "choose that reason"


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
    if not may_propose(user, body.decision_type):
        audit.record(db, user, "decision_refused", "case", case.id, case_id=case.id,
                     decision_type=body.decision_type, reason="wrong role")
        db.commit()
        raise HTTPException(403, f"Only {', '.join(spec['proposer'])}" + (f" on the {', '.join(spec['teams'])} team"
                                                                         if spec.get("teams") else "")
                            + f" can propose a {spec['label'].lower()}")
    if case.status not in spec["statuses"]:
        raise HTTPException(409, f"The case isn't waiting for a {spec['label'].lower()}")
    if body.decision_type == "exploitation_hold":
        why = _hold_refusal(db, case, body.value, body.note)
        if why:
            raise HTTPException(422, why)
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
        case = db.get(Case, d.case_id)
        item["can_approve"] = approval_refusal(user, d.decision_type, case) is None and user.id != d.proposed_by
        item["case_title"] = case.title
        related = related_documents(db, case)
        item["documents"] = [{"id": doc.id, "type_label": DOC_TYPES[doc.doc_type]["label"], "filename": doc.filename,
                              "status": doc.status} for doc in case_documents(db, case)] + \
            [{**doc, "type_label": f"{doc['type_label']} ({related['title']})"} for doc in (related or {}).get("documents", [])]
        if not item["can_approve"] and user.role == "ops_analyst" and not may_propose(user, d.decision_type):
            continue
        out.append(item)
    return out


class Acceptance(BaseModel):
    value: str = Field(pattern="^(accepted|declined)$")
    note: str | None = None
    reviewed: bool = False      # the principal confirms they reviewed the documents, findings, and risk
    documents_reviewed: list[int] = Field(default_factory=list)   # each document in good order, ticked as reviewed


@router.post("/cases/{case_id}/acceptance")
def accept(case_id: int, body: Acceptance, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    """A registered principal on the RIA service team accepts or declines the account (FINRA Rule 4512)."""
    case = get_case_for(db, user, case_id)
    accept_case(db, case, user, body.value, body.note, body.reviewed, body.documents_reviewed)
    db.commit()
    return case_detail(db, case, user)


class SendBack(BaseModel):
    reason: str = Field(min_length=10)
    documents: list[int] = Field(default_factory=list)


@router.post("/cases/{case_id}/send-back")
def send_back(case_id: int, body: SendBack, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    """The principal sends the case back to operations before accepting, naming the documents to look at again."""
    case = get_case_for(db, user, case_id)
    acceptance.send_back(db, case, user, body.reason, body.documents)
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
    """Operations finished the rework a rescission or a principal's send-back asked for; the case runs again and
    goes back to the principal."""
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
    why = approval_refusal(user, d.decision_type, case)
    if why:
        audit.record(db, user, "decision_refused", "case", case.id, case_id=case.id,
                     decision_type=d.decision_type, reason="wrong role for approval", detail=why)
        db.commit()
        raise HTTPException(403, why)
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
    if d.decision_type == "identity_fraud" and approve and d.value == "confirmed":
        proposer = db.get(User, d.proposed_by)
        log_event(db, user, category="Fraudulent identity confirmed", risk_type="regulatory", source="identity",
                  summary=f"{case.title}: AML compliance confirmed a fraudulent identity ({d.reference}). The case is "
                          "declined and stays held (IDFR); AML compliance decides whether a SAR is due within 30 "
                          "days of the determination (31 CFR 1023.320).",
                  area="aml", detected_by=proposer.display_name if proposer else "AML compliance",
                  approved_by=user.display_name, case=case, dedupe_key=f"identity_fraud:{d.id}")
    if d.decision_type == "rescission":
        (rescission.approve if approve else rescission.reject)(db, d, case, user)
    elif approve:
        decisions = dict(case.decisions or {})
        decisions[d.decision_type] = True if d.decision_type == "compliance_signoff" else d.value
        if d.decision_type in ("exploitation_hold", "hold_review"):     # dated: the hold's clock runs from them
            decisions["hold_events"] = list(decisions.get("hold_events") or []) + [
                {"type": d.decision_type, "value": d.value, "on": settings.today().isoformat(),
                 "reference": d.reference}]
        if d.decision_type in ("sanctions", "edd", "alert_review", "identity_fraud", "release", "risk_review",
                               "product", "estate_docs", "legal_hold", "merger_classification", "negative_consent"):
            decisions[f"{d.decision_type}_reference"] = d.reference
        case.decisions = decisions
        run_case(db, case, user)
        if d.decision_type == "exploitation_hold" and case.status == "ON_HOLD":
            from ..mail import hold_notices
            hold_notices(db, case)
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
