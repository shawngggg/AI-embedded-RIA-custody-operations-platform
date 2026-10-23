"""
Cash settlement (map 12): matching unapplied incoming cash to accounts, and netting the day's settlement by channel.
The three-layer invariant check is ledger.proof(); the cycle itself (posting, releasing held ACH funds, sending net
funding) runs in the app (app/settlement.py), which records each step.
"""
from __future__ import annotations

import re
from datetime import date

from . import policy
from .calendar import bdays_between

_ACCOUNT = re.compile(r"\bACC[\s\-_]*([0-9OIl]{4})\b", re.I)


def _digits(s: str) -> str:
    return s.upper().replace("O", "0").replace("I", "1").replace("L", "1")


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def match_receipt(memo: str, sender: str, accounts: list[dict]) -> list[dict]:
    """Candidate accounts for an incoming wire or ACH credit, best first: {account, name, score, why}.

    An exact account number in the memo scores 0.98. One read with letters for digits (O for 0, I or l for 1) scores
    0.75, because a typo that lands on a real account is likely but not certain. The account owner's name in the memo
    or as the sender adds 0.20. STL-001 applies a match at 0.90 or above by itself; anything lower waits for an analyst.
    """
    out = {}
    memo = memo or ""
    for m in _ACCOUNT.finditer(memo):
        raw = m.group(1)
        exact = raw.isdigit()
        acct_id = f"ACC-{_digits(raw)}"
        for a in accounts:
            if a["id"] == acct_id:
                score = 0.98 if exact else 0.75
                why = [f"Memo names {acct_id}" if exact else f"Memo reads ACC-{raw}: {acct_id} with letters read as "
                       "digits"]
                out[a["id"]] = {"account": a["id"], "name": a["name"], "score": score, "why": why}
    text = _norm(memo + " " + (sender or ""))
    for a in accounts:
        names = [a["name"], *a.get("owners", [])]
        if any(_norm(n) and _norm(n) in text for n in names):
            c = out.setdefault(a["id"], {"account": a["id"], "name": a["name"], "score": 0.55, "why": []})
            c["score"] = min(0.99, c["score"] + 0.20) if c["why"] else c["score"]
            c["why"].append(f"Names the account owner ({a['name']})")
    return sorted(out.values(), key=lambda c: (-c["score"], c["account"]))


def auto_apply(candidates: list[dict]) -> dict | None:
    """STL-001: the one candidate at or above the threshold, clear of the next best."""
    if not candidates:
        return None
    best = candidates[0]
    runner = candidates[1]["score"] if len(candidates) > 1 else 0
    return best if best["score"] >= policy.param("STL-001", "threshold") and best["score"] - runner >= 0.2 else None


def return_due(received_on: date, as_of: date) -> bool:
    """STL-002: unmatched money goes back after this many business days."""
    return bdays_between(received_on, as_of) >= policy.param("STL-002", "return_after")


def net_by_channel(items: list[dict]) -> list[dict]:
    """items: {channel, amount (positive in, negative out)}. One net per channel, then the total."""
    nets: dict[str, float] = {}
    for i in items:
        nets[i["channel"]] = round(nets.get(i["channel"], 0) + i["amount"], 2)
    return [{"channel": c, "net": n, "direction": "Receive" if n >= 0 else "Pay"} for c, n in sorted(nets.items())]
