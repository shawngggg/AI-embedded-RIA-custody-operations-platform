"""Tests for the rolling review (map 03, MVP)."""
from datetime import date

import pytest

from restrictions import Activity, Function, RestrictionError, RestrictionRegistry
from rolling_review import complete, enforce_deadline, request_refresh, schedule, trigger_early

OPENED = date(2026, 10, 10)


def test_next_review_follows_the_risk_tier():
    assert schedule("ACC-1", "Maria Chen", "LOW", OPENED).next_review == date(2027, 10, 10)
    assert schedule("ACC-2", "Elena Marsh-Duval", "HIGH", OPENED).next_review == date(2027, 4, 10)


def test_review_shows_as_due_within_30_days():
    r = schedule("ACC-1", "Maria Chen", "HIGH", OPENED)
    assert not r.due(date(2027, 3, 10)) and r.due(date(2027, 3, 11))


def test_missed_deadline_restricts_new_activity():
    reg = RestrictionRegistry()
    r = request_refresh(schedule("ACC-1", "Maria Chen", "LOW", OPENED), date(2027, 10, 1))
    assert r.response_due == date(2027, 10, 31)
    enforce_deadline(r, reg, date(2027, 10, 31))
    assert r.status == "requested" and reg.active("ACC-1") == []
    enforce_deadline(r, reg, date(2027, 11, 1))
    assert r.status == "restricted" and reg.check("ACC-1", Activity.BUY) == (False, ["KYCR"])
    assert reg.check("ACC-1", Activity.SELL) == (True, [])


def test_completing_the_review_lifts_the_code_and_resets_the_date():
    reg = RestrictionRegistry()
    r = request_refresh(schedule("ACC-1", "Maria Chen", "LOW", OPENED), date(2027, 10, 1))
    enforce_deadline(r, reg, date(2027, 11, 2))
    complete(r, "risk_increased", "HIGH", reg, date(2027, 11, 5))
    assert reg.active("ACC-1") == [] and r.status == "complete" and r.next_review == date(2028, 5, 5)
    assert reg.history("ACC-1")[-1]["function"] == "periodic_review"


def test_only_the_periodic_review_team_can_lift_its_code():
    reg = RestrictionRegistry()
    r = request_refresh(schedule("ACC-1", "Maria Chen", "LOW", OPENED), date(2027, 10, 1))
    enforce_deadline(r, reg, date(2027, 11, 2))
    with pytest.raises(RestrictionError):
        reg.remove(r.restriction_id, "ops", Function.OPERATIONS, "Client sent it", at=None)


def test_ownership_change_brings_the_review_forward():
    r = schedule("ACC-1", "Whitfield Family Holdings LLC", "LOW", OPENED)
    trigger_early(r, "ownership_change", date(2026, 12, 1))
    assert r.next_review == date(2026, 12, 1) and r.due(date(2026, 12, 1)) and r.trigger == "ownership_change"


def test_review_must_be_scheduled_before_it_is_requested():
    r = request_refresh(schedule("ACC-1", "Maria Chen", "LOW", OPENED), date(2027, 10, 1))
    with pytest.raises(ValueError):
        request_refresh(r, date(2027, 10, 2))


def test_outcome_must_be_known():
    with pytest.raises(ValueError):
        complete(schedule("ACC-1", "Maria Chen", "LOW", OPENED), "fine", "LOW", RestrictionRegistry(), OPENED)
