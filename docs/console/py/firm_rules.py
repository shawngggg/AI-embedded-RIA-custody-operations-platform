"""
Firm-level rules for RIA firm onboarding (Milestone 2): registration and
licensing, KYB, and CIP on the firm, plus the reliance determination that
decides whether the firm's pure RIA accounts can use Path A.

These checks always run on the firm, whatever path its clients' accounts take
later (RIA firm onboarding map, 01).

Effective dates: a rule's effective_date is the date that version entered the
platform's policy. Most rules start at the policy baseline (2026-01-01) and
cite the regulation they implement. Where the regulation itself changed on a
known date, the versions follow that date: the SEC staff reliance relief
expires on 2028-01-01 unless it is extended again.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from applications import FirmApplication
from models import RIAFirm, reliance_eligible
from rules import Disposition, Evaluation, evaluate_all, in_force

BASELINE = "2026-01-01"

# ---------------------------------------------------------------------------
# Computed fields: judgments derived from the stated facts
# ---------------------------------------------------------------------------


def _fiscal_year_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def last_fiscal_year_end(as_of: date, month: int) -> date:
    """The most recent fiscal year end before the evaluation date."""
    end = _fiscal_year_end(as_of.year, month)
    return end if end < as_of else _fiscal_year_end(as_of.year - 1, month)


def adv_annual_amendment_overdue(app: FirmApplication, as_of: date) -> bool:
    """
    Rule 204-1(a): the annual updating amendment to Form ADV is due within 90
    days after the end of the adviser's fiscal year. Overdue means the
    amendment for the latest fiscal year whose deadline has passed is missing.
    """
    if app.registration == "none":
        return False
    fye = last_fiscal_year_end(as_of, app.fiscal_year_end_month)
    if as_of <= fye + timedelta(days=90):
        fye = last_fiscal_year_end(fye, app.fiscal_year_end_month)
    if app.registration_date and app.registration_date > fye:
        return False  # registered after that fiscal year ended; nothing due yet
    filed = app.adv_last_annual_amendment
    return not (filed and filed > fye)


PO_BOX = re.compile(r"\b(p\.?\s*o\.?\s*box|post\s+office\s+box)\b", re.IGNORECASE)


def address_is_po_box(app: FirmApplication, as_of: date) -> bool:
    return bool(app.principal_address and PO_BOX.search(app.principal_address))


def beneficial_owners(app: FirmApplication) -> list:
    """Ownership prong: each individual with 25% or more of the equity."""
    return [p for p in app.people if p.ownership_pct >= 25]


def control_persons(app: FirmApplication) -> list:
    """Control prong: an individual with significant responsibility to manage the firm."""
    return [p for p in app.people if p.role == "control_person"]


def unverified_beneficial_owners(app: FirmApplication, as_of: date) -> list[str]:
    people = beneficial_owners(app) + [p for p in control_persons(app) if p not in beneficial_owners(app)]
    return [p.name for p in people if not p.identity_verified]


def unregistered_iars(app: FirmApplication, as_of: date) -> list[str]:
    return [p.name for p in app.people if p.iar_registration_required and not p.iar_registered]


COMPUTED = {
    "adv_annual_amendment_overdue": adv_annual_amendment_overdue,
    "address_is_po_box": address_is_po_box,
    "control_person_count": lambda app, as_of: len(control_persons(app)),
    "unverified_beneficial_owners": unverified_beneficial_owners,
    "unregistered_iars": unregistered_iars,
}

# ---------------------------------------------------------------------------
# The firm rule library
# ---------------------------------------------------------------------------

FIRM_RULES = [
    # --- Registration and licensing ---------------------------------------
    {
        "rule_id": "REG-001", "version": "1", "effective_date": BASELINE,
        "source": "Advisers Act §203(a) and state investment adviser registration; platform policy "
                  "(the platform custodies for registered advisers only)",
        "field": "registration", "operator": "equals", "value": "none",
        "disposition": Disposition.BLOCK, "owner": "compliance",
        "reason": "Not registered as an investment adviser with the SEC or a state",
    },
    {
        "rule_id": "REG-002", "version": "1", "effective_date": BASELINE,
        "source": "SEC Investment Adviser Public Disclosure (IAPD) registration status",
        "all": [
            {"field": "registration", "operator": "in", "value": ["SEC", "state"]},
            {"field": "iapd_status", "operator": "not_equals", "value": "approved"},
        ],
        "disposition": Disposition.REFER, "owner": "compliance",
        "reason": "Registration is not in approved status on IAPD; confirm before approval",
    },
    {
        "rule_id": "REG-003", "version": "1", "effective_date": BASELINE,
        "source": "Form ADV Part 1A Item 1 (SEC file number and CRD number)",
        "all": [
            {"field": "registration", "operator": "equals", "value": "SEC"},
            {"field": "sec_file_number", "operator": "is_blank"},
        ],
        "disposition": Disposition.REFER,
        "reason": "SEC file number missing",
        "required_documents": ["SEC file number (801-)"],
    },
    {
        "rule_id": "REG-004", "version": "1", "effective_date": BASELINE,
        "source": "Form ADV Part 1A Item 1 (CRD number)",
        "all": [
            {"field": "registration", "operator": "in", "value": ["SEC", "state"]},
            {"field": "crd_number", "operator": "is_blank"},
        ],
        "disposition": Disposition.REFER,
        "reason": "CRD number missing",
        "required_documents": ["CRD number"],
    },
    {
        "rule_id": "REG-005", "version": "1", "effective_date": BASELINE,
        "source": "Advisers Act §203A; Rule 203A-1 (an adviser with $110 million or more in RAUM "
                  "registers with the SEC)",
        "all": [
            {"field": "registration", "operator": "equals", "value": "state"},
            {"field": "raum", "operator": "greater_than_or_equal", "value": 110_000_000},
        ],
        "disposition": Disposition.REFER, "owner": "compliance",
        "reason": "State-registered with RAUM of $110 million or more; SEC registration is generally required",
    },
    {
        "rule_id": "REG-006", "version": "1", "effective_date": BASELINE,
        "source": "Rule 203A-1 (an SEC-registered adviser below $90 million in RAUM generally "
                  "withdraws) and the Rule 203A-2 exemptions",
        "all": [
            {"field": "registration", "operator": "equals", "value": "SEC"},
            {"field": "raum", "operator": "less_than", "value": 90_000_000},
            {"field": "sec_registration_basis", "operator": "is_blank"},
        ],
        "disposition": Disposition.REFER, "owner": "compliance",
        "reason": "SEC-registered with RAUM under $90 million and no exemption stated; confirm the "
                  "basis for SEC registration",
    },
    {
        "rule_id": "REG-007", "version": "1", "effective_date": BASELINE,
        "source": "Advisers Act Rule 204-1(a) (annual updating amendment within 90 days of fiscal year end)",
        "field": "adv_annual_amendment_overdue", "operator": "equals", "value": True,
        "disposition": Disposition.REFER, "owner": "compliance",
        "reason": "Form ADV annual updating amendment is overdue",
        "required_documents": ["Current Form ADV"],
    },
    {
        "rule_id": "REG-008", "version": "1", "effective_date": BASELINE,
        "source": "State investment adviser representative registration (Uniform Securities Act; "
                  "Series 65 or 66 qualification)",
        "field": "unregistered_iars", "operator": "is_present",
        "disposition": Disposition.REFER, "owner": "compliance",
        "reason": "Investment adviser representative not registered where required",
        "required_documents": ["IAR registration evidence"],
    },
    {
        "rule_id": "REG-009", "version": "1", "effective_date": BASELINE,
        "source": "Form ADV Part 1A Item 11 (disciplinary information); IAPD and BrokerCheck",
        "field": "disciplinary_disclosures", "operator": "equals", "value": True,
        "disposition": Disposition.REFER, "owner": "compliance",
        "reason": "Disciplinary disclosures on record; compliance review, and the history feeds the risk rating",
    },
    # --- KYB: the firm as a legal entity customer -------------------------
    {
        "rule_id": "KYB-001", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1023.220(a)(2)(ii)(A) (documents showing a non-individual customer exists)",
        "field": "formation_documents", "operator": "equals", "value": False,
        "disposition": Disposition.REFER,
        "reason": "Formation documents missing",
        "required_documents": ["Certificate of formation or articles", "Operating or partnership agreement"],
    },
    {
        "rule_id": "KYB-002", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1010.230 (beneficial ownership of legal entity customers); platform policy",
        "field": "ownership_chart", "operator": "equals", "value": False,
        "disposition": Disposition.REFER,
        "reason": "Ownership chart missing",
        "required_documents": ["Ownership chart"],
    },
    {
        "rule_id": "KYB-003", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1010.230(b) (identify the beneficial owners of a legal entity customer)",
        "field": "beneficial_ownership_certification", "operator": "equals", "value": False,
        "disposition": Disposition.REFER,
        "reason": "Beneficial ownership certification missing",
        "required_documents": ["Beneficial ownership certification"],
    },
    {
        "rule_id": "KYB-004", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1010.230(d)(2) (control prong: one individual who manages the entity)",
        "field": "control_person_count", "operator": "equals", "value": 0,
        "disposition": Disposition.REFER,
        "reason": "No control person identified",
        "required_documents": ["Control person identification"],
    },
    {
        "rule_id": "KYB-005", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1010.230(b)(2) (verify the identity of each beneficial owner)",
        "field": "unverified_beneficial_owners", "operator": "is_present",
        "disposition": Disposition.REFER,
        "reason": "Identity not yet verified for a 25% owner or the control person",
        "required_documents": ["ID documents for owners and control person (for verification)"],
    },
    {
        "rule_id": "KYB-006", "version": "1", "effective_date": BASELINE,
        "source": "Platform policy (custodial services agreement between the custodian and the adviser)",
        "field": "custodial_agreement_signed", "operator": "equals", "value": False,
        "disposition": Disposition.REFER,
        "reason": "Custodial services agreement not signed",
        "required_documents": ["Signed custodial services agreement"],
    },
    # --- CIP on the firm -------------------------------------------------
    {
        "rule_id": "CIP-F01", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1023.220(a)(2)(i)(A)(4) (taxpayer identification number)",
        "field": "ein", "operator": "is_blank",
        "disposition": Disposition.REFER,
        "reason": "Employer identification number missing",
        "required_documents": ["EIN"],
    },
    {
        "rule_id": "CIP-F02", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1023.220(a)(2)(i)(A)(3) (principal place of business or other physical location)",
        "field": "principal_address", "operator": "is_blank",
        "disposition": Disposition.REFER,
        "reason": "Principal place of business missing",
        "required_documents": ["Principal business address"],
    },
    {
        "rule_id": "CIP-F03", "version": "1", "effective_date": BASELINE,
        "source": "31 CFR 1023.220(a)(2)(i)(A)(3) (a physical location, not a P.O. box)",
        "field": "address_is_po_box", "operator": "equals", "value": True,
        "disposition": Disposition.REFER,
        "reason": "Principal place of business is a P.O. box; a physical address is required",
        "required_documents": ["Physical business address"],
    },
]


def evaluate_firm(app: FirmApplication, as_of: date) -> Evaluation:
    """Run every firm-level rule in force and collect all findings."""
    return evaluate_all(app, FIRM_RULES, as_of, COMPUTED)


# ---------------------------------------------------------------------------
# Reliance: can the firm's pure RIA accounts use Path A?
# ---------------------------------------------------------------------------

RELIANCE_RELIEF = [
    {
        "rule_id": "RLY-001", "version": "1",
        "effective_date": "2004-02-12", "expiry_date": "2028-01-01",
        "source": "SEC staff no-action position (2004), most recently extended in the letter to SIFMA "
                  "of Dec. 3, 2025 through Jan. 1, 2028, the postponed effective date of FinCEN's "
                  "investment adviser AML rule",
    },
]


def reliance_relief_in_force(as_of: date) -> bool:
    return any(in_force(r, as_of) for r in RELIANCE_RELIEF)


def to_ria_firm(app: FirmApplication, active: bool = True) -> RIAFirm:
    """The Milestone 1 RIAFirm record built from a firm application."""
    return RIAFirm(app.legal_name, active, app.registration == "SEC",
                   app.reliance_contract, app.aml_certification_date)


@dataclass
class RelianceResult:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    source: str = RELIANCE_RELIEF[0]["source"]

    def to_dict(self) -> dict:
        return {"eligible": self.eligible, "reasons": list(self.reasons), "source": self.source}


def firm_reliance(app: FirmApplication, as_of: date) -> RelianceResult:
    """
    Reliance on the RIA for CIP and beneficial ownership needs an SEC-registered
    adviser, a reliance contract, and an annual certification no more than 365
    days old (the Milestone 1 rule), while the SEC staff relief is in force.
    """
    reasons = []
    if not reliance_relief_in_force(as_of):
        reasons.append("SEC staff reliance relief is not in force on this date")
    if app.registration != "SEC":
        reasons.append("Not SEC-registered")
    if not app.reliance_contract:
        reasons.append("No reliance contract")
    if app.aml_certification_date is None:
        reasons.append("No annual AML certification on file")
    else:
        age_days = (as_of - app.aml_certification_date).days
        if age_days < 0:
            reasons.append("Annual AML certification is dated in the future")
        elif age_days > 365:
            reasons.append("Annual AML certification is more than 365 days old")
    eligible = not reasons and reliance_eligible(to_ria_firm(app), as_of)
    return RelianceResult(eligible=eligible, reasons=reasons)
