"""Tests for restriction codes (Milestone 3)."""
from datetime import datetime, timezone

import pytest

from restrictions import CATALOG, Activity, Function, RestrictionError, RestrictionRegistry

AT = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


def test_every_code_names_an_owner_blocks_something_and_cites_a_source():
    for code in CATALOG.values():
        assert isinstance(code.owner, Function) and code.blocks and code.source


def test_first_line_can_place_a_sanctions_hold_but_the_sanctions_team_owns_it():
    reg = RestrictionRegistry()
    r = reg.place("ACCT-1", "SANC", "Flagged-list match on owner", "ops.analyst", Function.OPERATIONS, AT)
    assert r.owner is Function.SANCTIONS and reg.active_codes("ACCT-1") == ["SANC"]


def test_only_the_owning_function_can_remove_a_code():
    reg = RestrictionRegistry()
    r = reg.place("ACCT-1", "SANC", "Flagged-list match", "ops.analyst", Function.OPERATIONS, AT)
    with pytest.raises(RestrictionError, match="Only sanctions can remove SANC"):
        reg.remove(r.restriction_id, "ops.analyst", Function.OPERATIONS, "Looks like a false positive", "N/A", AT)
    assert reg.active_codes("ACCT-1") == ["SANC"]


def test_refused_removals_are_logged():
    reg = RestrictionRegistry()
    r = reg.place("ACCT-1", "FRAUD", "Address and bank changed together", "ops.analyst", Function.OPERATIONS, AT)
    with pytest.raises(RestrictionError):
        reg.remove(r.restriction_id, "rm.user", Function.OPERATIONS, "Client called", at=AT)
    assert [e["event"] for e in reg.history("ACCT-1")] == ["placed", "removal_denied"]


def test_codes_that_need_a_decision_need_an_authorization_reference():
    reg = RestrictionRegistry()
    r = reg.place("ACCT-1", "SANC", "Flagged-list match", "ops.analyst", Function.OPERATIONS, AT)
    with pytest.raises(RestrictionError, match="authorization"):
        reg.remove(r.restriction_id, "sanctions.lead", Function.SANCTIONS, "False positive", None, AT)
    reg.remove(r.restriction_id, "sanctions.lead", Function.SANCTIONS, "False positive", "DET-2026-0412", AT)
    assert reg.active_codes("ACCT-1") == []
    assert reg.history("ACCT-1")[-1]["authorization"] == "DET-2026-0412"


def test_owner_removes_a_code_that_needs_no_authorization():
    reg = RestrictionRegistry()
    r = reg.place("ACCT-1", "CIPV", "Identity verification pending", "ops.analyst", Function.OPERATIONS, AT)
    reg.remove(r.restriction_id, "ops.analyst", Function.OPERATIONS, "Identity verified", at=AT)
    assert reg.active("ACCT-1") == []


def test_a_removed_code_cannot_be_removed_again():
    reg = RestrictionRegistry()
    r = reg.place("ACCT-1", "KYCR", "Refresh overdue", "review.bot", Function.PERIODIC_REVIEW, AT)
    reg.remove(r.restriction_id, "review.analyst", Function.PERIODIC_REVIEW, "Refreshed", at=AT)
    with pytest.raises(RestrictionError, match="already removed"):
        reg.remove(r.restriction_id, "review.analyst", Function.PERIODIC_REVIEW, "Again", at=AT)


def test_unknown_codes_and_blank_reasons_are_refused():
    reg = RestrictionRegistry()
    with pytest.raises(RestrictionError, match="Unknown"):
        reg.place("ACCT-1", "ZZZ", "x", "ops", Function.OPERATIONS, AT)
    with pytest.raises(RestrictionError, match="reason"):
        reg.place("ACCT-1", "CIPV", "  ", "ops", Function.OPERATIONS, AT)


def test_activity_checks_name_the_blocking_codes():
    reg = RestrictionRegistry()
    reg.place("ACCT-1", "KYCR", "Refresh overdue", "review.bot", Function.PERIODIC_REVIEW, AT)
    reg.place("ACCT-1", "LEGAL", "State tax levy", "legal.analyst", Function.LEGAL, AT)
    assert reg.check("ACCT-1", Activity.BUY) == (False, ["KYCR"])
    assert reg.check("ACCT-1", Activity.WITHDRAWAL) == (False, ["LEGAL"])
    assert reg.check("ACCT-1", Activity.SELL) == (True, [])


def test_deceased_code_stops_trading_outgoing_and_fee_deduction():
    reg = RestrictionRegistry()
    reg.place("ACCT-1", "DECD", "Death certificate received", "estates.analyst", Function.OPERATIONS, AT)
    for activity in (Activity.BUY, Activity.SELL, Activity.WITHDRAWAL, Activity.FEE_DEDUCTION):
        assert reg.check("ACCT-1", activity)[0] is False
    assert reg.check("ACCT-1", Activity.DEPOSIT) == (True, [])


def test_restrictions_are_per_account():
    reg = RestrictionRegistry()
    reg.place("ACCT-1", "SANC", "Match", "ops", Function.OPERATIONS, AT)
    assert reg.check("ACCT-2", Activity.BUY) == (True, [])


def test_restriction_serializes_with_owner_and_description():
    reg = RestrictionRegistry()
    d = reg.place("ACCT-1", "EDDP", "High risk", "ops", Function.OPERATIONS, AT).to_dict()
    assert d["owner"] == "aml_compliance" and d["description"].startswith("Enhanced due diligence")
