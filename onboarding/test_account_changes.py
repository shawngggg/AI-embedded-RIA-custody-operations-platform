"""Tests for account changes along map 04 (MVP)."""
from dataclasses import replace
from datetime import date

from account_changes import CHANGE_RULES, ChangeRequest, ChangeType, fraud_pattern, process_change
from applications import Person
from models import AccountGroup
from pipeline import Decisions
from rules import validate_rules
from sample_data import FLAGGED_LIST

TODAY = date(2026, 10, 10)


def req(change=ChangeType.ADDRESS, group=AccountGroup.PURE_RIA, **kw):
    base = dict(request_id="CHG-1", account_id="ACC-2001", account_group=group, account_firm="310001",
                change_type=change, submitted_by_role="ria_user", submitted_by_firm="310001",
                details={"new_address": "77 Canyon Rd, Park City, UT 84060"})
    if group is AccountGroup.SELF_DIRECTED:
        base.update(submitted_by_role="client", submitted_by_firm=None, submitted_by_client_of="ACC-2001")
    base.update(kw)
    return ChangeRequest(**base)


def test_rule_library_is_valid():
    assert validate_rules(CHANGE_RULES) == []


def test_ria_user_changes_a_pure_ria_account():
    res = process_change(req(), TODAY, flagged=FLAGGED_LIST)
    assert res.status == "APPLIED"
    notices = next(s for s in res.steps if s.name == "Send confirmation notices").detail
    assert notices == ["The client at the old address", "The client at the new address", "The RIA"]


def test_ria_user_from_another_firm_is_redirected():
    assert process_change(req(submitted_by_firm="310002"), TODAY).status == "REDIRECTED"


def test_client_cannot_change_a_pure_ria_account():
    r = req(submitted_by_role="client", submitted_by_firm=None, submitted_by_client_of="ACC-2001")
    assert process_change(r, TODAY).status == "REDIRECTED"


def test_client_changes_their_own_self_directed_account():
    res = process_change(req(group=AccountGroup.SELF_DIRECTED), TODAY)
    assert res.status == "APPLIED"


def test_ria_cannot_change_a_self_directed_account():
    r = req(group=AccountGroup.SELF_DIRECTED, submitted_by_role="ria_user", submitted_by_firm="310001",
            submitted_by_client_of=None)
    assert process_change(r, TODAY).status == "REDIRECTED"


def test_client_cannot_change_someone_elses_account():
    r = req(group=AccountGroup.SELF_DIRECTED, submitted_by_client_of="ACC-9999")
    assert process_change(r, TODAY).status == "REDIRECTED"


def test_owner_level_change_needs_the_client_signature():
    res = process_change(req(ChangeType.BENEFICIARY, details={"beneficiary": "Lena Chen, 100%"}), TODAY)
    assert res.status == "RETURNED" and res.requested_items == ["Client-signed change form"]
    signed = process_change(req(ChangeType.BENEFICIARY, client_signature=True), TODAY)
    assert signed.status == "APPLIED"


def test_registration_change_needs_a_medallion_guarantee():
    r = req(ChangeType.REGISTRATION, client_signature=True, details={"registration": "Joint tenants"})
    assert process_change(r, TODAY).requested_items == ["Medallion signature guarantee"]


def test_bank_instruction_must_be_complete_and_starts_inactive():
    assert process_change(req(ChangeType.BANK_INSTRUCTION, new_bank={"name": "First Utah Bank"}), TODAY).status == "RETURNED"
    res = process_change(req(ChangeType.BANK_INSTRUCTION, details={},
                             new_bank={"name": "First Utah Bank", "aba": "124000012", "account_last4": "4417"}),
                         TODAY, flagged=FLAGGED_LIST)
    assert res.status == "APPLIED"
    assert "inactive until it is verified" in " ".join(next(s for s in res.steps if s.name.startswith("Apply")).detail)


def test_address_and_bank_changes_close_together_need_confirmation():
    r = req(ChangeType.BANK_INSTRUCTION, recent_changes=[(ChangeType.ADDRESS, date(2026, 10, 1))],
            new_bank={"name": "First Utah Bank", "aba": "124000012", "account_last4": "4417"})
    assert fraud_pattern(r, TODAY) == ["Address changed on 2026-10-01"]
    assert process_change(r, TODAY).status == "PENDING_CONFIRMATION"
    assert process_change(r, TODAY, confirmation="rejected").status == "REJECTED"
    assert process_change(r, TODAY, confirmation="confirmed").status == "APPLIED"


def test_older_changes_do_not_trigger_the_pattern():
    r = req(ChangeType.BANK_INSTRUCTION, recent_changes=[(ChangeType.ADDRESS, date(2026, 9, 1))],
            new_bank={"name": "First Utah Bank", "aba": "124000012", "account_last4": "4417"})
    assert fraud_pattern(r, TODAY) == []


def test_flagged_bank_routing_escalates_to_the_sanctions_team():
    r = req(ChangeType.BANK_INSTRUCTION,
            new_bank={"name": "BAM Panama", "aba": "000000000", "account_last4": "0001", "bic": "BAMRPAPAXXX"})
    res = process_change(r, TODAY, flagged=FLAGGED_LIST)
    assert res.status == "PENDING_SANCTIONS" and [c["code"] for c in res.restrictions] == ["SANC"]
    cleared = process_change(r, TODAY, flagged=FLAGGED_LIST, decisions=Decisions(sanctions="false_positive"))
    assert cleared.status == "APPLIED"


def test_new_beneficial_owner_needs_verification_on_path_b():
    owner = Person("Ana Ruiz", "owner", 30, date_of_birth="1981-02-03", address="9 Elm St, Ogden, UT")
    r = req(ChangeType.BENEFICIAL_OWNER, client_signature=True, new_parties=[owner], path="B")
    assert process_change(r, TODAY, flagged=FLAGGED_LIST).status == "PENDING_VERIFICATION"
    verified = replace(r, new_parties=[replace(owner, identity_verified=True)])
    assert process_change(verified, TODAY, flagged=FLAGGED_LIST).status == "APPLIED"


def test_path_a_relies_on_the_ria_for_new_owner_cip():
    owner = Person("Ana Ruiz", "owner", 30)
    r = req(ChangeType.BENEFICIAL_OWNER, client_signature=True, new_parties=[owner], path="A")
    res = process_change(r, TODAY, flagged=FLAGGED_LIST)
    assert res.status == "APPLIED"
    assert next(s for s in res.steps if s.name.startswith("Re-run")).outcome == "Relied on the RIA (Path A)"


def test_ops_can_key_a_written_request():
    assert process_change(req(submitted_by_role="ops_analyst", submitted_by_firm=None), TODAY).status == "APPLIED"
