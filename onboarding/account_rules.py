"""
Account-level rules for client account opening (client account opening map, 02).

Two kinds of finding:
  - in good order: the signed application, the RIA's LPOA and fee
    authorization, tax forms, entity documents, and the minimum CIP
    information. Missing items go back to the RIA in one request.
  - Path B due diligence: when the custodian carries CIP and CDD itself, it
    also needs the account's purpose and, for a legal entity customer, its
    beneficial owners.

On Path A the custodian relies on the RIA for CIP and beneficial ownership and
uses RIA-provided information only, so the Path B rules don't apply.

Regulatory and non-regulatory findings (Shawn's direction, Oct. 11, 2026): an
account whose KYC, CDD, EDD, CIP, and AML requirements are met may open with
non-regulatory items still outstanding, restricted from trading and fee
deduction until they arrive. Only the fee authorization and the tax form can
wait (NON_REGULATORY below). The client's signature and the LPOA hold the
account: without the signature the custodian can't process the client's
information, and without the LPOA the RIA can't act on the account or see the
client's information.

The beneficial ownership rule has two dated versions. Until Feb. 13, 2026 the
owners were identified and verified at every new account. FinCEN's exceptive
relief of that date limits it to the first account, or later when earlier
information is in doubt or the customer can't confirm it is current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from applications import AccountApplication
from rules import Disposition, Evaluation, evaluate_all

BASELINE = "2026-01-01"
LEGAL_ENTITY_TYPES = ("llc", "corporation", "partnership")
CIP_SOURCE = "31 CFR 1023.220(a)(2)(i)(A) (name, date of birth, address, identification number)"


@dataclass
class AccountContext:
    application: AccountApplication
    path: str            # "A" or "B"


def missing_cip_information(ctx: AccountContext, as_of: date) -> list[str]:
    app = ctx.application
    gaps = []
    people = [p for p in app.holders if p.role in ("account_holder", "trustee", "authorized_signer")] \
        or app.holders
    if app.registration_type in LEGAL_ENTITY_TYPES:
        people = [p for p in app.holders if p.role == "control_person"]
    for p in people:
        missing = [label for label, value in (("date of birth", p.date_of_birth), ("address", p.address),
                                               ("identification number", p.tax_id)) if not value]
        if missing:
            gaps.append(f"{p.name}: {', '.join(missing)}")
    return gaps


def _natural_people(app: AccountApplication) -> list:
    if app.registration_type in LEGAL_ENTITY_TYPES:
        return [p for p in app.holders if p.role == "control_person"]
    return [p for p in app.holders if p.role in ("account_holder", "trustee", "authorized_signer")] or app.holders


def missing_phone(ctx: AccountContext, as_of: date) -> list[str]:
    """Each account holder (or an entity's control person) needs a phone number on file: callbacks dial it."""
    return [p.name for p in _natural_people(ctx.application) if not (p.phone or "").strip()]


def beneficiary_shares_off(ctx: AccountContext, as_of: date) -> list[str]:
    """Primary beneficiaries' shares add up to 100%, and so do contingent ones when there are any."""
    out = []
    for kind in ("primary", "contingent"):
        rows = [b for b in ctx.application.beneficiaries if (b.get("kind") or "primary") == kind]
        total = round(sum(float(b.get("share") or 0) for b in rows), 4)
        if rows and abs(total - 100) > 0.001:
            out.append(f"{kind.capitalize()} beneficiaries add up to {total:g}%")
    return out


def tenancy_shares_off(ctx: AccountContext, as_of: date) -> bool:
    app = ctx.application
    if app.registration_type != "joint" or app.joint_tenancy != "tenants_in_common":
        return False
    holders = [p for p in app.holders if p.role == "account_holder"]
    return abs(sum(p.ownership_pct for p in holders) - 100) > 0.001


def needs_employer_consent(ctx: AccountContext, as_of: date) -> list[str]:
    app = ctx.application
    return [] if app.employer_consent else [f"{p.name} ({p.finra_affiliation or 'a FINRA member'})"
                                            for p in app.holders if p.finra_affiliated]


def beneficial_owners_needed_v2(ctx: AccountContext, as_of: date) -> bool:
    app = ctx.application
    return app.first_entity_account or not app.bo_info_confirmed_current


# Missing one of these doesn't stop an account opening when every regulatory requirement is met: the account
# opens restricted from trading and fee deduction (code DOCS) until the item arrives. Confirmed by Shawn,
# Oct. 11, 2026. ACC-001 (client signature) and ACC-002 (LPOA) are not here: a missing one holds the account.
NON_REGULATORY = {
    "ACC-003": "Fee deduction authority is the adviser's obligation under the custody rule; the custodian "
               "deducts no fees until it arrives",
    "ACC-004": "The tax certification is an IRS requirement, not KYC or AML; until it arrives the custodian "
               "withholds as the tax rules require (backup withholding for a missing W-9)",
}


COMPUTED = {
    "missing_cip_information": missing_cip_information,
    "beneficial_owners_needed_v2": beneficial_owners_needed_v2,
    "is_legal_entity_customer": lambda c, d: c.application.registration_type in LEGAL_ENTITY_TYPES,
    "is_entity": lambda c, d: c.application.registration_type in LEGAL_ENTITY_TYPES + ("trust",),
    "missing_phone": missing_phone,
    "beneficiary_shares_off": beneficiary_shares_off,
    "tenancy_shares_off": tenancy_shares_off,
    "needs_employer_consent": needs_employer_consent,
}

REVIEW = "2026-10-25"   # added after the industry-practice review (Shawn, Oct. 25, 2026)

ACCOUNT_RULES = [
    # --- In good order -----------------------------------------------------
    {"rule_id": "ACC-001", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy: the client signs the account application",
     "field": "application.client_signature", "operator": "equals", "value": False,
     "disposition": Disposition.REFER, "reason": "Client signature missing",
     "required_documents": ["Client-signed account application"]},
    {"rule_id": "ACC-002", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy: the RIA's trading authority comes from a client-signed limited power of attorney",
     "all": [{"field": "application.group.value", "operator": "equals", "value": "pure_ria"},
             {"field": "application.lpoa_signed", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER, "reason": "Limited power of attorney for the RIA missing",
     "required_documents": ["Client-signed LPOA"]},
    {"rule_id": "ACC-003", "version": "1", "effective_date": BASELINE,
     "source": "Client's written authorization for direct fee deduction (NASAA model custody rule for "
               "state advisers; custodian policy for all advisers)",
     "all": [{"field": "application.group.value", "operator": "equals", "value": "pure_ria"},
             {"field": "application.fee_authorization", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER, "reason": "Fee deduction authorization missing",
     "required_documents": ["Client-signed fee authorization"]},
    {"rule_id": "ACC-004", "version": "1", "effective_date": BASELINE,
     "source": "IRS Form W-9 (26 U.S.C. 3406 backup withholding); Forms W-8BEN and W-8BEN-E (26 CFR 1.1441-1)",
     "field": "application.tax_form", "operator": "is_blank",
     "disposition": Disposition.REFER, "reason": "Tax certification missing",
     "required_documents": ["Form W-9 or W-8"]},
    {"rule_id": "ACC-005", "version": "1", "effective_date": BASELINE, "source": CIP_SOURCE,
     "field": "missing_cip_information", "operator": "is_present",
     "disposition": Disposition.REFER, "reason": "Minimum CIP information missing",
     "required_documents": ["Complete identifying information"]},
    {"rule_id": "ACC-006", "version": "1", "effective_date": BASELINE,
     "source": "31 CFR 1023.220(a)(2)(ii)(A) (documents showing a non-individual customer exists)",
     "all": [{"field": "is_entity", "operator": "equals", "value": True},
             {"field": "application.entity_formation_documents", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER, "reason": "Entity formation documents or trust instrument missing",
     "required_documents": ["Formation documents or trust instrument"]},
    {"rule_id": "ACC-007", "version": "1", "effective_date": BASELINE,
     "source": "31 CFR 1023.220(a)(2)(i)(A)(4) (taxpayer identification number)",
     "all": [{"field": "is_entity", "operator": "equals", "value": True},
             {"field": "application.entity_ein", "operator": "is_blank"}],
     "disposition": Disposition.REFER, "reason": "Entity EIN missing",
     "required_documents": ["Entity EIN"]},
    # --- The account record (added after the industry-practice review) --------
    {"rule_id": "ACC-008", "version": "1", "effective_date": REVIEW,
     "source": "Platform policy: a phone number on file for each account holder, so a callback (MM-006) dials a "
               "number from the record, never one on the request",
     "field": "missing_phone", "operator": "is_present",
     "disposition": Disposition.REFER, "reason": "Phone number missing for an account holder",
     "required_documents": ["Phone number for each account holder"]},
    {"rule_id": "ACC-009", "version": "1", "effective_date": REVIEW,
     "source": "IRC 408 and 408A: the IRA type sets contribution, distribution, and reporting rules",
     "all": [{"field": "application.registration_type", "operator": "equals", "value": "ira"},
             {"field": "application.ira_type", "operator": "is_blank"}],
     "disposition": Disposition.REFER, "reason": "IRA type missing (traditional, Roth, SEP, or inherited)",
     "required_documents": ["IRA type"]},
    {"rule_id": "ACC-010", "version": "1", "effective_date": REVIEW,
     "source": "IRC 408(a) and (h): an IRA is held under a written custodial agreement (the custodian's "
               "adoption agreement)",
     "all": [{"field": "application.registration_type", "operator": "equals", "value": "ira"},
             {"field": "application.ira_adoption_agreement", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER, "reason": "IRA adoption agreement missing",
     "required_documents": ["Signed IRA adoption agreement"]},
    {"rule_id": "ACC-011", "version": "1", "effective_date": REVIEW,
     "source": "Platform policy: beneficiary shares must be complete, or the custodian can't settle a claim",
     "field": "beneficiary_shares_off", "operator": "is_present",
     "disposition": Disposition.REFER, "reason": "Beneficiary shares don't add up to 100%",
     "required_documents": ["Beneficiary designation with shares that add up to 100%"]},
    {"rule_id": "ACC-012", "version": "1", "effective_date": REVIEW,
     "source": "State property law: a joint account's tenancy decides who owns it at a death",
     "all": [{"field": "application.registration_type", "operator": "equals", "value": "joint"},
             {"field": "application.joint_tenancy", "operator": "is_blank"}],
     "disposition": Disposition.REFER, "reason": "Joint tenancy missing",
     "required_documents": ["Joint tenancy (JTWROS, tenants in common, community property, or entirety)"]},
    {"rule_id": "ACC-013", "version": "1", "effective_date": REVIEW,
     "source": "State property law: tenants in common hold stated shares",
     "field": "tenancy_shares_off", "operator": "equals", "value": True,
     "disposition": Disposition.REFER, "reason": "Tenants-in-common shares don't add up to 100%",
     "required_documents": ["Each joint owner's share"]},
    {"rule_id": "ACC-014", "version": "1", "effective_date": REVIEW,
     "source": "FINRA Rule 3210 (accounts at other broker-dealers: the employer's prior written consent)",
     "field": "needs_employer_consent", "operator": "is_present",
     "disposition": Disposition.REFER, "reason": "Account holder works at a FINRA member: employer's consent needed",
     "required_documents": ["Employer's written consent (FINRA Rule 3210)"]},
    # --- Path B: the custodian's own CDD ------------------------------------
    {"rule_id": "CDD-001", "version": "1", "effective_date": BASELINE,
     "source": "31 CFR 1023.210(b)(5)(i) (understand the nature and purpose of the relationship)",
     "all": [{"field": "path", "operator": "equals", "value": "B"},
             {"field": "application.account_purpose", "operator": "is_blank"}],
     "disposition": Disposition.REFER, "reason": "Account purpose and expected activity missing",
     "required_documents": ["Account purpose and expected activity"]},
    {"rule_id": "CDD-BO", "version": "1", "effective_date": "2018-05-11", "expiry_date": "2026-02-13",
     "source": "31 CFR 1010.230(b) (identify and verify beneficial owners at each new account)",
     "all": [{"field": "path", "operator": "equals", "value": "B"},
             {"field": "is_legal_entity_customer", "operator": "equals", "value": True},
             {"field": "application.beneficial_ownership_certification", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER, "reason": "Beneficial ownership certification needed for each new account",
     "required_documents": ["Beneficial ownership certification"]},
    {"rule_id": "CDD-BO", "version": "2", "effective_date": "2026-02-13",
     "source": "31 CFR 1010.230(b); FinCEN exceptive relief order FIN-2026-R001 (Feb. 13, 2026): "
               "beneficial owners at the first account, or when earlier information is in doubt or "
               "not confirmed as current",
     "all": [{"field": "path", "operator": "equals", "value": "B"},
             {"field": "is_legal_entity_customer", "operator": "equals", "value": True},
             {"field": "application.beneficial_ownership_certification", "operator": "equals", "value": False},
             {"field": "beneficial_owners_needed_v2", "operator": "equals", "value": True}],
     "disposition": Disposition.REFER,
     "reason": "Beneficial ownership certification needed: first account for this entity, or earlier "
               "information not confirmed as current",
     "required_documents": ["Beneficial ownership certification"]},
]


def evaluate_account(app: AccountApplication, path: str, as_of: date) -> Evaluation:
    return evaluate_all(AccountContext(app, path), ACCOUNT_RULES, as_of, COMPUTED)
