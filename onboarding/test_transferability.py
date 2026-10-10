"""Tests for transferability rules (Milestone 6)."""
from datetime import date

import pytest

from applications import Position
from rules import validate_rules
from sample_data import ACCOUNTS
from transferability import XFER_RULES, Route, review_position, review_transfer

TODAY = date(2026, 10, 10)


def pos(security_id, qty=100.0, asset_type="equity"):
    return Position(security_id, security_id, asset_type, qty)


def route(security_id, direction="incoming", **kw):
    return review_position(pos(security_id, kw.pop("qty", 100.0)), direction, TODAY, **kw)


def test_rule_library_is_valid():
    assert validate_rules(XFER_RULES) == []


def test_dtc_eligible_stock_moves_in_kind_through_acats():
    d = route("037833100")
    assert d.route is Route.ACATS_IN_KIND and d.quantity_in_kind == 100 and d.rule_id == "XFR-006"


def test_fractional_share_is_sold_as_cash_in_lieu():
    d = route("922908363", qty=410.5)
    assert (d.quantity_in_kind, d.quantity_to_liquidate) == (410.0, 0.5)
    assert "cash in lieu" in d.reason


def test_mutual_fund_moves_through_acats_fund_serv():
    d = route("SYN-MF-03")
    assert d.route is Route.ACATS_IN_KIND and d.rule_id == "XFR-007"


def test_sponsor_held_alternative_re_registers_by_loi():
    d = route("SYN-REIT-01")
    assert d.route is Route.LOI and d.counterparty == "Summit REIT Sponsor (synthetic)"
    assert d.action.startswith("Send an LOI")


def test_annuity_re_registers_by_loi():
    assert route("SYN-ANN-05").route is Route.LOI


def test_proprietary_product_of_the_delivering_firm_is_liquidated():
    d = route("SYN-PRV-01")
    assert d.route is Route.LIQUIDATE and d.quantity_to_liquidate == 100 and d.rule_id == "XFR-003"


def test_no_agreement_goes_to_product_acceptance_and_the_transfer_waits():
    d = route("SYN-LP-02")
    assert d.route is Route.PRODUCT_ACCEPTANCE and d.counterparty == "Risk and compliance"


def test_client_can_choose_liquidation_instead_of_product_acceptance():
    d = route("SYN-LP-02", client_elects_liquidation=True)
    assert d.route is Route.LIQUIDATE and d.rule_id == "XFR-004"


def test_restricted_and_unknown_securities_need_review():
    assert route("SYN-RST-04").route is Route.REVIEW
    assert route("NOT-IN-MASTER").route is Route.REVIEW


def test_outgoing_transfers_skip_the_platform_acceptance_check():
    assert route("SYN-PRV-01", "outgoing").route is Route.REVIEW      # not held here; no route
    assert route("SYN-REIT-01", "outgoing").route is Route.LOI
    assert route("037833100", "outgoing").route is Route.ACATS_IN_KIND


def test_all_in_kind_is_a_full_release():
    review = review_transfer([pos("037833100"), pos("SYN-MF-03")], "incoming", TODAY)
    assert review.release == "full"


def test_loi_or_liquidation_makes_a_partial_release_until_confirmed():
    review = review_transfer(ACCOUNTS["maria_chen"].funding_positions, "incoming", TODAY)
    assert [d.route for d in review.dispositions] == [Route.ACATS_IN_KIND, Route.ACATS_IN_KIND, Route.LIQUIDATE]
    assert review.release == "partial"
    review.confirm("SYN-PRV-01")
    assert review.release == "full"


def test_anything_waiting_for_product_acceptance_holds_the_transfer():
    review = review_transfer([pos("037833100"), pos("SYN-LP-02")], "incoming", TODAY)
    assert review.release == "hold"


def test_liquidation_elections_apply_per_position():
    review = review_transfer([pos("037833100"), pos("SYN-LP-02")], "incoming", TODAY,
                             liquidation_elections={"SYN-LP-02"})
    assert review.release == "partial"


def test_confirming_an_in_kind_position_is_an_error():
    review = review_transfer([pos("037833100")], "incoming", TODAY)
    with pytest.raises(KeyError):
        review.confirm("037833100")


def test_direction_must_be_known():
    with pytest.raises(ValueError):
        review_transfer([], "sideways", TODAY)


def test_review_serializes_for_the_audit_log():
    d = review_transfer([pos("SYN-REIT-01")], "incoming", TODAY).to_dict()
    assert d["release"] == "partial" and d["dispositions"][0]["route"] == "loi"
