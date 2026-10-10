"""Tests for firm-level rules and the reliance determination (Milestone 2)."""
from dataclasses import replace
from datetime import date

from applications import FirmApplication, Person
from firm_rules import (
    FIRM_RULES, adv_annual_amendment_overdue, evaluate_firm, firm_reliance,
    reliance_relief_in_force, to_ria_firm,
)
from rules import Disposition, validate_rules

TODAY = date(2026, 10, 10)


def clean_firm(**changes) -> FirmApplication:
    app = FirmApplication(
        legal_name="Granite Peak Advisors LLC", registration="SEC",
        sec_file_number="801-120001", crd_number="310001", iapd_status="approved",
        raum=1_200_000_000, adv_last_annual_amendment=date(2026, 3, 15),
        principal_address="45 W South Temple, Suite 900, Salt Lake City, UT 84101",
        ein="87-1234501", formation_documents=True, ownership_chart=True,
        beneficial_ownership_certification=True, custodial_agreement_signed=True,
        reliance_contract=True, aml_certification_date=date(2026, 3, 1),
        people=[Person("Dana Whitfield", "control_person", 60, identity_verified=True),
                Person("Marcus Lee", "owner", 40, identity_verified=True)],
    )
    return replace(app, **changes)


def ids(app, as_of=TODAY):
    return evaluate_firm(app, as_of).rule_ids


def test_rule_library_is_valid():
    assert validate_rules(FIRM_RULES) == []


def test_every_rule_cites_a_source():
    assert all(r["source"].strip() for r in FIRM_RULES)


def test_clean_firm_clears():
    ev = evaluate_firm(clean_firm(), TODAY)
    assert ev.disposition is Disposition.CLEAR and ev.decisions == [] and ev.warnings == []


def test_unregistered_adviser_is_blocked():
    ev = evaluate_firm(clean_firm(registration="none"), TODAY)
    assert "REG-001" in ev.rule_ids and ev.disposition is Disposition.BLOCK


def test_registration_not_approved_on_iapd_is_referred_to_compliance():
    ev = evaluate_firm(clean_firm(iapd_status="pending"), TODAY)
    assert ev.rule_ids == ["REG-002"] and ev.decisions[0].owner == "compliance"


def test_missing_identifiers_request_items():
    assert ids(clean_firm(sec_file_number=None, crd_number="")) == ["REG-003", "REG-004"]


def test_state_adviser_at_110_million_should_be_sec_registered():
    app = clean_firm(registration="state", sec_file_number=None, raum=110_000_000, reliance_contract=False)
    assert "REG-005" in ids(app)
    assert "REG-005" not in ids(replace(app, raum=109_999_999))


def test_sec_adviser_under_90_million_needs_a_stated_basis():
    assert "REG-006" in ids(clean_firm(raum=80_000_000))
    assert "REG-006" not in ids(clean_firm(raum=80_000_000, sec_registration_basis="multi-state adviser"))
    assert "REG-006" not in ids(clean_firm(raum=95_000_000))


def test_adv_amendment_due_90_days_after_fiscal_year_end():
    late = clean_firm(adv_last_annual_amendment=date(2025, 3, 20))
    assert adv_annual_amendment_overdue(late, date(2026, 3, 31)) is False   # still inside 90 days
    assert adv_annual_amendment_overdue(late, date(2026, 4, 1)) is True     # deadline passed
    assert "REG-007" in ids(late)


def test_adv_amendment_uses_the_firms_fiscal_year():
    june_fye = clean_firm(fiscal_year_end_month=6, adv_last_annual_amendment=date(2025, 9, 1))
    assert adv_annual_amendment_overdue(june_fye, date(2026, 9, 28)) is False  # FYE Jun 30, due Sep 28
    assert adv_annual_amendment_overdue(june_fye, date(2026, 9, 29)) is True


def test_newly_registered_firm_has_no_amendment_due_yet():
    new = clean_firm(adv_last_annual_amendment=None, registration_date=date(2026, 2, 1))
    assert adv_annual_amendment_overdue(new, TODAY) is False


