"""
Position reconciliation (map 13): match the books to the street, classify each break, route it, and escalate by age
and value.

The books are the general ledger's location accounts (DTC 1500, transfer agents 1510, sponsors 1520, the settlement
bank 1010), which the three-layer invariant already ties to the client and RIA master layers. The street is what DTC,
the transfer agents, the sponsors, and the bank report.
"""
from __future__ import annotations

from datetime import date

from . import policy
from .calendar import bdays_between

LOCATIONS = {"1500": "DTC", "1510": "Transfer agents (Fund/SERV)", "1520": "Sponsors", "1010": "Settlement bank"}

# Known break patterns: what each is, how it's fixed, and whether the fix can post by itself (REC-002)
PATTERNS = {
    "dividend_reinvestment": {"label": "Dividend reinvested at the transfer agent, not yet booked to holders",
                              "known": True,
                              "fix": "Book each holder's reinvested shares from the fund's dividend notice"},
    "ach_return_unbooked": {"label": "ACH return at the bank, not yet reversed on the client ledger",
                            "known": False,
                            "fix": "Process the return on the deposit: reverse it on the client ledger and tell the RIA"},
    "late_delivery": {"label": "Shares delivered late on a transfer, matched to the account",
                      "known": False, "fix": "Book the shares to the account the delivery belongs to"},
    "bank_fee": {"label": "Bank fee debited, not yet booked", "known": True,
                 "fix": "Book the fee to the custodian's expense; no client is affected"},
    "unexplained": {"label": "Unexplained difference", "known": False,
                    "fix": "Investigate: trace the activity at the depository or bank"},
}


def compare(books: dict[tuple[str, str], float], street: dict[tuple[str, str], float]) -> list[dict]:
    """Every (location, asset) where the books and the street differ (REC-001: positions exactly, cash to the cent)."""
    out = []
    for key in sorted(set(books) | set(street)):
        b, s = round(books.get(key, 0), 6), round(street.get(key, 0), 6)
        if abs(b - s) > (0.005 if key[1] == "USD" else 1e-9):
            out.append({"location": key[0], "asset": key[1], "books": b, "street": s, "difference": round(s - b, 6)})
    return out


def route(pattern: str, value: float) -> str:
    """REC-002: auto-fix, AI-suggest, or human-only."""
    if value > policy.param("REC-002", "human_over") or pattern == "unexplained":
        return "human_only"
    if PATTERNS.get(pattern, {}).get("known") and value <= policy.param("REC-002", "auto_fix_max"):
        return "auto_fix"
    return "ai_suggest"


def escalate(opened: date, as_of: date, value: float) -> str | None:
    """REC-003: why a break goes to a supervisor, or None."""
    age = bdays_between(opened, as_of)
    reasons = []
    if age > policy.param("REC-003", "age_days"):
        reasons.append(f"open {age} business days")
    if value > policy.param("REC-003", "value"):
        reasons.append(f"${value:,.2f}")
    return ", ".join(reasons) or None


def certifiable(breaks: list[dict]) -> tuple[bool, str]:
    """REC-004: certify only when nothing is open but expected in-transit items."""
    open_ = [b for b in breaks if b["status"] not in ("resolved",) and b.get("pattern") != "in_transit"]
    if open_:
        return False, f"{len(open_)} break{'s' if len(open_) != 1 else ''} still open"
    return True, "No open breaks besides expected in-transit items"
