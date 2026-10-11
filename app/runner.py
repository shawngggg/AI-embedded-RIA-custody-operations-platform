"""
Runs a case through the onboarding engine and records what happened.

Every run replays the case from its stored application and the decisions
people have approved, with a fresh restriction registry. The result then
updates the case (status, queue, SLA clock), the restriction codes the case
owns, the firm or account record, the rolling-review schedule, and the
audit log.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

import models as engine_models
from account_changes import ChangeType, process_change
from firm_rules import to_ria_firm
from pipeline import Decisions, OnboardingResult, onboard_firm, open_account
from restrictions import CATALOG
from risk import REVIEW_MONTHS, add_months
from sample_data import FLAGGED_LIST, IAPD_LOOKUP

from . import audit
from .config import settings
from .orm import Account, Case, CaseTransition, Firm, Restriction, Review, User, utcnow
from .queues import QUEUES, is_closed, queue_for
from .serialize import account_from, change_from, firm_from


class StoredExtraction:
    """The parts of a saved extraction result the firm pipeline reports."""
    def __init__(self, d: dict):
        self.accepted = d.get("accepted", [])
        self.rejected = d.get("rejected", [])
        self.review_queue = d.get("review_queue", [])
        self._d = d

    def to_dict(self) -> dict:
        return dict(self._d)


def decisions_of(case: Case) -> Decisions:
    d = case.decisions or {}
    return Decisions(compliance_signoff=bool(d.get("compliance_signoff")),
                     sanctions=d.get("sanctions"), sanctions_reference=d.get("sanctions_reference", "DET-UNSET"),
                     edd=d.get("edd"), edd_reference=d.get("edd_reference", "EDD-UNSET"))


def simulated_iapd(app) -> dict:
    """Simulated public registration lookup: known CRDs, and any registered adviser with a CRD."""
    lookup = dict(IAPD_LOOKUP)
    if app.crd_number and app.registration in ("SEC", "state") and app.crd_number not in lookup:
        lookup[app.crd_number] = app.iapd_status or "approved"
    return lookup


def existing_accounts(db: Session, account_row: Account | None, client_key: str) -> list:
    rows = db.scalars(select(Account).where(Account.client_key == client_key, Account.status == "OPEN")).all()
    out = []
    for a in rows:
        if account_row is not None and a.id == account_row.id:
            continue
        firm = db.get(Firm, a.firm_id)
        out.append(engine_models.Account(a.id, to_ria_firm(firm_from(firm.application), firm.status == "ACTIVE"),
                                         engine_models.AccountGroup(a.group), True))
    return out


def run_case(db: Session, case: Case, actor: User | None) -> OnboardingResult:
    as_of = settings.today()
    decisions = decisions_of(case)
    if case.kind == "firm":
        app = firm_from(case.application)
        extraction = StoredExtraction(case.extra["extraction"]) if case.extra.get("extraction") else None
        res = onboard_firm(app, as_of, flagged=FLAGGED_LIST, decisions=decisions,
                           registration_lookup=simulated_iapd(app), extraction=extraction)
    elif case.kind == "account":
        firm = db.get(Firm, case.firm_id)
        account_row = db.get(Account, case.account_id)
        app = account_from(case.application)
        res = open_account(app, firm_from(firm.application), as_of,
                           existing=existing_accounts(db, account_row, account_row.client_key),
                           ria_active=firm.status == "ACTIVE", ria_risk_tier=firm.risk_tier or "LOW",
                           flagged=FLAGGED_LIST, decisions=decisions)
    elif case.kind == "change":
        data = dict(case.application)
        if case.decisions.get("verification") == "verified":
            data["new_parties"] = [{**p, "identity_verified": True} for p in data.get("new_parties", [])]
        res = process_change(change_from(data), as_of, flagged=FLAGGED_LIST, decisions=decisions,
                             confirmation=case.decisions.get("confirmation"))
    else:
        raise ValueError(f"Unknown case kind {case.kind}")
    sync(db, case, res, actor)
    return res


def _sync_restrictions(db: Session, case: Case, res: OnboardingResult, actor: User | None) -> None:
    result = res.to_dict()
    wanted = {r["code"]: r for r in result["restrictions"]}
    removed_events = {e["code"]: e for e in result["restriction_events"] if e["event"] == "removed"}
    current = db.scalars(select(Restriction).where(Restriction.case_id == case.id, Restriction.active.is_(True))).all()
    have = {r.code: r for r in current}
    for code, row in have.items():
        if code not in wanted:
            ev = removed_events.get(code, {})
            row.active, row.removed_at = False, utcnow()
            row.removed_by = actor.display_name if actor else "system"
            row.removal_reason = ev.get("reason", "No longer applies after the case was re-run")
            row.authorization = ev.get("authorization")
            audit.record(db, actor, "restriction_removed", "restriction", row.id, case_id=case.id, code=code,
                         target=row.target_id, reason=row.removal_reason, authorization=row.authorization)
    for code, r in wanted.items():
        if code in have:
            continue
        row = Restriction(target_id=case.target_id, code=code, reason=r["reason"], owner=CATALOG[code].owner.value,
                          source="case", case_id=case.id, placed_by=actor.display_name if actor else "system")
        db.add(row)
        db.flush()
        audit.record(db, actor, "restriction_placed", "restriction", row.id, case_id=case.id, code=code,
                     target=case.target_id, owner=row.owner, reason=row.reason)


def _schedule_review(db: Session, subject_id: str, name: str, tier: str) -> None:
    review = db.scalar(select(Review).where(Review.subject_id == subject_id))
    next_date = add_months(settings.today(), REVIEW_MONTHS[tier])
    if review is None:
        db.add(Review(subject_id=subject_id, subject_name=name, risk_tier=tier, next_review=next_date,
                      history=[{"on": settings.today().isoformat(), "event": "scheduled at opening"}]))
    else:
        review.risk_tier, review.next_review = tier, next_date


def _apply_change(db: Session, case: Case) -> None:
    account = db.get(Account, case.account_id)
    if account is None:
        return
    app = dict(account.application)
    data = case.application
    kind = ChangeType(data["change_type"])
    details = data.get("details", {})
    holders = [dict(h) for h in app.get("holders", [])]
    if kind is ChangeType.ADDRESS and holders and details.get("new_address"):
        holders[0]["address"] = details["new_address"]
    if kind is ChangeType.BENEFICIAL_OWNER:
        holders += [{**p, "identity_verified": p.get("identity_verified", False)} for p in data.get("new_parties", [])]
    if kind is ChangeType.BANK_INSTRUCTION and data.get("new_bank"):
        app.setdefault("bank_instructions", []).append({**data["new_bank"], "status": "inactive_until_verified"})
    if kind is ChangeType.BENEFICIARY:
        app["beneficiaries"] = details.get("beneficiaries") or details.get("beneficiary")
    if kind in (ChangeType.CONTACT_INFO, ChangeType.TRUSTED_CONTACT, ChangeType.REGISTRATION,
                ChangeType.POWER_OF_ATTORNEY, ChangeType.STANDING_LOA):
        app.setdefault("maintenance", {}).update(details)
    app["holders"] = holders
    account.application = app
    account.change_history = list(account.change_history or []) + [[kind.value, settings.today().isoformat()]]
    if kind in (ChangeType.BENEFICIAL_OWNER, ChangeType.REGISTRATION):
        review = db.scalar(select(Review).where(Review.subject_id == account.id))
        if review is not None:
            review.next_review, review.trigger, review.status = settings.today(), "ownership_change", "scheduled"
            review.history = list(review.history or []) + [
                {"on": settings.today().isoformat(), "event": "early review: ownership change"}]


def sync(db: Session, case: Case, res: OnboardingResult, actor: User | None) -> None:
    now = utcnow()
    previous = case.status
    case.status = res.status
    case.result = res.to_dict()
    case.updated_at = now
    queue = queue_for(case.kind, res.status)
    if queue != case.queue:
        case.queue = queue
        case.queue_entered_at = now if queue else None
        case.sla_due = now + timedelta(hours=QUEUES[queue]["sla_hours"]) if queue else None
    if previous != res.status:
        db.add(CaseTransition(case_id=case.id, from_status=previous, to_status=res.status, at=now))
        if queue == "items_requested":
            case.nigo_count += 1
    if is_closed(case.kind, res.status):
        case.closed_at = case.closed_at or now
    audit.record(db, actor, "case_run", "case", case.id, case_id=case.id, status=res.status, previous=previous,
                 rule_ids=res.findings.rule_ids if res.findings else [], path=res.path,
                 policy_version=case.result["policy_version"])
    _sync_restrictions(db, case, res, actor)

    if case.kind == "firm":
        firm = db.get(Firm, case.firm_id)
        if res.status == "ACTIVE" and firm.status != "ACTIVE":
            firm.status, firm.activated_at = "ACTIVE", now
            firm.reliance_eligible = bool(res.reliance and res.reliance.eligible)
            firm.risk_tier = res.risk.tier if res.risk else "LOW"
            firm.application = case.application
            _schedule_review(db, case.target_id, firm.legal_name, firm.risk_tier)
            audit.record(db, actor, "firm_activated", "firm", firm.crd, case_id=case.id,
                         reliance_eligible=firm.reliance_eligible, risk_tier=firm.risk_tier)
        elif res.status == "DECLINED":
            firm.status = "DECLINED"
    elif case.kind == "account":
        account = db.get(Account, case.account_id)
        if res.status == "OPEN" and account.status != "OPEN":
            account.status, account.opened_at = "OPEN", now
            account.path, account.risk_tier = res.path, res.risk.tier
            _schedule_review(db, account.id, account.display_name, account.risk_tier)
            audit.record(db, actor, "account_opened", "account", account.id, case_id=case.id, path=res.path,
                         risk_tier=res.risk.tier)
        elif res.status in ("DECLINED", "BLOCKED", "RETURNED"):
            account.status = res.status
    elif case.kind == "change" and res.status == "APPLIED" and previous != "APPLIED":
        _apply_change(db, case)
        audit.record(db, actor, "change_applied", "account", case.account_id, case_id=case.id,
                     change_type=case.application.get("change_type"))
