"""
Identity theft red flags at account opening (walkthrough note 5, Nov. 1, 2026). The SEC's Regulation S-ID (and the
CFTC's identical list) gives, among its examples, a tax ID that's the same as one another customer gave, and an address
or phone number shared with an unusually large number of other people. The custodian checks every new holder against
the clients already on file, Path A included: relying on the RIA for CIP doesn't hand over the custodian's own
identity theft program.

- A tax ID on file for someone with a different name: always a red flag.
- A phone on file for someone else: a red flag when it's a different family name (a household often shares a phone),
  or when two or more other people already use it (platform policy, standing in for "unusually large" in a small book).

The flags are worked out once, when the case is created, and kept on the case, so a replay reads the same ones. CIP
reviews them (app/queues.py, red_flag_review); a suspected fraud goes to AML compliance through the identity check.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .orm import Account

POLICY = "Regulation S-ID red flags (17 CFR 248, subpart C, appendix A, supplement A, items 14 and 15)"


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _name_key(name: str | None) -> tuple[str, str]:
    """First and last name, lowercased: "Priscilla A. Ng" and "Priscilla Ng" are the same person."""
    parts = [p for p in re.sub(r"[^a-z\s'-]", " ", (name or "").lower()).split() if len(p) > 1 or p.isalpha()]
    parts = [p for p in parts if len(p) > 1]
    return (parts[0], parts[-1]) if parts else ("", "")


def same_person(a: str | None, b: str | None) -> bool:
    return _name_key(a) == _name_key(b) and _name_key(a) != ("", "")


def find(db: Session, holders: list[dict], account_id: str | None) -> list[dict]:
    """[{person, flag}] for the new holders, against every other account's holders."""
    by_tax: dict[str, list[tuple[str, str]]] = {}
    by_phone: dict[str, list[tuple[str, str]]] = {}
    for a in db.scalars(select(Account).where(Account.id != (account_id or ""))).all():
        for h in (a.application or {}).get("holders", []):
            tax, phone = _digits(h.get("tax_id")), _digits(h.get("phone"))
            if len(tax) >= 9:
                by_tax.setdefault(tax, []).append((a.id, h.get("name") or a.display_name))
            if len(phone) >= 10:
                by_phone.setdefault(phone, []).append((a.id, h.get("name") or a.display_name))
    flags: list[dict] = []
    for h in holders:
        name = h.get("name")
        tax, phone = _digits(h.get("tax_id")), _digits(h.get("phone"))
        for acct, other in by_tax.get(tax, []) if len(tax) >= 9 else []:
            if not same_person(name, other):
                flags.append({"person": name, "flag": f"Tax ID ending {tax[-4:]} is on file for {other} ({acct}) "
                                                      "under a different name"})
        sharing = {(acct, other) for acct, other in by_phone.get(phone, []) if not same_person(name, other)} \
            if len(phone) >= 10 else set()
        other_families = {o for _, o in sharing if _name_key(o)[1] != _name_key(name)[1]}
        if other_families or len({o for _, o in sharing}) >= 2:
            who = ", ".join(sorted(f"{o} ({acct})" for acct, o in sharing))
            flags.append({"person": name, "flag": f"Phone ending {phone[-4:]} is also on file for {who}"})
    out, seen = [], set()
    for f in flags:
        if (f["person"], f["flag"]) not in seen:
            seen.add((f["person"], f["flag"]))
            out.append(f)
    return out
