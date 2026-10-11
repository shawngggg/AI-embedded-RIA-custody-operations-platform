"""Shapes the API returns, and who may see which case."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from restrictions import CATALOG

from .orm import ROLES, Account, Case, Firm, PendingDecision, Restriction, Review, User
from .queues import DECISIONS, QUEUES

# Fields a person can supply to resolve each finding: rule id -> [(field, label, type)]
CORRECTIONS = {
    "REG-003": [("sec_file_number", "SEC file number (801-)", "text")],
    "REG-004": [("crd_number", "CRD number", "text")],
    "KYB-001": [("formation_documents", "Formation documents received", "bool")],
    "KYB-002": [("ownership_chart", "Ownership chart received", "bool")],
    "KYB-003": [("beneficial_ownership_certification", "Beneficial ownership certification received", "bool")],
    "KYB-004": [("control_person", "Control person (name)", "control_person")],
    "KYB-005": [("verify_people", "Identity documents received and verified for owners and control person",
                 "verify_people")],
    "KYB-006": [("custodial_agreement_signed", "Custodial services agreement signed", "bool")],
    "CIP-F01": [("ein", "EIN", "text")],
    "CIP-F02": [("principal_address", "Principal place of business (street address)", "text")],
    "CIP-F03": [("principal_address", "Principal place of business (street address)", "text")],
    "ACC-001": [("client_signature", "Client-signed application received", "bool")],
    "ACC-002": [("lpoa_signed", "Client-signed LPOA received", "bool")],
    "ACC-003": [("fee_authorization", "Client-signed fee authorization received", "bool")],
    "ACC-004": [("tax_form", "Tax form received (W-9, W-8BEN, or W-8BEN-E)", "text")],
    "ACC-006": [("entity_formation_documents", "Formation documents or trust instrument received", "bool")],
    "ACC-007": [("entity_ein", "Entity EIN", "text")],
    "CDD-001": [("account_purpose", "Account purpose and expected activity", "text")],
    "CDD-BO": [("beneficial_ownership_certification", "Beneficial ownership certification received", "bool")],
    "CHG-002": [("client_signature", "Client-signed change form received", "bool")],
    "CHG-003": [("medallion_guarantee", "Medallion signature guarantee received", "bool")],
    "CHG-004": [("new_bank", "Bank name, ABA routing number, and last 4 of the account", "bank")],
}


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def user_dict(db: Session, u: User) -> dict:
    firm = db.get(Firm, u.firm_id) if u.firm_id else None
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email,
            "role": u.role, "role_label": ROLES.get(u.role, u.role), "active": u.active, "is_demo": u.is_demo,
            "note": u.demo_note, "firm": {"id": firm.id, "crd": firm.crd, "name": firm.legal_name} if firm else None,
            "client_account_id": u.client_account_id,
            "workspace": "portal" if u.role in ("ria_admin", "ria_user", "client") else "workbench"}


def can_see(db: Session, user: User, case: Case) -> bool:
    if user.role in ("ria_admin", "ria_user"):
        return case.firm_id == user.firm_id and case.kind in ("account", "change")
    if user.role == "client":
        return case.account_id == user.client_account_id and case.kind == "change"
    return True


def get_case_for(db: Session, user: User, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None or not can_see(db, user, case):
        raise HTTPException(404, "No such case")
    return case


def sla_state(case: Case, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not case.sla_due:
        return {"due": None, "breached": False, "hours_left": None}
    due = case.sla_due if case.sla_due.tzinfo else case.sla_due.replace(tzinfo=timezone.utc)
    left = (due - now).total_seconds() / 3600
    return {"due": due.isoformat(), "breached": left < 0, "hours_left": round(left, 1)}


def case_summary(db: Session, c: Case) -> dict:
    firm = db.get(Firm, c.firm_id) if c.firm_id else None
    return {"id": c.id, "kind": c.kind, "title": c.title, "status": c.status, "queue": c.queue,
            "queue_label": QUEUES[c.queue]["label"] if c.queue else None,
            "firm": firm.legal_name if firm else None, "account_id": c.account_id, "target_id": c.target_id,
            "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
            "queue_entered_at": iso(c.queue_entered_at), "sla": sla_state(c), "path": (c.result or {}).get("path")}


def restriction_dict(r: Restriction) -> dict:
    return {"id": r.id, "target_id": r.target_id, "code": r.code, "description": CATALOG[r.code].description,
            "owner": r.owner, "reason": r.reason, "source": r.source, "case_id": r.case_id,
            "placed_by": r.placed_by, "placed_at": iso(r.placed_at), "active": r.active,
            "removed_by": r.removed_by, "removed_at": iso(r.removed_at), "removal_reason": r.removal_reason,
            "authorization": r.authorization,
            "blocks": sorted(a.value for a in CATALOG[r.code].blocks),
            "requires_authorization": CATALOG[r.code].requires_authorization}


def decision_dict(db: Session, d: PendingDecision) -> dict:
    proposer = db.get(User, d.proposed_by)
    decider = db.get(User, d.decided_by) if d.decided_by else None
    spec = DECISIONS[d.decision_type]
    return {"id": d.id, "case_id": d.case_id, "decision_type": d.decision_type, "label": spec["label"],
            "value": d.value, "value_label": spec["values"].get(d.value, d.value), "reference": d.reference,
            "note": d.note, "status": d.status, "proposed_by": proposer.display_name if proposer else None,
            "proposed_by_id": d.proposed_by, "proposed_at": iso(d.proposed_at),
            "decided_by": decider.display_name if decider else None, "decided_at": iso(d.decided_at),
            "approver_roles": spec["approver"]}


def corrections_for(case: Case) -> list[dict]:
    if case.status not in ("PENDING_ITEMS", "RETURNED") or not case.result:
        return []
    findings = (case.result.get("findings") or {}).get("decisions", [])
    out, seen = [], set()
    for f in findings:
        for field, label, kind in CORRECTIONS.get(f["rule_id"], []):
            if field in seen:
                continue
            seen.add(field)
            out.append({"field": field, "label": label, "type": kind, "rule_id": f["rule_id"],
                        "reason": f["reason"], "current": case.application.get(field)})
    return out


def available_decisions(case: Case, user: User) -> list[dict]:
    out = []
    for key, spec in DECISIONS.items():
        if case.status in spec["statuses"] and user.role in spec["proposer"]:
            out.append({"decision_type": key, "label": spec["label"],
                        "values": [{"value": v, "label": l} for v, l in spec["values"].items()],
                        "approver_roles": spec["approver"]})
    return out


def case_detail(db: Session, c: Case, user: User) -> dict:
    data = case_summary(db, c)
    restrictions = db.scalars(select(Restriction).where(Restriction.target_id == c.target_id)
                              .order_by(Restriction.placed_at.desc())).all()
    pending = db.scalars(select(PendingDecision).where(PendingDecision.case_id == c.id)
                         .order_by(PendingDecision.proposed_at.desc())).all()
    account = db.get(Account, c.account_id) if c.account_id else None
    data.update({
        "application": c.application, "decisions": c.decisions, "result": c.result, "extra": c.extra,
        "restrictions": [restriction_dict(r) for r in restrictions],
        "decision_log": [decision_dict(db, d) for d in pending],
        "available_decisions": [] if user.role in ("ria_admin", "ria_user", "client") else available_decisions(c, user),
        "corrections": corrections_for(c) if user.role in ("ops_analyst", "ops_supervisor", "ria_admin", "ria_user",
                                                           "client") else [],
        "account": {"id": account.id, "status": account.status, "group": account.group,
                    "display_name": account.display_name} if account else None,
        "manual_steps": c.manual_steps, "nigo_count": c.nigo_count, "closed_at": iso(c.closed_at),
    })
    return data


def review_dict(r: Review, today) -> dict:
    due = r.status == "scheduled" and (r.next_review - today).days <= 30
    overdue = r.status == "requested" and r.response_due is not None and today > r.response_due
    return {"id": r.id, "subject_id": r.subject_id, "subject_name": r.subject_name, "risk_tier": r.risk_tier,
            "next_review": r.next_review.isoformat(), "status": r.status,
            "requested_on": r.requested_on.isoformat() if r.requested_on else None,
            "response_due": r.response_due.isoformat() if r.response_due else None,
            "trigger": r.trigger, "due": due, "overdue": overdue, "history": r.history or []}
