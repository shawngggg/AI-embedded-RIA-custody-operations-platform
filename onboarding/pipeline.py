"""
End-to-end onboarding along the process maps: RIA firm onboarding (01) and
client account opening (02).

Each run returns a step-by-step trace in the maps' lanes, the resulting
status, every rule finding with its citation, the restriction codes placed,
and an audit record. A run stops where the map waits: for missing items, a
compliance review, a sanctions determination, or an EDD decision. The
decisions people make are inputs (Decisions), so every branch can be replayed.

What runs where:
  - Milestone 1 (Shawn's models.py): the account-opening gate (can_open) and
    the path decision (determine_path)
  - Milestone 2: firm rules, account rules, reliance
  - Milestone 3: restriction codes on the firm or account
  - Milestone 4: first-line flagged-list check and sanctions escalation
  - Milestone 5: risk rating and EDD
  - Milestone 6: transferability review of funding assets
  - Milestone 7: AI extraction of the firm's application package
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from account_rules import evaluate_account
from applications import AccountApplication, FirmApplication
from extraction import ExtractionResult, extract_firm_application, gate
from firm_rules import evaluate_firm, firm_reliance, reliance_relief_in_force, to_ria_firm
from flagged_list import Kind, Subject, first_line_check, record_determination
from models import Account, AccountGroup, Path, can_open, determine_path
from restrictions import Function, RestrictionRegistry
from risk import rate_account, rate_firm
from rules import POLICY_VERSION, Disposition
from transferability import SECURITY_MASTER, review_transfer

OPS = "Custody operations"
AI = "AI assist services"
RULES = "Rules and controls engine"
AML = "AML compliance"
SANCTIONS = "Sanctions team"
LEDGER = "Ledger and account services"

ROUTE_LABELS = {"acats_in_kind": "ACATS in kind", "loi": "LOI", "liquidate": "Liquidate",
                "product_acceptance": "Product acceptance", "review": "Review"}
REGISTRATION_LABELS = {"SEC": "SEC-registered", "state": "State-registered", "none": "Not registered"}
TYPE_LABELS = {"individual": "individual", "joint": "joint", "trust": "trust", "llc": "LLC",
               "corporation": "corporation", "partnership": "partnership", "ira": "IRA"}


def _stamp(as_of: date) -> datetime:
    """Timestamps follow the evaluation date, so a replayed run gives the same record."""
    return datetime(as_of.year, as_of.month, as_of.day, 12, 0, tzinfo=timezone.utc)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


@dataclass
class Decisions:
    """What people decide when the map waits for them."""
    compliance_signoff: bool = False          # registration findings reviewed by compliance
    sanctions: str | None = None              # false_positive | true_match
    sanctions_reference: str = "DET-DEMO-0001"
    edd: str | None = None                    # approved | declined
    edd_reference: str = "EDD-DEMO-0001"


@dataclass
class Step:
    lane: str
    name: str
    outcome: str
    detail: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"lane": self.lane, "step": self.name, "outcome": self.outcome,
                "detail": list(self.detail), "rule_ids": list(self.rule_ids)}


@dataclass
class OnboardingResult:
    kind: str                                  # firm | account
    subject: str
    target_id: str
    as_of: date
    status: str = "IN_PROGRESS"
    steps: list[Step] = field(default_factory=list)
    findings: object = None                    # rules.Evaluation
    risk: object = None                        # risk.RiskResult
    reliance: object = None                    # firm_rules.RelianceResult
    path: str | None = None
    screening: list[dict] = field(default_factory=list)
    escalation: dict | None = None
    transfer: object = None                    # transferability.TransferReview
    extraction: object = None                  # extraction.ExtractionResult
    requested_items: list[str] = field(default_factory=list)
    registry: RestrictionRegistry | None = None
    record: object = None                      # models.RIAFirm or models.Account once opened

    def add(self, lane, name, outcome, detail=None, rule_ids=None) -> None:
        self.steps.append(Step(lane, name, outcome, list(detail or []), list(rule_ids or [])))

    @property
    def restrictions(self) -> list[dict]:
        return [r.to_dict() for r in self.registry.active(self.target_id)] if self.registry else []

    def to_dict(self) -> dict:
        return {
            "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "evaluation_date": self.as_of.isoformat(),
            "policy_version": POLICY_VERSION,
            "kind": self.kind, "subject": self.subject, "target_id": self.target_id,
            "status": self.status, "path": self.path,
            "steps": [s.to_dict() for s in self.steps],
            "findings": self.findings.to_dict() if self.findings else None,
            "risk": self.risk.to_dict() if self.risk else None,
            "reliance": self.reliance.to_dict() if self.reliance else None,
            "screening": list(self.screening), "escalation": self.escalation,
            "requested_items": list(self.requested_items),
            "restrictions": self.restrictions,
            "restriction_events": self.registry.history(self.target_id) if self.registry else [],
            "transfer": self.transfer.to_dict() if self.transfer else None,
            "extraction": self.extraction.to_dict() if self.extraction else None,
        }


def write_audit_log(result: OnboardingResult, path: str = "audit_log.jsonl") -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result.to_dict(), default=str) + "\n")


# ---------------------------------------------------------------------------
# Shared steps
# ---------------------------------------------------------------------------

def _hit_line(h) -> str:
    if h.match_type == "jurisdiction":
        return f"{h.subject.name} ({h.subject.role}): sanctioned jurisdiction {h.matched}"
    if h.match_type == "ownership_50":
        return f"{h.subject.name}: flagged owners hold {h.evidence['flagged_ownership_pct']:g}% (50% rule)"
    dob = h.evidence.get("date_of_birth")
    return (f"{h.subject.name} ({h.subject.role}) vs {h.matched} [{h.entry_id}]: {h.match_type} match, "
            f"score {h.score:.2f}, date of birth {dob}")


def _screen_and_escalate(res: OnboardingResult, subjects, flagged, registry, decisions, by, as_of,
                         entity_id=None) -> str | None:
    """Flagged-list check and sanctions escalation. Returns a stop status, or None to continue."""
    at = _stamp(as_of)
    screening = first_line_check(res.target_id, subjects, flagged, registry, by, as_of, at, entity_id)
    res.screening = [h.to_dict() for h in screening.hits]
    if not screening.flagged:
        res.add(RULES, "First-line check against the sanctions flagged list", "No match",
                [_count(len(subjects), "party or product", "parties and products") + " checked"])
        return None
    esc = screening.escalation
    res.add(RULES, "First-line check against the sanctions flagged list", "Possible match; sanctions hold placed",
            [_hit_line(h) for h in screening.hits])
    res.add(OPS, "Sanctions escalation (shared subprocess)", "Escalated to the sanctions team",
            [f"{esc.escalation_id}; follow up by {esc.due_at.isoformat()}",
             "Only the sanctions team can release the hold"])
    res.escalation = esc.package()
    if decisions.sanctions is None:
        return "PENDING_SANCTIONS"
    record_determination(esc, registry, decisions.sanctions, "sanctions.team", Function.SANCTIONS,
                         decisions.sanctions_reference, at)
    res.escalation = esc.package()
    if decisions.sanctions == "true_match":
        res.add(SANCTIONS, "Determination", "True match: blocked; the sanctions team reports to OFAC",
                [f"Authorization {decisions.sanctions_reference}", "Hold replaced by blocked-property code OFAC"])
        return "BLOCKED"
    res.add(SANCTIONS, "Determination", "False positive: hold released",
            [f"Authorization {decisions.sanctions_reference}"])
    return None


def _edd(res: OnboardingResult, risk, registry, decisions, by, step_name) -> str | None:
    if not risk.edd_required:
        return None
    at = _stamp(res.as_of)
    hold = registry.place(res.target_id, "EDDP", f"{risk.tier} risk; EDD pending", by, Function.OPERATIONS, at)
    res.add(AML, step_name, "Required; EDD hold placed", risk.edd_requirements)
    if decisions.edd is None:
        return "PENDING_EDD"
    if decisions.edd == "declined":
        res.add(AML, "EDD decision", "Declined", [f"Reference {decisions.edd_reference}"])
        return "DECLINED"
    registry.remove(hold.restriction_id, "aml.officer", Function.AML_COMPLIANCE, "EDD approved",
                    decisions.edd_reference, at)
    res.add(AML, "EDD decision", "Approved; EDD hold removed by AML compliance",
            [f"Reference {decisions.edd_reference}"])
    return None


def _pep_media_step(res: OnboardingResult, people) -> None:
    hits = [f"{p.name}: {'foreign' if p.pep_type == 'foreign' else 'domestic'} PEP" for p in people if p.is_pep]
    hits += [f"{p.name}: adverse media" for p in people if p.adverse_media]
    res.add(AI, "PEP and adverse media screening; summarize hits (AI)",
            "Hits found; they feed the risk rating" if hits else "No hits", hits)


# ---------------------------------------------------------------------------
# RIA firm onboarding (map 01)
# ---------------------------------------------------------------------------

def onboard_firm(app: FirmApplication, as_of: date, *, flagged=None, registry=None, decisions=None,
                 registration_lookup: dict | None = None, by: str = "ops.analyst",
                 extraction: ExtractionResult | None = None) -> OnboardingResult:
    decisions = decisions or Decisions()
    registry = registry or RestrictionRegistry()
    target = f"FIRM-{app.crd_number or app.legal_name}"
    res = OnboardingResult("firm", app.legal_name, target, as_of, registry=registry, extraction=extraction)
    res.add(OPS, "RIA application received", "Received", [app.legal_name])
    if extraction is not None:
        res.add(AI, "Extract firm documents (AI)",
                f"{len(extraction.accepted)} facts accepted; {len(extraction.rejected)} rejected by the controls gate",
                extraction.review_queue)
    if registration_lookup and app.crd_number in registration_lookup:
        app = replace(app, iapd_status=registration_lookup[app.crd_number])

    ev = evaluate_firm(app, as_of)
    res.findings = ev
    nigo = [d for d in ev.decisions if d.required_documents and d.owner == "operations"]
    if nigo:
        res.add(AI, "Check completeness; categorize NIGO (AI)", "Not in good order",
                [d.reason for d in nigo], [d.rule_id for d in nigo])
        res.requested_items = [doc for d in nigo for doc in d.required_documents]
        res.add(OPS, "Request missing items from the RIA", "Sent", res.requested_items)
        res.status = "PENDING_ITEMS"
        return res
    res.add(AI, "Check completeness; categorize NIGO (AI)", "In good order")

    blocked = [d for d in ev.decisions if d.disposition is Disposition.BLOCK]
    review = [d for d in ev.decisions if d.owner == "compliance" and d.disposition is not Disposition.BLOCK]
    if blocked:
        res.add(RULES, "Verify registration and licensing", "Fails", [d.reason for d in blocked],
                [d.rule_id for d in blocked])
        res.add(OPS, "Relationship declined", "Decline notice sent to the RIA")
        res.status = "DECLINED"
        return res
    if review and not decisions.compliance_signoff:
        res.add(RULES, "Verify registration and licensing", "Compliance review needed",
                [d.reason for d in review], [d.rule_id for d in review])
        res.status = "PENDING_REVIEW"
        return res
    res.add(RULES, "Verify registration and licensing",
            "Reviewed and signed off by compliance" if review else "Verified",
            [d.reason for d in review] or [f"{REGISTRATION_LABELS.get(app.registration, app.registration)}; "
                                           f"IAPD status {app.iapd_status}"],
            [d.rule_id for d in review])
    def _kyb_line(p):
        required = p.ownership_pct >= 25 or p.role == "control_person"
        status = "identity verified" if p.identity_verified else (
            "identity not verified" if required else "not a beneficial owner; verification not required")
        return f"{p.name} ({p.role.replace('_', ' ')}, {p.ownership_pct:g}%): {status}"
    res.add(RULES, "KYB and CIP on the firm; verify owners and control persons", "Complete",
            [_kyb_line(p) for p in app.people])
    _pep_media_step(res, app.people)

    subjects = [Subject("firm", Kind.ENTITY, app.legal_name, "applicant", country="United States",
                        address=app.principal_address)]
    subjects += [Subject(f"person:{i}", Kind.INDIVIDUAL, p.name, p.role, p.date_of_birth,
                         p.country_of_residence, p.address, ownership_pct=p.ownership_pct)
                 for i, p in enumerate(app.people)]
    stop = _screen_and_escalate(res, subjects, flagged or [], registry, decisions, by, as_of, entity_id="firm")
    if stop:
        if stop == "BLOCKED":
            res.add(OPS, "Relationship declined", "The sanctions team directs next steps")
            stop = "DECLINED"
        res.status = stop
        return res

    risk = rate_firm(app, as_of)
    res.risk = risk
    res.add(RULES, "Risk-rate the RIA", f"{risk.tier} ({risk.score} points)",
            [f"{f['description']} (+{f['points']})" for f in risk.factors] or ["No risk factors"],
            [f["factor_id"] for f in risk.factors])
    stop = _edd(res, risk, registry, decisions, by,
                "EDD: owners' source of wealth, source of firm funds, enhanced review")
    if stop:
        res.status = stop
        return res

    reliance = firm_reliance(app, as_of)
    res.reliance = reliance
    res.add(RULES, "Determine reliance eligibility",
            "Eligible: pure RIA accounts can use Path A" if reliance.eligible else
            "Not eligible: the firm's accounts take Path B", reliance.reasons)
    ria = to_ria_firm(app, active=True)
    res.record = ria
    ident = app.crd_number or "NEW"
    res.add(LEDGER, "Open master and house accounts; set users, entitlements, fee-deduction authority",
            "Opened", [f"Master MSTR-{ident}", f"House HSE-{ident}", "Fee-deduction authority on file"])
    timers = [f"Rolling review {risk.next_review.isoformat()} ({risk.review_months} months, {risk.tier} risk)"]
    if app.aml_certification_date:
        due = app.aml_certification_date + timedelta(days=365)
        timers.append(f"Annual AML certification {'overdue since' if due < as_of else 'due'} {due.isoformat()}")
    res.add(LEDGER, "Schedule rolling review and annual certification timer", "Scheduled", timers)
    res.status = "ACTIVE"
    return res


def onboard_firm_from_package(document_text: str, as_of: date, *, client=None, payload: dict | None = None,
                              **kwargs) -> OnboardingResult:
    """
    Extraction first, then onboarding. Pass a client for a live model call, or
    a payload (model output already in hand) to run only the controls gate.
    """
    if payload is not None:
        extraction = gate(payload, document_text, "end_turn", "illustrative output")
    else:
        extraction = extract_firm_application(document_text, client=client)
    return onboard_firm(extraction.application, as_of, extraction=extraction, **kwargs)


# ---------------------------------------------------------------------------
# Client account opening (map 02)
# ---------------------------------------------------------------------------

def open_account(app: AccountApplication, ria_app: FirmApplication, as_of: date, *,
                 existing: list[Account] | None = None, ria_active: bool = True, ria_risk_tier: str = "LOW",
                 flagged=None, registry=None, decisions=None, by: str = "ops.analyst",
                 security_master: dict | None = None,
                 liquidation_elections: set[str] | None = None) -> OnboardingResult:
    decisions = decisions or Decisions()
    registry = registry or RestrictionRegistry()
    primary = app.primary.name if app.primary else app.account_id
    res = OnboardingResult("account", app.entity_name or primary, app.account_id, as_of, registry=registry)
    group = "self-directed" if app.group is AccountGroup.SELF_DIRECTED else "pure RIA"
    res.add(OPS, "Account application received", "Received",
            [f"{app.account_id}: {group} {TYPE_LABELS.get(app.registration_type, app.registration_type)} "
             f"account under {ria_app.legal_name}"])

    ria = to_ria_firm(ria_app, active=ria_active)
    account = Account(app.account_id, ria, app.group)
    allowed, reason = can_open(account, existing or [])        # Milestone 1
    gate_step = "Gate: RIA active; a self-directed account needs an active RIA-managed account, one per client"
    if not allowed:
        res.add(RULES, gate_step, "Not eligible", [reason])
        res.add(OPS, "Application returned", "Returned to the RIA", [reason])
        res.status = "RETURNED"
        return res
    res.add(RULES, gate_step, "Eligible")

    path = determine_path(account, as_of)                      # Milestone 1
    path_notes = []
    if path is Path.A and not reliance_relief_in_force(as_of):
        path = Path.B
        path_notes.append("SEC staff reliance relief is not in force on this date")
    res.path = path.value
    ev = evaluate_account(app, path.value, as_of)
    res.findings = ev
    if ev.decisions:
        res.add(AI, "Extract application, title, LPOA, tax forms, entity documents; categorize NIGO (AI)",
                "Not in good order", [d.reason for d in ev.decisions], ev.rule_ids)
        res.requested_items = ev.required_documents
        res.add(OPS, "Request corrections from the RIA", "Sent", res.requested_items)
        res.status = "PENDING_ITEMS"
        return res
    res.add(AI, "Extract application, title, LPOA, tax forms, entity documents; categorize NIGO (AI)",
            "In good order")

    if path is Path.A:
        res.add(RULES, "Path A applies?", "Yes: pure RIA account under a reliance-eligible RIA")
        res.add(RULES, "Path A: rely on RIA for CIP and beneficial ownership; RIA-provided information only",
                "Relied on the RIA", ["The custodian stays responsible for its AML program, SARs, and sanctions"])
    else:
        reliance = firm_reliance(ria_app, as_of)
        why = path_notes or (["Self-directed account"] if app.group is AccountGroup.SELF_DIRECTED
                             else reliance.reasons)
        res.add(RULES, "Path A applies?", "No: Path B, the custodian carries CIP and CDD", why)
        people = [p for p in app.holders if p.role in ("account_holder", "control_person", "owner", "trustee")]
        unverified = [p.name for p in people if not p.identity_verified]
        if unverified:
            registry.place(app.account_id, "CIPV", "Identity verification pending: " + ", ".join(unverified),
                           by, Function.OPERATIONS, _stamp(as_of))
            res.add(OPS, "Path B: verify identity (documentary or non-documentary)",
                    "Pending; outgoing activity limited until verified (CIPV)",
                    unverified + ["If identity can't be verified: request more documents, then decline"])
        else:
            res.add(OPS, "Path B: verify identity (documentary or non-documentary)", "Verified",
                    [p.name for p in people])
        cdd = [f"Purpose: {app.account_purpose}"]
        if app.registration_type in ("llc", "corporation", "partnership"):
            if app.beneficial_ownership_certification:
                cdd.append("Beneficial ownership certification received")
            else:
                cdd.append("Beneficial owners on file from the entity's first account, confirmed as current "
                           "(FinCEN exceptive relief, Feb. 13, 2026); confirmation recorded")
        res.add(RULES, "Path B: CDD on purpose, expected activity, beneficial owners of entities", "Complete", cdd)
        res.add(LEDGER, "Path B: enroll in account-level AML monitoring and trading surveillance", "Enrolled")

    _pep_media_step(res, app.holders)
    subjects = [Subject(f"holder:{i}", Kind.INDIVIDUAL, p.name, p.role, p.date_of_birth,
                        p.country_of_residence, p.address, ownership_pct=p.ownership_pct)
                for i, p in enumerate(app.holders)]
    entity_id = None
    if app.entity_name:
        entity_id = "entity"
        subjects.insert(0, Subject("entity", Kind.ENTITY, app.entity_name, "account_owner"))
    master = SECURITY_MASTER if security_master is None else security_master
    for pos in app.funding_positions:
        sec = master.get(pos.security_id)
        ids = {"cusip": pos.security_id}
        if sec and sec.isin:
            ids["isin"] = sec.isin
        subjects.append(Subject(f"product:{pos.security_id}", Kind.PRODUCT, pos.description, "funding asset",
                                identifiers=ids))
    stop = _screen_and_escalate(res, subjects, flagged or [], registry, decisions, by, as_of, entity_id)
    if stop:
        if stop == "BLOCKED":
            res.add(OPS, "Not opened", "The sanctions team directs next steps")
        res.status = stop
        return res

    risk = rate_account(app, as_of, ria_risk_tier, path.value)
    res.risk = risk
    res.add(RULES, "Risk-rate the account", f"{risk.tier} ({risk.score} points)",
            [f"Rated on {risk.information_basis}"]
            + ([f"{f['description']} (+{f['points']})" for f in risk.factors] or ["No risk factors"]),
            [f["factor_id"] for f in risk.factors])
    stop = _edd(res, risk, registry, decisions, by, "EDD: source of funds and source of wealth; approve")
    if stop:
        res.status = stop
        return res

    res.record = account
    codes = registry.active_codes(app.account_id)
    ident = ria_app.crd_number or "NEW"
    res.add(LEDGER, "Create account under RIA master; map to GL; apply any restriction codes", "Opened",
            [f"{app.account_id} under master MSTR-{ident}",
             "Restriction codes: " + (", ".join(codes) if codes else "none")])
    res.add(LEDGER, "Schedule rolling review by risk level", "Scheduled",
            [f"Next review {risk.next_review.isoformat()} ({risk.review_months} months)"])
    if app.funding_positions:
        review = review_transfer(app.funding_positions, "incoming", as_of, security_master,
                                 liquidation_elections)
        res.transfer = review
        res.add(OPS, "Transferability review of funding assets (shared subprocess)",
                f"Release: {review.release}",
                [f"{d.description}: {ROUTE_LABELS[d.route.value]}. {d.reason}" for d in review.dispositions])
    res.status = "OPEN"
    return res
