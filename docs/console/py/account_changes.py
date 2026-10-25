"""
Account changes along the account maintenance map (04), for the MVP.

Who may ask for a change depends on the account group:
  - pure RIA account: an authorized user of the RIA the account sits under.
    Owner-level changes (beneficiaries, registration, powers of attorney,
    standing letters of authorization, beneficial owners) also need the
    client's signature.
  - self-directed account: the client. The RIA has view-only access.
Operations can key a written request on the requester's behalf; the same
authority rules apply.

Then, in map order: in-good-order rules, a fraud-pattern check (address,
bank, and contact changes close together), the first-line flagged-list check
on new parties and banks, a due diligence re-run when ownership changes, and
the change is applied with notices. New bank instructions stay inactive until
verified.

Policy choices are labeled as such: the 30-day look-back for the fraud
pattern and the medallion requirement for registration changes are reference
design, not rules from regulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from applications import Person
from flagged_list import Kind, Subject
from models import AccountGroup
from pipeline import (AI, LEDGER, OPS, RESUBMIT, RULES, Decisions, OnboardingResult, _edd, _identity_issue,
                      _route_alerts, _screen_and_escalate, _unverified_issue)
from restrictions import RestrictionRegistry
from rules import Disposition, evaluate_all

BASELINE = "2026-01-01"
REVIEW = "2026-10-25"        # rules added after the industry-practice review
FRAUD_LOOKBACK_DAYS = 30     # platform policy


class ChangeType(Enum):
    ADDRESS = "address"
    CONTACT_INFO = "contact_info"
    BANK_INSTRUCTION = "bank_instruction"
    TRUSTED_CONTACT = "trusted_contact"
    BENEFICIARY = "beneficiary"
    REGISTRATION = "registration"
    POWER_OF_ATTORNEY = "power_of_attorney"
    STANDING_LOA = "standing_loa"
    BENEFICIAL_OWNER = "beneficial_owner"


OWNER_LEVEL = {ChangeType.BENEFICIARY, ChangeType.REGISTRATION, ChangeType.POWER_OF_ATTORNEY,
               ChangeType.STANDING_LOA, ChangeType.BENEFICIAL_OWNER}
FRAUD_SENSITIVE = {ChangeType.ADDRESS, ChangeType.CONTACT_INFO, ChangeType.BANK_INSTRUCTION}
OWNERSHIP = {ChangeType.BENEFICIAL_OWNER, ChangeType.REGISTRATION}

LABELS = {
    ChangeType.ADDRESS: "address", ChangeType.CONTACT_INFO: "email or phone",
    ChangeType.BANK_INSTRUCTION: "bank instructions", ChangeType.TRUSTED_CONTACT: "trusted contact",
    ChangeType.BENEFICIARY: "beneficiaries", ChangeType.REGISTRATION: "registration (title)",
    ChangeType.POWER_OF_ATTORNEY: "power of attorney", ChangeType.STANDING_LOA: "standing letter of authorization",
    ChangeType.BENEFICIAL_OWNER: "beneficial owners",
}


@dataclass
class ChangeRequest:
    request_id: str
    account_id: str
    account_group: AccountGroup
    account_firm: str                       # CRD of the RIA the account sits under
    change_type: ChangeType
    submitted_by_role: str                  # ria_user | ria_admin | client | ops_analyst
    submitted_by_firm: str | None = None    # requester's RIA (CRD), for RIA users
    submitted_by_client_of: str | None = None  # account id the client user owns
    details: dict = field(default_factory=dict)
    client_signature: bool = False
    medallion_guarantee: bool = False
    new_parties: list[Person] = field(default_factory=list)
    new_bank: dict | None = None            # {"name", "aba", "account_last4", "bic"?}
    recent_changes: list[tuple] = field(default_factory=list)   # (ChangeType, date) already applied
    path: str = "B"                         # the account's due diligence path
    firm_name: str | None = None            # the account's RIA, to check an SLOA payee isn't the adviser
    firm_address: str | None = None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _channel_ok(r: ChangeRequest, as_of: date) -> bool:
    if r.account_group is AccountGroup.PURE_RIA:
        if r.submitted_by_role in ("ria_user", "ria_admin"):
            return r.submitted_by_firm == r.account_firm
        return r.submitted_by_role == "ops_analyst"
    if r.submitted_by_role == "client":
        return r.submitted_by_client_of == r.account_id
    return r.submitted_by_role == "ops_analyst"


def _bank_incomplete(r: ChangeRequest, as_of: date) -> bool:
    if r.change_type is not ChangeType.BANK_INSTRUCTION:
        return False
    bank = r.new_bank or {}
    return not (bank.get("name") and bank.get("aba") and bank.get("account_last4"))


def _norm(text: str | None) -> str:
    return " ".join("".join(ch for ch in (text or "").lower() if ch.isalnum() or ch == " ").split())


def is_sloa_setup(r: ChangeRequest) -> bool:
    return r.change_type is ChangeType.STANDING_LOA and not r.details.get("end_sloa")


def _sloa_incomplete(r: ChangeRequest, as_of: date) -> bool:
    """SEC SLOA relief, condition 1: the client's instruction names the third party and either the third party's
    address or its account at a custodian."""
    d = r.details
    return is_sloa_setup(r) and not ((d.get("payee") or "").strip() and (
        (d.get("payee_address") or "").strip() or ((d.get("institution") or "").strip()
                                                   and (d.get("account_last4") or "").strip())))


def _sloa_related(r: ChangeRequest, as_of: date) -> bool:
    """Condition 6: the payee isn't the adviser, and isn't at the adviser's address."""
    if not is_sloa_setup(r):
        return False
    payee, where = _norm(r.details.get("payee")), _norm(r.details.get("payee_address"))
    return bool(payee and payee == _norm(r.firm_name)) or bool(where and where == _norm(r.firm_address))


