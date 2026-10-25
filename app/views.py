"""Shapes the API returns, and who may see which case."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from restrictions import CATALOG

from . import ai_suggestions, audit, identity_check
from .acceptance import acceptance_state, holds_principal
from .documents import CORRECTABLE, DOC_TYPES, case_documents, doc_dict, field_docs, reopen_refusal
from .access import capabilities, may_propose
from .orm import OPS_TEAMS, PORTAL_ROLES, REGISTRATIONS, ROLES, iso, Account, Case, Firm, PendingDecision, Restriction, Review, User
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
    "ACC-008": [("holder_phones", "Phone number for each account holder", "per_person")],
    "ACC-009": [("ira_type", "IRA type", "choice")],
    "ACC-010": [("ira_adoption_agreement", "IRA adoption agreement received", "bool")],
    "ACC-011": [("beneficiaries", "Beneficiaries and their shares", "beneficiaries")],
    "ACC-012": [("joint_tenancy", "Joint tenancy", "choice")],
    "ACC-013": [("holder_shares", "Each holder's share (tenants in common)", "per_person")],
    "ACC-014": [("employer_consent", "Employer's written consent received (FINRA Rule 3210)", "bool")],
    "CDD-001": [("account_purpose", "Account purpose and expected activity", "text")],
    "CDD-BO": [("beneficial_ownership_certification", "Beneficial ownership certification received", "bool")],
    "CHG-002": [("client_signature", "Client-signed change form received", "bool")],
    "CHG-003": [("medallion_guarantee", "Medallion signature guarantee received", "bool")],
    "CHG-004": [("new_bank", "Bank name, ABA routing number, and last 4 of the account", "bank")],
}

# A choice field's options, and the person attribute a per-person field sets
CHOICES = {
    "ira_type": [("traditional", "Traditional IRA"), ("roth", "Roth IRA"), ("sep", "SEP IRA"),
                 ("inherited", "Inherited IRA")],
    "joint_tenancy": [("jtwros", "Joint tenants with right of survivorship"), ("tenants_in_common", "Tenants in common"),
                      ("community_property", "Community property"), ("tenants_by_entirety", "Tenants by the entirety")],
}
PER_PERSON = {"holder_phones": "phone", "holder_shares": "ownership_pct"}
NATURAL_ROLES = ("account_holder", "trustee", "authorized_signer", "control_person")


def _people_for(case: Case, field: str) -> list[dict]:
    holders = (case.application or {}).get("holders", [])
    if field == "holder_phones":
        return [{"name": h["name"], "value": h.get("phone") or ""} for h in holders
                if h.get("role", "account_holder") in NATURAL_ROLES]
    return [{"name": h["name"], "value": h.get("ownership_pct") or 0} for h in holders
            if h.get("role", "account_holder") == "account_holder"]


def user_dict(db: Session, u: User) -> dict:
    firm = db.get(Firm, u.firm_id) if u.firm_id else None
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email,
            "role": u.role, "role_label": ROLES.get(u.role, u.role), "active": u.active, "is_demo": u.is_demo,
            "note": u.demo_note, "firm": {"id": firm.id, "crd": firm.crd, "name": firm.legal_name} if firm else None,
            "client_account_id": u.client_account_id,
            "registrations": [{"code": r, "label": REGISTRATIONS.get(r, r)} for r in (u.registrations or [])],
            "individual_crd": u.individual_crd,
            "can_accept_accounts": u.role in ("ria_service", "ria_service_lead") and holds_principal(u),
            "team": u.team, "team_label": OPS_TEAMS.get(u.team) if u.team else None,
            "capabilities": capabilities(u),
            "workspace": "portal" if u.role in ("ria_admin", "ria_user", "client") else "workbench"}


# What the portal sees: an RIA its firm's requests (and an advisor move bringing a client to it); a client the requests
# on their own account. Sanctions, risk, and compliance cases stay inside even when they're about the firm's account.
RIA_KINDS = ("account", "change", "transfer", "entitlement", "offboarding", "deceased", "product", "advisor_move")
CLIENT_KINDS = ("change", "transfer", "entitlement")


def can_see(db: Session, user: User, case: Case) -> bool:
    if user.role in ("ria_admin", "ria_user"):
        if case.kind == "advisor_move" and user.firm_id:
            firm = db.get(Firm, user.firm_id)
            if (case.application or {}).get("new_firm_crd") == firm.crd:
                return True
        return case.firm_id == user.firm_id and case.kind in RIA_KINDS
    if user.role == "client":
        return case.account_id == user.client_account_id and case.kind in CLIENT_KINDS
    return True


def get_case_for(db: Session, user: User, case_id: int) -> Case:
    if user.role not in PORTAL_ROLES and not capabilities(user)["cases"]:
        audit.refuse(db, user, "access", "Case files aren't part of your role", case_id=case_id)
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "No such case")
    if not can_see(db, user, case):
        # Same answer as a missing case, so nothing leaks; logged, because probing other firms' cases is a signal,
        # and raised once a day per person in the access and authentication risk register
        from .config import settings
        from .risk_register import log_access_event
        who = f"{user.display_name} ({ROLES.get(user.role, user.role)}" + (f", {db.get(Firm, user.firm_id).legal_name}"
                                                                          if user.firm_id else "") + ")"
        log_access_event(db, user, category="Out-of-scope access attempt", target_id=user.username,
                         summary=f"{who} tried to open case #{case.id}, which is outside their firm or account. "
                                 "The request was refused with the same answer as a missing case.",
                         dedupe_key=f"access:scope:{user.id}:{settings.today().isoformat()}")
        audit.refuse(db, user, "access", "No such case", status=404, case_id=case.id, scope="outside the user's firm or account")
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


def related_documents(db: Session, case: Case) -> dict | None:
    """Documents on another case that a decision here rests on: an SLOA wire's release rests on the client's signed
    SLOA, which lives on the account change that set it up."""
    sloa_id = (case.application or {}).get("sloa_id") if case.kind == "transfer" else None
    if not sloa_id or not sloa_id.startswith("SLOA-") or not sloa_id[5:].isdigit():
        return None
    source = db.get(Case, int(sloa_id[5:]))
    if source is None or source.account_id != case.account_id:
        return None
    return {"title": f"The client's SLOA ({sloa_id}, case #{source.id})", "case_id": source.id,
            "documents": [doc_dict(d) for d in case_documents(db, source)]}


def corrections_for(db: Session, case: Case) -> list[dict]:
    waiting = case.status in CORRECTABLE or (case.status == "RETURNED" and case.kind == "change")
    if not waiting or not case.result:
        return []
    findings = (case.result.get("findings") or {}).get("decisions", [])
    docs = case_documents(db, case)
    out, seen = [], set()
    for f in findings:
        for field, label, kind in CORRECTIONS.get(f["rule_id"], []):
            if field in seen:
                continue
            seen.add(field)
            types = field_docs(case.kind, field)
            out.append({"field": field, "label": label, "type": "document" if types else kind,
                        "rule_id": f["rule_id"], "reason": f["reason"], "current": case.application.get(field),
                        "options": [{"value": v, "label": l} for v, l in CHOICES.get(field, [])],
                        "people": _people_for(case, field) if field in PER_PERSON else [],
                        "doc_types": [{"value": t, "label": DOC_TYPES[t]["label"]} for t in types],
                        "documents": [doc_dict(d) for d in docs
                                      if d.doc_type in types and d.status in ("received", "in_good_order")]})
    return out


def available_decisions(case: Case, user: User) -> list[dict]:
    out = []
    for key, spec in DECISIONS.items():
        if case.status in spec["statuses"] and may_propose(user, key) and not spec.get("direct") \
                and not spec.get("custom"):
            out.append({"decision_type": key, "label": spec["label"],
                        "values": [{"value": v, "label": l} for v, l in spec["values"].items()],
                        "approver_roles": spec["approver"]})
    return out


def rescission_state(db: Session, c: Case, user: User) -> dict | None:
    from . import rescission
    if c.kind not in ("firm", "account") or user.role in ("ria_admin", "ria_user", "client"):
        return None
    if c.status not in rescission.OPENED and not rescission.pending(db, c):
        return None
    why = rescission.refusal(db, c, user)
    return {"can_request": why is None, "reason": why, "kinds": rescission.KINDS, "return_to": rescission.RETURN_TO}


def case_detail(db: Session, c: Case, user: User) -> dict:
    if user.role in PORTAL_ROLES:
        from . import portal_view
        return portal_view.detail(db, c, user)
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
        "corrections": corrections_for(db, c) if user.role in ("ops_analyst", "ops_supervisor", "ria_admin", "ria_user",
                                                           "client") else [],
        "account": {"id": account.id, "status": account.status, "group": account.group,
                    "display_name": account.display_name} if account else None,
        "manual_steps": c.manual_steps, "nigo_count": c.nigo_count, "closed_at": iso(c.closed_at),
        "acceptance": None if user.role in ("ria_admin", "ria_user", "client") else acceptance_state(db, c, user),
        "documents": [{**doc_dict(d), "reopen_refusal": reopen_refusal(c, d, user)} for d in case_documents(db, c)],
        "related_documents": related_documents(db, c) if user.role not in PORTAL_ROLES else None,
        "rescission": rescission_state(db, c, user),
        "rework": (c.extra or {}).get("rework"),
        "ai_suggestions": ai_suggestions.for_case(db, c, user) if c.kind == "firm" else [],
        "identity_check": identity_check.state(c, user),
    })
    data.update(r2_detail(db, c, user))
    return data


def r2_detail(db: Session, c: Case, user: User) -> dict:
    """What a release 2 case adds to the case page: its map, summary, people-steps open to this person, ledger
    entries, and the cases it opened or belongs to."""
    from . import actions, ledger, servicing
    if c.kind not in servicing.R2_KINDS:
        return {"map": None, "summary": {}, "actions": [], "ledger_entries": [], "linked": []}
    linked = []
    for key, cid in ((c.extra or {}).get("children") or {}).items():
        ch = db.get(Case, cid)
        if ch:
            linked.append({"id": ch.id, "title": ch.title, "status": ch.status, "relation": "opened", "key": key})
    for pid in (c.extra or {}).get("parents", []):
        p = db.get(Case, pid)
        if p:
            linked.append({"id": p.id, "title": p.title, "status": p.status, "relation": "part of", "key": None})
    return {"map": servicing.MAP[c.kind], "summary": (c.result or {}).get("summary") or {},
            "actions": actions.available(db, c, user), "ledger_entries": ledger.entries(db, case_id=c.id),
            "linked": linked, "expected": (c.result or {}).get("expected")}


def review_dict(r: Review, today) -> dict:
    due = r.status == "scheduled" and (r.next_review - today).days <= 30
    overdue = r.status == "requested" and r.response_due is not None and today > r.response_due
    return {"id": r.id, "subject_id": r.subject_id, "subject_name": r.subject_name, "risk_tier": r.risk_tier,
            "next_review": r.next_review.isoformat(), "status": r.status,
            "requested_on": r.requested_on.isoformat() if r.requested_on else None,
            "response_due": r.response_due.isoformat() if r.response_due else None,
            "trigger": r.trigger, "due": due, "overdue": overdue, "history": r.history or []}
