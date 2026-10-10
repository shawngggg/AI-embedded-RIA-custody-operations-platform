"""End-to-end tests: firm onboarding and account opening along maps 01 and 02."""
import json
from dataclasses import replace
from datetime import date

import pytest

from applications import Position
from demo import PACKAGE_KEY, catalog, run_account, run_firm, run_json
from firm_rules import to_ria_firm
from models import Account, AccountGroup
from pipeline import AI, AML, LEDGER, OPS, RULES, SANCTIONS, Decisions, onboard_firm, open_account, write_audit_log
from sample_data import ACCOUNTS, FIRMS, FLAGGED_LIST

TODAY = date(2026, 10, 10)
LANES = {OPS, AI, RULES, AML, SANCTIONS, LEDGER}


def step(res, name_start):
    return next(s for s in res.steps if s.name.startswith(name_start))


@pytest.mark.parametrize("key,status", [
    (PACKAGE_KEY, "PENDING_ITEMS"), ("granite_peak", "ACTIVE"), ("juniper_ridge", "ACTIVE"),
    ("bonneville", "ACTIVE"), ("alta_vista", "PENDING_ITEMS"), ("cottonwood", "DECLINED"),
    ("meridian_gate", "PENDING_SANCTIONS"), ("lakeshore", "PENDING_EDD"),
])
def test_each_sample_firm_reaches_the_expected_state(key, status):
    res = run_firm(key)
    assert res.status == status
    assert {s.lane for s in res.steps} <= LANES


def test_active_firm_gets_its_milestone_1_record_and_reliance():
    res = run_firm("granite_peak")
    assert res.record.active and res.record.sec_registered and res.reliance.eligible
    assert step(res, "Open master").outcome == "Opened"


def test_firm_without_current_certification_is_active_but_not_reliance_eligible():
    res = run_firm("juniper_ridge")
    assert res.status == "ACTIVE" and not res.reliance.eligible


def test_package_read_by_ai_comes_back_with_one_missing_items_request():
    res = run_firm(PACKAGE_KEY)
    extract = step(res, "Extract firm documents")
    assert "1 rejected" in extract.outcome and any("ignored" in d for d in extract.detail)
    assert res.requested_items == ["Beneficial ownership certification",
                                   "ID documents for owners and control person (for verification)"]
    assert "REG-002" not in res.findings.rule_ids     # IAPD status came from the registration lookup


def test_registration_findings_wait_for_compliance_sign_off():
    app = replace(FIRMS["granite_peak"], disciplinary_disclosures=True)
    waiting = onboard_firm(app, TODAY, flagged=FLAGGED_LIST)
    assert waiting.status == "PENDING_REVIEW" and step(waiting, "Verify registration").rule_ids == ["REG-009"]
    signed = onboard_firm(app, TODAY, flagged=FLAGGED_LIST, decisions=Decisions(compliance_signoff=True))
    assert signed.status == "ACTIVE" and "signed off" in step(signed, "Verify registration").outcome
    assert "RF-DIS-1" in [f["factor_id"] for f in signed.risk.factors]


def test_sanctions_match_holds_the_firm_until_the_sanctions_team_decides():
    res = run_firm("meridian_gate")
    assert [r["code"] for r in res.restrictions] == ["SANC"]
    assert {h["match_type"] for h in res.screening} == {"name", "ownership_50"}


def test_true_match_declines_the_firm_and_blocks():
    res = run_firm("meridian_gate", sanctions="true_match", sanctions_reference="DET-1")
    assert res.status == "DECLINED" and [r["code"] for r in res.restrictions] == ["OFAC"]
    assert step(res, "Determination").lane == SANCTIONS


def test_false_positive_lets_onboarding_continue():
    res = run_firm("meridian_gate", sanctions="false_positive", sanctions_reference="DET-2")
    assert res.status == "ACTIVE" and res.restrictions == []
    events = res.to_dict()["restriction_events"]
    assert events[-1]["event"] == "removed" and events[-1]["function"] == "sanctions"


def test_high_risk_firm_waits_for_edd_and_aml_compliance_removes_the_hold():
    assert [r["code"] for r in run_firm("lakeshore").restrictions] == ["EDDP"]
    approved = run_firm("lakeshore", edd="approved", edd_reference="EDD-9")
    assert approved.status == "ACTIVE" and approved.restrictions == []
    assert approved.to_dict()["restriction_events"][-1]["function"] == "aml_compliance"
    assert run_firm("lakeshore", edd="declined").status == "DECLINED"