COMPUTED = {
    "sloa_incomplete": _sloa_incomplete,
    "sloa_related": _sloa_related,
    "channel_ok": _channel_ok,
    "owner_level": lambda r, d: r.change_type in OWNER_LEVEL,
    "is_registration": lambda r, d: r.change_type is ChangeType.REGISTRATION,
    "bank_incomplete": _bank_incomplete,
    "group": lambda r, d: r.account_group.value,
}

CHANGE_RULES = [
    {"rule_id": "CHG-001", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy (account maintenance map, 04): the RIA is the channel for pure RIA "
               "accounts; the client acts on the self-directed account",
     "field": "channel_ok", "operator": "equals", "value": False,
     "disposition": Disposition.BLOCK,
     "reason": "Not an authorized requester for this account; redirected"},
    {"rule_id": "CHG-002", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy: owner-level changes carry the client's signature",
     "all": [{"field": "owner_level", "operator": "equals", "value": True},
             {"field": "client_signature", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER,
     "reason": "Owner-level change needs the client's signature",
     "required_documents": ["Client-signed change form"]},
    {"rule_id": "CHG-003", "version": "1", "effective_date": BASELINE,
     "source": "Industry practice (reference design): re-registration needs a medallion signature guarantee",
     "all": [{"field": "is_registration", "operator": "equals", "value": True},
             {"field": "medallion_guarantee", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER,
     "reason": "Registration change needs a medallion signature guarantee",
     "required_documents": ["Medallion signature guarantee"]},
    {"rule_id": "CHG-004", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy: a bank instruction names the bank, its routing number, and the account",
     "field": "bank_incomplete", "operator": "equals", "value": True,
     "disposition": Disposition.REFER,
     "reason": "Bank instruction incomplete",
     "required_documents": ["Bank name, ABA routing number, and account number"]},
    {"rule_id": "CHG-005", "version": "1", "effective_date": REVIEW,
     "source": "SEC no-action letter on standing letters of authorization (Feb. 21, 2017), condition 1",
     "field": "sloa_incomplete", "operator": "equals", "value": True,
     "disposition": Disposition.REFER,
     "reason": "The SLOA must name the payee and either the payee's address or the payee's bank account",
     "required_documents": ["Payee's name, and the payee's address or bank and account"]},
    {"rule_id": "CHG-006", "version": "1", "effective_date": REVIEW,
     "source": "SEC no-action letter on standing letters of authorization (Feb. 21, 2017), condition 6",
     "field": "sloa_related", "operator": "equals", "value": True,
     "disposition": Disposition.REFER,
     "reason": "The payee is the adviser or at the adviser's address, so the SLOA relief doesn't cover it",
     "required_documents": ["A payee who isn't the adviser or at its address (the client can still send a signed "
                            "request)"]},
]


def fraud_pattern(r: ChangeRequest, as_of: date) -> list[str]:
    """
    Account-takeover red flags (platform policy): a fraud-sensitive change
    alongside another fraud-sensitive change of a different kind in the last
    30 days.
    """
    if r.change_type not in FRAUD_SENSITIVE:
        return []
    since = as_of - timedelta(days=FRAUD_LOOKBACK_DAYS)
    hits = []
    for kind, when in r.recent_changes:
        kind = ChangeType(kind) if not isinstance(kind, ChangeType) else kind
        if kind in FRAUD_SENSITIVE and kind is not r.change_type and when >= since:
            hits.append(f"{LABELS[kind].capitalize()} changed on {when.isoformat()}")
    return hits


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

def process_change(r: ChangeRequest, as_of: date, *, flagged=None, registry=None, decisions=None,
                   confirmation: str | None = None, by: str = "ops.analyst",
                   identity_issue: dict | None = None) -> OnboardingResult:
    """
    Run a change request along map 04. `confirmation` is the outcome of the RIA
    confirmation and client callback when the fraud pattern fires:
    "confirmed" or "rejected".
    """
    decisions = decisions or Decisions()
    registry = registry or RestrictionRegistry()
    res = OnboardingResult("change", f"{LABELS[r.change_type].capitalize()} change on {r.account_id}",
                           r.account_id, as_of, registry=registry)
    who = {"ria_user": "RIA user", "ria_admin": "RIA administrator", "client": "client",
           "ops_analyst": "operations, keying a written request"}.get(r.submitted_by_role, r.submitted_by_role)
    res.add(OPS, "Change request received", "Received", [f"{r.request_id}: {LABELS[r.change_type]}, from the {who}"])

    ev = evaluate_all(r, CHANGE_RULES, as_of, COMPUTED)
    res.findings = ev
    blocked = [d for d in ev.decisions if d.disposition is Disposition.BLOCK]
    group = "pure RIA" if r.account_group is AccountGroup.PURE_RIA else "self-directed"
    if blocked:
        expected = "an authorized user of the account's RIA" if r.account_group is AccountGroup.PURE_RIA \
            else "the client who owns the account"
        res.add(RULES, "Channel check: who may change this account", "Not authorized",
                [f"A {group} account takes changes from {expected}"], [d.rule_id for d in blocked])
        res.add(OPS, "Redirected", "Returned to the requester with the right channel")
        res.status = "REDIRECTED"
        return res
    res.add(RULES, "Channel check: who may change this account", "Authorized", [f"{group} account"])
    res.add(AI, "Classify the change; extract forms and documents (AI)", LABELS[r.change_type].capitalize(),
            ["Owner-level change" if r.change_type in OWNER_LEVEL else "Not owner-level"])

    nigo = [d for d in ev.decisions if d.disposition is Disposition.REFER]
    if nigo:
        res.add(RULES, "Authority check: within the RIA's LPOA, or a client-signed form required",
                "Not in good order", [d.reason for d in nigo], [d.rule_id for d in nigo])
        res.requested_items = ev.required_documents
        res.add(OPS, "Return for correction", "Sent", res.requested_items)
        res.status = "RETURNED"
        return res
    res.add(RULES, "Authority check: within the RIA's LPOA, or a client-signed form required", "In good order")

    flags = fraud_pattern(r, as_of)
    if flags:
        res.add(RULES, "Fraud-pattern check: address, bank, and contact changes together", "Suspicious pattern",
                flags, ["FRD-001"])
        if confirmation is None:
            res.add(OPS, "Confirm with the RIA; call back the client", "Waiting for confirmation")
            res.status = "PENDING_CONFIRMATION"
            return res
        if confirmation == "rejected":
            res.add(OPS, "Confirm with the RIA; call back the client", "Not confirmed")
            res.add(OPS, "Rejected; fraud case opened", "The change isn't applied")
            res.status = "REJECTED"
            return res
        res.add(OPS, "Confirm with the RIA; call back the client", "Confirmed")
    else:
        res.add(RULES, "Fraud-pattern check: address, bank, and contact changes together", "No pattern")

    subjects = [Subject(f"party:{i}", Kind.INDIVIDUAL, p.name, p.role, p.date_of_birth, p.country_of_residence,
                        p.address, ownership_pct=p.ownership_pct) for i, p in enumerate(r.new_parties)]
    if r.new_bank:
        ids = {k: r.new_bank[k] for k in ("aba", "bic") if r.new_bank.get(k)}
        subjects.append(Subject("bank", Kind.INSTITUTION, r.new_bank.get("name", ""), "receiving_bank",
                                identifiers=ids))
    if is_sloa_setup(r):                        # the SLOA's payee and their bank are new parties too
        subjects.append(Subject("sloa_payee", Kind.ENTITY if r.details.get("payee_kind") == "entity"
                                else Kind.INDIVIDUAL, r.details.get("payee", ""), "payee",
                                address=r.details.get("payee_address")))
        if r.details.get("institution"):
            ids = {"aba": r.details["aba"]} if r.details.get("aba") else {}
            subjects.append(Subject("sloa_bank", Kind.INSTITUTION, r.details["institution"], "payee_bank",
                                    identifiers=ids))
    if subjects:
        stop = _screen_and_escalate(res, subjects, flagged or [], registry, decisions, by, as_of)
        if stop:
            if stop == "BLOCKED":
                res.add(OPS, "Change stopped", "The sanctions team directs next steps")
            res.status = stop
            return res
    else:
        res.add(RULES, "First-line check on new parties: owner, trustee, beneficiary, bank", "No new parties")

    # A new owner, trustee, or beneficiary who is a PEP or has adverse media goes to AML compliance, which clears
    # the alert or requires EDD before the change is applied
    stop = _route_alerts(res, r.new_parties, False, decisions) or _edd(
        res, None, registry, decisions, by, "EDD on the new party before the change is applied", hold=False)
    if stop:
        res.status = "REJECTED" if stop == "DECLINED" else stop
        return res

    if is_sloa_setup(r):
        step = "Verify the SLOA: signature review, and a callback to the client on a number on file"
        if confirmation is None:
            res.add(OPS, step, "Waiting for client service", ["SEC SLOA relief, condition 3: the custodian verifies "
                                                              "the client's instruction"])
            res.status = "PENDING_CONFIRMATION"
            return res
        if confirmation == "rejected":
            res.add(OPS, step, "Not confirmed")
            res.add(OPS, "Rejected", "The SLOA isn't set up")
            res.status = "REJECTED"
            return res
        res.add(OPS, step, "Verified")

    if r.change_type in OWNERSHIP:
        unverified = [p.name for p in r.new_parties if not p.identity_verified]
        if r.path == "A":
            res.add(RULES, "Re-run due diligence: CIP on new parties", "Relied on the RIA (Path A)",
                    [p.name for p in r.new_parties] or ["No new parties named"])
        elif unverified:
            stop = _identity_issue(res, _unverified_issue(identity_issue, unverified), decisions, registry, by)
            if stop == RESUBMIT:                      # back to the requester for new identity documents
                res.add(OPS, "Return for correction", "Sent", res.requested_items)
                res.status = "RETURNED"
                return res
            if stop:
                res.status = "REJECTED" if stop == "DECLINED" else stop
                return res
            res.add(RULES, "Re-run due diligence: CIP on new parties", "Identity verification pending", unverified)
            res.status = "PENDING_VERIFICATION"
            return res
        else:
            res.add(RULES, "Re-run due diligence: CIP on new parties", "Verified", [p.name for p in r.new_parties])

    applied = [f"{k.replace('_', ' ')}: {v}" for k, v in r.details.items()] or [LABELS[r.change_type]]
    if r.change_type is ChangeType.BANK_INSTRUCTION:
        applied.append("New bank link stays inactive until it is verified")
    if is_sloa_setup(r):
        applied += ["The client's signature on the SLOA was verified when the signed form was checked",
                    "The RIA can send payments to this payee only, with these details; it can't change them",
                    "Reconfirmed with the client every year; the client can end it at any time"]
    elif r.change_type is ChangeType.STANDING_LOA:
        applied.append(f"{r.details['end_sloa']} ended: the RIA can't send further payments under it")
    res.add(LEDGER, "Apply change with audit trail", "Applied", applied)
    if is_sloa_setup(r):
        notices = ["The client: initial notice confirming the standing instruction (SLOA relief, condition 7)",
                   "The RIA"]
    elif r.change_type is ChangeType.ADDRESS:
        notices = ["The client at the old address", "The client at the new address", "The RIA"]
    elif r.account_group is AccountGroup.SELF_DIRECTED:
        notices = ["The client", "The RIA (view-only)"]
    else:
        notices = ["The client", "The RIA"]
    res.add(OPS, "Send confirmation notices", "Sent", notices)
    res.add(LEDGER, "Refresh the rolling-review profile", "Updated",
            ["Ownership changed: early review triggered"] if r.change_type in OWNERSHIP else ["Profile updated"])
    res.status = "APPLIED"
    return res
