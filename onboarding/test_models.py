"""Milestone 1 tests for the RIA custody platform onboarding module.

Run from the repo root:   python -m pytest onboarding -v
Every test should pass once models.py matches the Milestone 1 spec.
"""
from datetime import date

from models import (
    AccountGroup,
    Path,
    RIAFirm,
    Account,
    reliance_eligible,
    determine_path,
    can_open,
)

TODAY = date(2026, 10, 10)

# Synthetic RIA firms
RIA_CURRENT = RIAFirm("Granite Peak Advisors", active=True, sec_registered=True,
                      reliance_contract=True, certification_date=date(2026, 3, 1))
RIA_EXPIRED = RIAFirm("Juniper Ridge Wealth", active=True, sec_registered=True,
                      reliance_contract=True, certification_date=date(2025, 6, 1))
RIA_STATE = RIAFirm("Bonneville Planning", active=True, sec_registered=False,
                    reliance_contract=False, certification_date=None)
RIA_INACTIVE = RIAFirm("Wasatch Capital", active=False, sec_registered=True,
                       reliance_contract=True, certification_date=date(2026, 3, 1))


# ---------- reliance_eligible ----------

def test_current_certification_is_eligible():
    assert reliance_eligible(RIA_CURRENT, TODAY) is True


def test_expired_certification_is_not_eligible():
    assert reliance_eligible(RIA_EXPIRED, TODAY) is False


def test_state_registered_firm_is_not_eligible():
    # No certification date at all: the function must not crash on None
    assert reliance_eligible(RIA_STATE, TODAY) is False


def test_exactly_365_days_is_still_eligible():
    ria = RIAFirm("Edge Case LLC", True, True, True, date(2025, 10, 10))  # 365 days before TODAY
    assert reliance_eligible(ria, TODAY) is True


def test_366_days_is_not_eligible():
    ria = RIAFirm("Edge Case LLC", True, True, True, date(2025, 10, 9))   # 366 days before TODAY
    assert reliance_eligible(ria, TODAY) is False


# ---------- determine_path ----------

def test_pure_ria_account_under_eligible_ria_takes_path_a():
    account = Account("ACC-001", RIA_CURRENT, AccountGroup.PURE_RIA)
    assert determine_path(account, TODAY) is Path.A


def test_self_directed_account_always_takes_path_b():
    account = Account("ACC-002", RIA_CURRENT, AccountGroup.SELF_DIRECTED)
    assert determine_path(account, TODAY) is Path.B


def test_pure_ria_account_under_expired_ria_takes_path_b():
    account = Account("ACC-003", RIA_EXPIRED, AccountGroup.PURE_RIA)
    assert determine_path(account, TODAY) is Path.B


def test_pure_ria_account_under_state_registered_ria_takes_path_b():
    account = Account("ACC-004", RIA_STATE, AccountGroup.PURE_RIA)
    assert determine_path(account, TODAY) is Path.B


# ---------- can_open (the account-opening gate) ----------

def test_inactive_ria_cannot_open_accounts():
    new = Account("ACC-010", RIA_INACTIVE, AccountGroup.PURE_RIA)
    allowed, reason = can_open(new, [])
    assert allowed is False
    assert reason == "RIA not active"


def test_state_registered_ria_can_still_open_a_pure_ria_account():
    # Certification decides the path, not whether the account can open
    new = Account("ACC-011", RIA_STATE, AccountGroup.PURE_RIA)
    assert can_open(new, []) == (True, "OK")


def test_self_directed_needs_an_open_ria_managed_account():
    new = Account("ACC-012", RIA_CURRENT, AccountGroup.SELF_DIRECTED)
    allowed, reason = can_open(new, [])
    assert allowed is False
    assert reason == "Self-directed account needs an open RIA-managed account"


def test_self_directed_opens_when_client_has_an_open_ria_managed_account():
    existing = [Account("ACC-013", RIA_CURRENT, AccountGroup.PURE_RIA)]
    new = Account("ACC-014", RIA_CURRENT, AccountGroup.SELF_DIRECTED)
    assert can_open(new, existing) == (True, "OK")


def test_closed_ria_managed_account_does_not_count():
    existing = [Account("ACC-015", RIA_CURRENT, AccountGroup.PURE_RIA, is_open=False)]
    new = Account("ACC-016", RIA_CURRENT, AccountGroup.SELF_DIRECTED)
    allowed, reason = can_open(new, existing)
    assert allowed is False
    assert reason == "Self-directed account needs an open RIA-managed account"


def test_only_one_self_directed_account_per_client():
    existing = [
        Account("ACC-017", RIA_CURRENT, AccountGroup.PURE_RIA),
        Account("ACC-018", RIA_CURRENT, AccountGroup.SELF_DIRECTED),
    ]
    new = Account("ACC-019", RIA_CURRENT, AccountGroup.SELF_DIRECTED)
    allowed, reason = can_open(new, existing)
    assert allowed is False
    assert reason == "Client already has a self-directed account"
