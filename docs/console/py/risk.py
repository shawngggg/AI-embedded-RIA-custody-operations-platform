"""
Risk rating and EDD triggers (Milestone 5).

Rates RIA firms and client accounts from cited, effective-dated risk factors,
decides when enhanced due diligence is required, lists what EDD has to
collect, and sets the next periodic review date.

How the rating works (a policy choice, labeled as reference design):
  - each factor that applies adds points; 25 or more is MEDIUM, 50 or more is HIGH
  - some factors require EDD on their own (foreign PEP, adverse media, a FATF
    jurisdiction that calls for EDD or countermeasures, bearer shares, shell or
    private investment company structures); they make the rating HIGH
    whatever the score
  - HIGH means EDD: source of funds, source of wealth, and AML compliance
    approval, plus senior management approval for a foreign PEP
  - review cadence: HIGH every 6 months, otherwise every 12 (rolling review
    map, 03, demo policy)

On Path A the account is rated on RIA-provided information only (client
account opening map, 02).

FATF lists change after each plenary (February, June, October). A new list
is a new version with the plenary date, so earlier ratings stay reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from applications import AccountApplication, FirmApplication
from flagged_list import normalize
from rules import active_rules, matches

BASELINE = "2026-01-01"
MEDIUM_AT, HIGH_AT = 25, 50
REVIEW_MONTHS = {"HIGH": 6, "MEDIUM": 12, "LOW": 12}

# ---------------------------------------------------------------------------
# FATF lists, effective-dated
# ---------------------------------------------------------------------------

FATF_LISTS = [
    {
        "rule_id": "FATF", "version": "1", "effective_date": "2026-06-19",
        "source": "FATF public statements of 19 June 2026: High-Risk Jurisdictions subject to a Call "
                  "for Action; Jurisdictions under Increased Monitoring",
        "call_for_action": ["North Korea", "Iran"],
        "enhanced_due_diligence": ["Myanmar"],
        "increased_monitoring": [
            "Angola", "Bolivia", "Bosnia and Herzegovina", "Bulgaria", "Cameroon", "Cote d'Ivoire",
            "Democratic Republic of the Congo", "Haiti", "Iraq", "Kenya", "Kuwait", "Laos", "Lebanon",
            "Monaco", "Nepal", "Papua New Guinea", "South Sudan", "Syria", "Venezuela", "Vietnam",
            "Virgin Islands (UK)", "Yemen",
        ],
    },
]

COUNTRY_ALIASES = {
    "dprk": "north korea",
    "democratic people s republic of korea": "north korea",
    "lao pdr": "laos",
    "lao people s democratic republic": "laos",
    "british virgin islands": "virgin islands uk",
    "bvi": "virgin islands uk",
    "ivory coast": "cote d ivoire",
    "drc": "democratic republic of the congo",
    "burma": "myanmar",
}


def canonical_country(name: str | None) -> str:
    n = normalize(name or "")
    return COUNTRY_ALIASES.get(n, n)


def fatf_lists(as_of: date) -> dict:
    current = active_rules(FATF_LISTS, as_of)
    if not current:
        return {"call_for_action": [], "enhanced_due_diligence": [], "increased_monitoring": [], "source": ""}
    return current[0]


def _on_list(countries: set[str], as_of: date, key: str) -> list[str]:
    listed = {canonical_country(c): c for c in fatf_lists(as_of)[key]}
    return sorted(listed[c] for c in countries if c in listed)


# ---------------------------------------------------------------------------
# Risk inputs: the facts a rating reads, with the jurisdictions resolved
# ---------------------------------------------------------------------------

@dataclass
class AccountRiskInput:
    application: AccountApplication
    ria_risk_tier: str = "LOW"
    information_basis: str = "custodian-verified"   # RIA-provided on Path A

    def countries(self) -> set[str]:
        found = set()
        for p in self.application.holders:
            found.add(canonical_country(p.country_of_residence))
            found.add(canonical_country(p.nationality))
        return {c for c in found if c}


@dataclass
class FirmRiskInput:
    application: FirmApplication

    def countries(self) -> set[str]:
        found = {canonical_country(j) for j in self.application.jurisdictions}
        for p in self.application.people:
            found.add(canonical_country(p.country_of_residence))
            found.add(canonical_country(p.nationality))
        return {c for c in found if c}


def _account_holders(subject):
    return subject.application.holders if isinstance(subject, AccountRiskInput) else subject.application.people


COMPUTED = {
    "fatf_call_for_action": lambda s, d: _on_list(s.countries(), d, "call_for_action"),
    "fatf_edd": lambda s, d: _on_list(s.countries(), d, "enhanced_due_diligence"),
    "fatf_monitoring": lambda s, d: _on_list(s.countries(), d, "increased_monitoring"),
    "foreign_pep": lambda s, d: any(p.is_pep and p.pep_type == "foreign" for p in _account_holders(s)),
    "other_pep": lambda s, d: any(p.is_pep and p.pep_type != "foreign" for p in _account_holders(s)),
    "adverse_media": lambda s, d: any(p.adverse_media for p in _account_holders(s))
    or bool(getattr(s.application, "adverse_media", False)),
    "non_us_resident": lambda s, d: bool(s.application.primary)
    and canonical_country(s.application.primary.country_of_residence) != "united states",
    "sof_or_sow_missing": lambda s, d: not (s.application.source_of_funds or "").strip()
    or not (s.application.source_of_wealth or "").strip(),
    "foreign_25pct_owner": lambda s, d: any(
        p.ownership_pct >= 25 and canonical_country(p.country_of_residence) != "united states"
        for p in s.application.people),
    "years_registered": lambda s, d: (
        (d - s.application.registration_date).days / 365.25 if s.application.registration_date else 99),
}

PEP_SOURCE = ("FATF Recommendation 12; interagency statement on due diligence for politically exposed "
              "persons (Aug. 21, 2020)")
JUR_SOURCE = "FATF public statements of 19 June 2026"
CDD_SOURCE = "31 CFR 1023.210(b)(5) (risk-based customer due diligence); FINRA Rule 3310"


def _factor(fid, description, points, mandatory, source, **condition):
    rule = {"rule_id": fid, "version": "1", "effective_date": BASELINE, "description": description,
            "points": points, "mandatory_edd": mandatory, "source": source}
    rule.update(condition)
    return rule


ACCOUNT_FACTORS = [
    _factor("RA-JUR-1", "Tied to a FATF call-for-action jurisdiction", 100, True, JUR_SOURCE,
            field="fatf_call_for_action", operator="is_present"),
    _factor("RA-JUR-2", "Tied to a jurisdiction where FATF calls for enhanced due diligence", 40, True,
            JUR_SOURCE, field="fatf_edd", operator="is_present"),
    _factor("RA-JUR-3", "Tied to a jurisdiction under FATF increased monitoring", 25, False, JUR_SOURCE,
            field="fatf_monitoring", operator="is_present"),
    _factor("RA-PEP-1", "Foreign politically exposed person", 50, True, PEP_SOURCE,
            field="foreign_pep", operator="equals", value=True),
    _factor("RA-PEP-2", "Domestic or international-organization PEP", 25, False, PEP_SOURCE,
            field="other_pep", operator="equals", value=True),
    _factor("RA-MED-1", "Adverse media on a holder or owner", 40, True, CDD_SOURCE,
            field="adverse_media", operator="equals", value=True),
    _factor("RA-ENT-1", "Private investment company or shell structure", 40, True, CDD_SOURCE,
            field="application.entity_type", operator="in", value=["private_investment_company", "shell"]),
    _factor("RA-ENT-2", "Entity with bearer shares", 50, True, CDD_SOURCE,
            field="application.bearer_shares", operator="equals", value=True),
    _factor("RA-NRA-1", "Resides outside the United States", 15, False, CDD_SOURCE,
            field="non_us_resident", operator="equals", value=True),
    _factor("RA-SOF-1", "Source of funds or source of wealth not stated", 30, False, CDD_SOURCE,
            field="sof_or_sow_missing", operator="equals", value=True),
    _factor("RA-3PF-1", "Third-party funding expected", 15, False, CDD_SOURCE,
            field="application.third_party_funding", operator="equals", value=True),
    _factor("RA-SIZE-1", "Expected initial funding of $5 million or more", 10, False, CDD_SOURCE,
            field="application.expected_initial_funding", operator="greater_than_or_equal", value=5_000_000),
    _factor("RA-RIA-1", "The account's RIA is rated high risk", 15, False, CDD_SOURCE,
            field="ria_risk_tier", operator="equals", value="HIGH"),
]

FIRM_FACTORS = [
    _factor("RF-JUR-1", "Firm or owners tied to a FATF call-for-action jurisdiction", 100, True, JUR_SOURCE,
            field="fatf_call_for_action", operator="is_present"),
    _factor("RF-JUR-2", "Firm or owners tied to a jurisdiction where FATF calls for EDD", 40, True,
            JUR_SOURCE, field="fatf_edd", operator="is_present"),
    _factor("RF-JUR-3", "Firm or owners tied to a jurisdiction under FATF increased monitoring", 25, False,
            JUR_SOURCE, field="fatf_monitoring", operator="is_present"),
    _factor("RF-PEP-1", "Foreign PEP among owners or principals", 50, True, PEP_SOURCE,
            field="foreign_pep", operator="equals", value=True),
    _factor("RF-PEP-2", "Domestic PEP among owners or principals", 25, False, PEP_SOURCE,
            field="other_pep", operator="equals", value=True),
    _factor("RF-MED-1", "Adverse media on the firm, its owners, or principals", 40, True, CDD_SOURCE,
            field="adverse_media", operator="equals", value=True),
    _factor("RF-DIS-1", "Disciplinary disclosures", 25, False,
            "Form ADV Part 1A Item 11; platform policy",
            field="application.disciplinary_disclosures", operator="equals", value=True),
    _factor("RF-OWN-1", "Ownership through three or more layers", 20, False, CDD_SOURCE,
            field="application.ownership_layers", operator="greater_than_or_equal", value=3),
    _factor("RF-OWN-2", "A 25% owner resides outside the United States", 15, False, CDD_SOURCE,
            field="foreign_25pct_owner", operator="equals", value=True),
    _factor("RF-CLI-1", "A quarter or more of the firm's clients are outside the United States", 20, False,
            CDD_SOURCE, field="application.foreign_client_pct", operator="greater_than_or_equal", value=25),
    _factor("RF-NEW-1", "Registered less than two years ago", 10, False, "Platform policy",
            field="years_registered", operator="less_than", value=2),
]

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class RiskResult:
    tier: str
    score: int
    factors: list[dict]
    edd_required: bool
    edd_requirements: list[str]
    review_months: int
    next_review: date
    information_basis: str = "custodian-verified"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"tier": self.tier, "score": self.score, "factors": list(self.factors),
                "edd_required": self.edd_required, "edd_requirements": list(self.edd_requirements),
                "review_months": self.review_months, "next_review": self.next_review.isoformat(),
                "information_basis": self.information_basis, "warnings": list(self.warnings)}


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year, month = d.year + month // 12, month % 12 + 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, days_in_month))


def _rate(subject, factors: list[dict], as_of: date, edd_items: list[str],
          basis: str) -> RiskResult:
    warnings: list[str] = []
    applied = []
    for factor in active_rules(factors, as_of):
        if matches(subject, factor, as_of, COMPUTED, warnings):
            applied.append({"factor_id": factor["rule_id"], "description": factor["description"],
                            "points": factor["points"], "mandatory_edd": factor["mandatory_edd"],
                            "source": factor["source"]})
    score = sum(f["points"] for f in applied)
    mandatory = any(f["mandatory_edd"] for f in applied)
    tier = "HIGH" if mandatory or score >= HIGH_AT else "MEDIUM" if score >= MEDIUM_AT else "LOW"
    edd = tier == "HIGH"
    requirements = list(edd_items) if edd else []
    if edd and any(f["factor_id"].endswith("PEP-1") for f in applied):
        requirements.append("Senior management approval (foreign PEP)")
    if edd and any(f["factor_id"].endswith("JUR-1") for f in applied):
        requirements.append("Sanctions team review of the jurisdiction exposure")
    months = REVIEW_MONTHS[tier]
    return RiskResult(tier, score, applied, edd, requirements, months, add_months(as_of, months),
                      basis, warnings)


ACCOUNT_EDD = ["Source of funds documentation", "Source of wealth narrative with corroboration",
               "AML compliance approval"]
FIRM_EDD = ["Owners' source of wealth", "Source of the firm's funds", "Enhanced review and AML compliance approval"]


def rate_account(app: AccountApplication, as_of: date, ria_risk_tier: str = "LOW",
                 path: str = "B") -> RiskResult:
    basis = "RIA-provided information only, Path A" if path == "A" else "custodian-verified information, Path B"
    subject = AccountRiskInput(app, ria_risk_tier, basis)
    return _rate(subject, ACCOUNT_FACTORS, as_of, ACCOUNT_EDD, basis)


def rate_firm(app: FirmApplication, as_of: date) -> RiskResult:
    return _rate(FirmRiskInput(app), FIRM_FACTORS, as_of, FIRM_EDD, "custodian-verified")
