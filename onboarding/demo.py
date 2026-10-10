"""
Run the onboarding scenarios: from the command line (python demo.py) and from
the browser console, which calls run_json() through Pyodide.
"""

from __future__ import annotations

import json
import sys
from datetime import date

from firm_rules import to_ria_firm
from models import Account
from pipeline import Decisions, OnboardingResult, onboard_firm, onboard_firm_from_package, open_account
from risk import rate_firm
from sample_data import (
    ACCOUNT_FIRMS, ACCOUNT_LABELS, ACCOUNTS, EXISTING_ACCOUNTS, FIRM_LABELS, FIRM_PACKAGE_TEXT, FIRMS,
    FLAGGED_LIST, IAPD_LOOKUP, ILLUSTRATIVE_EXTRACTION, TODAY,
)

PACKAGE_KEY = "red_butte_package"


def run_firm(key: str, as_of: date = TODAY, **decisions) -> OnboardingResult:
    d = Decisions(**decisions)
    if key == PACKAGE_KEY:
        return onboard_firm_from_package(FIRM_PACKAGE_TEXT, as_of, payload=ILLUSTRATIVE_EXTRACTION,
                                         flagged=FLAGGED_LIST, registration_lookup=IAPD_LOOKUP, decisions=d)
    return onboard_firm(FIRMS[key], as_of, flagged=FLAGGED_LIST, decisions=d)


def run_account(key: str, as_of: date = TODAY, **decisions) -> OnboardingResult:
    app, firm = ACCOUNTS[key], FIRMS[ACCOUNT_FIRMS[key]]
    ria = to_ria_firm(firm)
    existing = [Account(aid, ria, group, is_open) for aid, group, is_open in EXISTING_ACCOUNTS.get(key, [])]
    return open_account(app, firm, as_of, existing=existing, ria_risk_tier=rate_firm(firm, as_of).tier,
                        flagged=FLAGGED_LIST, decisions=Decisions(**decisions))


def catalog() -> dict:
    firms = [{"key": PACKAGE_KEY, "label": "Application package read by AI (controls gate)",
              "name": "Red Butte Capital Management LLC"}]
    firms += [{"key": k, "label": FIRM_LABELS[k], "name": FIRMS[k].legal_name} for k in FIRMS]
    accounts = [{"key": k, "label": ACCOUNT_LABELS[k], "firm": FIRMS[ACCOUNT_FIRMS[k]].legal_name,
                 "name": ACCOUNTS[k].entity_name or ACCOUNTS[k].primary.name} for k in ACCOUNTS]
    return {"as_of": TODAY.isoformat(), "firms": firms, "accounts": accounts,
            "package_text": FIRM_PACKAGE_TEXT}


def run_json(kind: str, key: str, as_of: str, decisions: str = "{}") -> str:
    """Entry point for the browser console. Returns the result as JSON."""
    when = date.fromisoformat(as_of)
    choices = {k: v for k, v in json.loads(decisions or "{}").items() if v not in (None, "")}
    result = run_firm(key, when, **choices) if kind == "firm" else run_account(key, when, **choices)
    return json.dumps(result.to_dict(), default=str)


def print_result(res: OnboardingResult) -> None:
    print(f"{res.subject}  [{res.kind}]  evaluated {res.as_of.isoformat()}")
    for s in res.steps:
        print(f"  {s.lane:<28} {s.name}")
        print(f"  {'':<28}   -> {s.outcome}")
        for line in s.detail:
            print(f"  {'':<28}      {line}")
    codes = ", ".join(r["code"] for r in res.restrictions) or "none"
    print(f"  STATUS: {res.status}   path: {res.path or '-'}   restriction codes: {codes}\n")


def main() -> None:
    print("=" * 78)
    print(f"RIA CUSTODY PLATFORM: ONBOARDING MODULE (synthetic data, evaluated {TODAY.isoformat()})")
    print("=" * 78 + "\n")
    print("RIA FIRMS\n")
    for key in [PACKAGE_KEY, *FIRMS]:
        print_result(run_firm(key))
    print("Replaying the waiting branches with people's decisions:\n")
    print_result(run_firm("meridian_gate", sanctions="true_match", sanctions_reference="DET-2026-0101"))
    print_result(run_firm("lakeshore", edd="approved", edd_reference="EDD-2026-0102"))
    print("CLIENT ACCOUNTS\n")
    for key in ACCOUNTS:
        print_result(run_account(key))
    print_result(run_account("ilya_sorvetkin", sanctions="false_positive", sanctions_reference="DET-2026-0103"))
    print_result(run_account("elena_marsh_pep", edd="approved", edd_reference="EDD-2026-0104"))
    print("Same entity account before FinCEN's Feb. 13, 2026 exceptive relief:\n")
    print_result(run_account("whitfield_holdings", date(2026, 2, 12)))


if __name__ == "__main__":
    sys.exit(main())
