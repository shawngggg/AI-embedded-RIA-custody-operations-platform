"""
Customer protection (SEC Rule 15c3-3), from the books: the reserve formula and the possession-or-control check.

    reserve_computation(client_cash, gl, on_deposit, as_of) -> dict
    possession_or_control(customer, control, names) -> dict

The reserve formula (Exhibit A) nets what the custodian owes customers in cash (credits) against what customers owe it
(debits). When credits exceed debits, the difference must sit in the special reserve bank account for the exclusive
benefit of customers. The platform computes it every business day as of that day's close. Since the 2024 amendments,
a broker-dealer with $500 million or more in average total credits must compute daily (compliance date June 30, 2026);
below that the weekly computation is allowed, and a firm may choose daily, which lets it reduce aggregate debits by 2%
instead of 3%. A deficit is deposited no later than one hour after banks open on the second business day after the
computation date.

This book has no margin lending and no stock loan, so most Exhibit A lines are zero; the computation shows the lines
the books can fill and says which ones are empty.

Possession or control (paragraph (d)): every customer's fully paid securities must sit at a good control location
(here DTC, the fund transfer agents through Fund/SERV, and the sponsors of non-traded products holding in the
custodian's name). The check compares the stock record (what the books hold for customers at those locations) with
what the street confirms there (the books, adjusted for open reconciliation differences), security by security. A
shortfall is a deficit to bring back into control.
"""
from __future__ import annotations

from datetime import date

from . import policy
from .calendar import bday
from .ledger import CASH, LOCATION_GL, amount

RESERVE_GL = "1030"
CONTROL_LOCATIONS = tuple(LOCATION_GL.values())       # 1500 DTC, 1510 transfer agents, 1520 sponsors
GL_CREDITS = {"2190": "Unapplied receipts in suspense, pending identification to a customer",
              "2200": "Outgoing customer cash released but not yet settled"}


def reserve_computation(client_cash: dict[str, int], gl: dict[tuple[str, str], int], on_deposit: int,
                        as_of: date) -> dict:
    """client_cash: each client account's total cash (all buckets) in units. gl: GL balances by (account, asset), debit
    positive. on_deposit: the reserve account's balance in units."""
    p = policy.BY_ID["CP-001"]["params"]
    free = sum(u for u in client_cash.values() if u > 0)
    owed_by = sum(-u for u in client_cash.values() if u < 0)
    credits = [{"line": "Free credit balances in customers' accounts", "amount": amount(free),
                "accounts": sum(1 for u in client_cash.values() if u > 0)}]
    for acct, label in GL_CREDITS.items():
        credits.append({"line": label, "amount": amount(max(0, -gl.get((acct, CASH), 0)))})
    debits = [{"line": "Debit balances in customers' cash accounts", "amount": amount(owed_by),
               "accounts": sum(1 for u in client_cash.values() if u < 0)}]
    total_credits = round(sum(c["amount"] for c in credits), 2)
    gross_debits = round(sum(d["amount"] for d in debits), 2)
    haircut = round(gross_debits * p["debit_reduction"], 2)
    total_debits = round(gross_debits - haircut, 2)
    required = round(max(0.0, total_credits - total_debits), 2)
    deposit = round(amount(on_deposit), 2)
    gap = round(deposit - required, 2)
    due = bday(as_of, p["deposit_bdays"])
    return {"as_of": as_of.isoformat(), "credits": credits, "debits": debits, "total_credits": total_credits,
            "gross_debits": gross_debits, "debit_reduction": haircut, "total_debits": total_debits,
            "required": required, "on_deposit": deposit, "excess": max(gap, 0.0), "deficit": max(-gap, 0.0),
            "deposit_due": f"{due.isoformat()} {p['deposit_by']} ET" if gap < 0 else None,
            "empty_lines": ["Margin debits and stock borrowed: the platform doesn't lend",
                            "Securities loaned and bank loans: none",
                            "Fails to deliver and fails to receive in cash: none on the books"],
            "frequency": "Daily (the platform's choice; required from $500 million in average total credits)"}


def possession_or_control(customer: dict[str, int], control: dict[str, int], names: dict[str, str]) -> dict:
    """customer: what the books hold for customers at good control locations, by security. control: what the street
    confirms at those locations. Both in units."""
    rows, deficits = [], []
    for sec in sorted(set(customer) | set(control)):
        if sec == CASH:
            continue
        need, have = customer.get(sec, 0), control.get(sec, 0)
        row = {"security": sec, "name": names.get(sec, sec), "customers": amount(need), "in_control": amount(have),
               "deficit": amount(max(0, need - have))}
        rows.append(row)
        if need > have:
            deficits.append(row)
    return {"securities": len(rows), "in_control": len(rows) - len(deficits), "deficits": deficits}
