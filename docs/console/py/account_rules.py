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


def beneficial_owners_needed_v2(ctx: AccountContext, as_of: date) -> bool:
    app = ctx.application
    return app.first_entity_account or not app.bo_info_confirmed_current


COMPUTED = {
    "missing_cip_information": missing_cip_information,
    "beneficial_owners_needed_v2": beneficial_owners_needed_v2,
    "is_legal_entity_customer": lambda c, d: c.application.registration_type in LEGAL_ENTITY_TYPES,
    "is_entity": lambda c, d: c.application.registration_type in LEGAL_ENTITY_TYPES + ("trust",),
}

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
