"""Tests for risk rating and EDD triggers (Milestone 5)."""
from dataclasses import replace
from datetime import date

from applications import Person
from models import AccountGroup
from risk import (
    ACCOUNT_FACTORS, FIRM_FACTORS, add_months, canonical_country, fatf_lists, rate_account, rate_firm,
)
from sample_data import ACCOUNTS, FIRMS

TODAY = date(2026, 10, 10)


def ids(result):
    return [f["factor_id"] for f in result.factors]


def account(key="maria_chen", **changes):
    return replace(ACCOUNTS[key], **changes)


def holder(app, **changes):
    return replace(app, holders=[replace(app.holders[0], **changes)] + app.holders[1:])


def test_every_factor_has_points_a_source_and_a_unique_id():
    for library in (ACCOUNT_FACTORS, FIRM_FACTORS):
        assert all(f["points"] > 0 and f["source"] for f in library)
        assert len({f["rule_id"] for f in library}) == len(library)


def test_fatf_lists_follow_the_june_2026_statement():
    lists = fatf_lists(TODAY)
    assert lists["call_for_action"] == ["North Korea", "Iran"] and lists["enhanced_due_diligence"] == ["Myanmar"]
    assert "Iraq" in lists["increased_monitoring"] and "Namibia" not in lists["increased_monitoring"]
    assert fatf_lists(date(2026, 6, 18))["call_for_action"] == []


def test_country_names_are_matched_through_common_aliases():
    assert canonical_country("DPRK") == canonical_country("North Korea")
    assert canonical_country("British Virgin Islands") == canonical_country("Virgin Islands (UK)")
    assert canonical_country("Burma") == canonical_country("Myanmar")


def test_plain_domestic_account_is_low_risk_and_reviewed_annually():
    r = rate_account(account(), TODAY, path="A")
    assert (r.tier, r.score, r.edd_required) == ("LOW", 0, False)
    assert r.next_review == date(2027, 10, 10) and r.information_basis.startswith("RIA-provided")


def test_points_add_up_to_medium():
    app = holder(account(third_party_funding=True), country_of_residence="Japan")
    r = rate_account(app, TODAY)
    assert ids(r) == ["RA-NRA-1", "RA-3PF-1"] and (r.score, r.tier) == (30, "MEDIUM")
    assert r.edd_requirements == []


def test_points_alone_can_reach_high():
    app = holder(account(third_party_funding=True, source_of_wealth=""), country_of_residence="Japan")
    r = rate_account(app, TODAY)
    assert r.score == 60 and r.tier == "HIGH" and r.edd_required


def test_foreign_pep_requires_edd_and_senior_management_approval():
    r = rate_account(ACCOUNTS["elena_marsh_pep"], TODAY)
    assert "RA-PEP-1" in ids(r) and "RA-JUR-3" in ids(r) and r.tier == "HIGH"
    assert r.edd_requirements[-1] == "Senior management approval (foreign PEP)"
    assert r.review_months == 6 and r.next_review == date(2027, 4, 10)


def test_domestic_pep_adds_points_without_mandatory_edd():
    r = rate_account(holder(account(), is_pep=True, pep_type="domestic"), TODAY)
    assert ids(r) == ["RA-PEP-2"] and r.tier == "MEDIUM"


def test_mandatory_factors_force_high_whatever_the_score():
    for app in (holder(account(), adverse_media=True),
                holder(account(), nationality="Myanmar"),
                account("whitfield_holdings", entity_type="shell"),
                account("whitfield_holdings", bearer_shares=True)):
        assert rate_account(app, TODAY).tier == "HIGH"


def test_call_for_action_jurisdiction_adds_a_sanctions_review():
    r = rate_account(holder(account(), nationality="Iran"), TODAY)
    assert "RA-JUR-1" in ids(r)
    assert "Sanctions team review of the jurisdiction exposure" in r.edd_requirements


def test_any_holder_counts_not_just_the_first():
    app = account("whitfield_holdings")
    app = replace(app, holders=app.holders + [Person("Lin Ortega", "owner", 0, nationality="Venezuela")])
    assert "RA-JUR-3" in ids(rate_account(app, TODAY))


def test_high_risk_ria_raises_its_accounts_rating():
    assert "RA-RIA-1" in ids(rate_account(account(), TODAY, ria_risk_tier="HIGH"))


def test_large_funding_adds_points():
    assert "RA-SIZE-1" in ids(rate_account(account(expected_initial_funding=5_000_000), TODAY))


def test_clean_firm_is_low_risk():
    r = rate_firm(FIRMS["granite_peak"], TODAY)
    assert (r.tier, r.score) == ("LOW", 0)


def test_firm_with_foreign_pep_and_offshore_layer_needs_edd():
    r = rate_firm(FIRMS["lakeshore"], TODAY)
    assert r.tier == "HIGH" and r.edd_required
    assert {"RF-PEP-1", "RF-JUR-3", "RF-OWN-1", "RF-OWN-2", "RF-CLI-1"} <= set(ids(r))
    assert r.edd_requirements[:2] == ["Owners' source of wealth", "Source of the firm's funds"]


def test_new_firm_with_disciplinary_history_is_medium():
    app = replace(FIRMS["granite_peak"], disciplinary_disclosures=True, registration_date=date(2025, 6, 1))
    r = rate_firm(app, TODAY)
    assert ids(r) == ["RF-DIS-1", "RF-NEW-1"] and r.tier == "MEDIUM"


def test_add_months_handles_month_ends():
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2027, 8, 31), 6) == date(2028, 2, 29)


def test_self_directed_account_is_rated_like_any_other():
    r = rate_account(ACCOUNTS["farid_hosseini_sd"], TODAY)
    assert ACCOUNTS["farid_hosseini_sd"].group is AccountGroup.SELF_DIRECTED and r.tier == "LOW"