def test_unregistered_iar_is_referred():
    app = clean_firm(people=clean_firm().people + [
        Person("Priya Natarajan", "principal", identity_verified=True,
               iar_registration_required=True, iar_registered=False)])
    assert ids(app) == ["REG-008"]


def test_disciplinary_disclosures_go_to_compliance():
    ev = evaluate_firm(clean_firm(disciplinary_disclosures=True), TODAY)
    assert ev.rule_ids == ["REG-009"] and ev.decisions[0].owner == "compliance"


def test_missing_kyb_documents_come_back_in_one_request():
    app = clean_firm(formation_documents=False, ownership_chart=False,
                     beneficial_ownership_certification=False, custodial_agreement_signed=False)
    ev = evaluate_firm(app, TODAY)
    assert ev.rule_ids == ["KYB-001", "KYB-002", "KYB-003", "KYB-006"]
    assert "Ownership chart" in ev.required_documents and ev.disposition is Disposition.REFER


def test_no_control_person_is_referred():
    app = clean_firm(people=[Person("Dana Whitfield", "owner", 60, identity_verified=True)])
    assert ids(app) == ["KYB-004"]


def test_unverified_25_percent_owner_is_referred_but_smaller_owners_are_not():
    app = clean_firm(people=[Person("Dana Whitfield", "control_person", 60, identity_verified=True),
                             Person("Marcus Lee", "owner", 25, identity_verified=False),
                             Person("Ana Ruiz", "owner", 15, identity_verified=False)])
    assert ids(app) == ["KYB-005"]
    smaller = replace(app, people=[app.people[0], replace(app.people[1], ownership_pct=24.9), app.people[2]])
    assert ids(smaller) == []


def test_unverified_control_person_is_referred_even_without_ownership():
    app = clean_firm(people=[Person("Marcus Lee", "owner", 100, identity_verified=True),
                             Person("Jo Park", "control_person", 0, identity_verified=False)])
    assert ids(app) == ["KYB-005"]


def test_firm_cip_needs_ein_and_physical_address():
    assert ids(clean_firm(ein=None)) == ["CIP-F01"]
    assert ids(clean_firm(principal_address=None)) == ["CIP-F02"]
    assert ids(clean_firm(principal_address="P.O. Box 1180, Park City, UT 84060")) == ["CIP-F03"]


def test_reliance_for_sec_firm_with_contract_and_current_certification():
    result = firm_reliance(clean_firm(), TODAY)
    assert result.eligible and result.reasons == []


def test_reliance_reasons_explain_each_gap():
    result = firm_reliance(clean_firm(registration="state", reliance_contract=False,
                                      aml_certification_date=None), TODAY)
    assert not result.eligible
    assert result.reasons == ["Not SEC-registered", "No reliance contract", "No annual AML certification on file"]


def test_stale_and_future_certifications_are_explained():
    assert firm_reliance(clean_firm(aml_certification_date=date(2025, 10, 9)), TODAY).reasons == [
        "Annual AML certification is more than 365 days old"]
    assert firm_reliance(clean_firm(aml_certification_date=date(2026, 12, 1)), TODAY).reasons == [
        "Annual AML certification is dated in the future"]


def test_reliance_ends_when_the_sec_staff_relief_expires():
    assert reliance_relief_in_force(date(2027, 12, 31))
    assert not reliance_relief_in_force(date(2028, 1, 1))
    app = clean_firm(aml_certification_date=date(2027, 6, 1))
    assert firm_reliance(app, date(2027, 12, 31)).eligible
    late = firm_reliance(app, date(2028, 1, 1))
    assert not late.eligible and late.reasons == ["SEC staff reliance relief is not in force on this date"]


def test_firm_application_maps_to_the_milestone_1_record():
    firm = to_ria_firm(clean_firm())
    assert (firm.name, firm.active, firm.sec_registered, firm.reliance_contract) == (
        "Granite Peak Advisors LLC", True, True, True)
    assert firm.certification_date == date(2026, 3, 1)