@pytest.mark.parametrize("key,status,path", [
    ("maria_chen", "OPEN", "A"), ("kenji_tanaka", "OPEN", "B"), ("farid_hosseini_sd", "OPEN", "B"),
    ("whitfield_holdings", "OPEN", "B"), ("ilya_sorvetkin", "PENDING_SANCTIONS", "A"),
    ("elena_marsh_pep", "PENDING_EDD", "A"), ("orphan_self_directed", "RETURNED", None),
])
def test_each_sample_account_reaches_the_expected_state(key, status, path):
    res = run_account(key)
    assert (res.status, res.path) == (status, path)
    assert {s.lane for s in res.steps} <= LANES


def test_path_a_account_is_rated_on_ria_provided_information_and_funded_by_transfer():
    res = run_account("maria_chen")
    assert res.risk.information_basis.startswith("RIA-provided")
    assert res.transfer.release == "partial" and step(res, "Transferability").outcome == "Release: partial"


def test_self_directed_account_without_an_ria_managed_account_is_returned():
    res = run_account("orphan_self_directed")
    assert step(res, "Application returned").detail == ["Self-directed account needs an open RIA-managed account"]


def test_inactive_ria_cannot_open_accounts():
    res = open_account(ACCOUNTS["maria_chen"], FIRMS["granite_peak"], TODAY, ria_active=False)
    assert res.status == "RETURNED" and step(res, "Gate").detail == ["RIA not active"]


def test_beneficial_ownership_rule_follows_the_february_2026_relief():
    assert run_account("whitfield_holdings", date(2026, 2, 12)).requested_items == [
        "Beneficial ownership certification"]
    later = run_account("whitfield_holdings", TODAY)
    assert later.status == "OPEN" and any("exceptive relief" in d for d in step(later, "Path B: CDD").detail)


def test_unverified_identity_on_path_b_opens_with_a_cipv_restriction():
    app = ACCOUNTS["kenji_tanaka"]
    app = replace(app, holders=[replace(app.holders[0], identity_verified=False)])
    res = open_account(app, FIRMS["bonneville"], TODAY, flagged=FLAGGED_LIST)
    assert res.status == "OPEN" and [r["code"] for r in res.restrictions] == ["CIPV"]


def test_path_a_falls_back_to_path_b_when_the_reliance_relief_expires():
    firm = replace(FIRMS["granite_peak"], aml_certification_date=date(2027, 6, 1))
    assert open_account(ACCOUNTS["maria_chen"], firm, date(2027, 12, 31)).path == "A"
    later = open_account(ACCOUNTS["maria_chen"], firm, date(2028, 1, 1))
    assert later.path == "B" and step(later, "Path A applies?").detail == [
        "SEC staff reliance relief is not in force on this date"]


def test_flagged_product_in_the_funding_assets_is_escalated():
    app = replace(ACCOUNTS["maria_chen"], funding_positions=[
        Position("SYN-NWF-B", "Northwind Frontier Fund Class B (synthetic)", "alternative", 1000)])
    res = open_account(app, FIRMS["granite_peak"], TODAY, flagged=FLAGGED_LIST)
    assert res.status == "PENDING_SANCTIONS" and res.screening[0]["match_type"] == "identifier"


def test_cleared_and_approved_accounts_open():
    assert run_account("ilya_sorvetkin", sanctions="false_positive").status == "OPEN"
    pep = run_account("elena_marsh_pep", edd="approved")
    assert pep.status == "OPEN" and pep.risk.review_months == 6


def test_second_self_directed_account_is_returned():
    firm = FIRMS["granite_peak"]
    ria = to_ria_firm(firm)
    existing = [Account("A1", ria, AccountGroup.PURE_RIA), Account("A2", ria, AccountGroup.SELF_DIRECTED)]
    res = open_account(ACCOUNTS["farid_hosseini_sd"], firm, TODAY, existing=existing)
    assert step(res, "Gate").detail == ["Client already has a self-directed account"]


def test_audit_record_is_json_and_can_be_appended_to_a_log(tmp_path):
    res = run_account("maria_chen")
    log = tmp_path / "audit_log.jsonl"
    write_audit_log(res, str(log))
    write_audit_log(run_firm("granite_peak"), str(log))
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["status"] == "OPEN"
    assert json.loads(lines[0])["evaluation_date"] == "2026-10-10"


def test_browser_entry_point_returns_json():
    data = json.loads(run_json("account", "ilya_sorvetkin", "2026-10-10", '{"sanctions": "false_positive"}'))
    assert data["status"] == "OPEN" and data["escalation"]["status"] == "released"
    data = json.loads(run_json("firm", "lakeshore", "2026-10-10", '{"edd": ""}'))
    assert data["status"] == "PENDING_EDD"


def test_catalog_lists_every_scenario():
    c = catalog()
    assert len(c["firms"]) == len(FIRMS) + 1 and len(c["accounts"]) == len(ACCOUNTS)
    assert "Red Butte" in c["package_text"]
