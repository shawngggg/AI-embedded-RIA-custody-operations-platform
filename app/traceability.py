"""
What a decision rests on: the policy version in force and the rules behind it, each with its version and citation.

Every decision event in the audit log (proposal, approval, rejection, principal acceptance, rescission request)
carries this, so a reviewer can trace a decision from the log entry alone, without replaying the case.
"""
from __future__ import annotations

from datetime import date

from account_changes import CHANGE_RULES
from account_rules import ACCOUNT_RULES, CIP_SOURCE
from firm_rules import FIRM_RULES
from risk import ACCOUNT_FACTORS, FIRM_FACTORS, PEP_SOURCE
from rules import POLICY_VERSION
from transferability import XFER_RULES

from custody import policy as r2

from .orm import Case

_LIBRARY = [*FIRM_RULES, *ACCOUNT_RULES, *CHANGE_RULES, *XFER_RULES, *ACCOUNT_FACTORS, *FIRM_FACTORS, *r2.library()]


def _r2(*rule_ids: str) -> list[dict]:
    return [{"rule_id": r, "rule_version": r2.BY_ID[r]["version"], "source": r2.BY_ID[r]["source"]} for r in rule_ids]


R2_DECISIONS = {"release", "risk_review", "product", "estate_docs", "legal_hold", "merger_classification",
                "negative_consent"}

# Decisions whose basis is a rule outside the rule library: a FINRA rule, a regulation, or platform policy.
STATIC = {
    "acceptance": [{"rule_id": "FINRA-4512", "rule_version": "4512(a)(1)(D)",
                    "source": "FINRA Rule 4512(a)(1)(D): the signature of the principal denoting the account was "
                              "accepted under the member's procedures for accepting accounts"}],
    "rescission": [{"rule_id": "FINRA-4512", "rule_version": "4512(a)(1)(D)",
                    "source": "FINRA Rule 4512(a)(1)(D): the acceptance being withdrawn"},
                   {"rule_id": "RSC-001", "rule_version": "1",
                    "source": "Platform policy: a rescission restricts the account at once, needs the RIA service "
                              "team lead's approval, and logs an operational risk event"}],
    "sanctions": [{"rule_id": "SCR-001", "rule_version": "1",
                   "source": "Custodian sanctions program (OFAC regulations, 31 CFR Chapter V): first-line check "
                             "against the sanctions team's flagged list; the sanctions team decides"}],
    "edd": [{"rule_id": "AML-EDD", "rule_version": "1",
             "source": "Broker-dealer AML program, including customer due diligence: 31 CFR 1023.210"}],
    "alert_review": [{"rule_id": "AML-ALR", "rule_version": "1",
                      "source": "Broker-dealer AML program and customer due diligence: 31 CFR 1023.210; " + PEP_SOURCE
                                + "; platform policy: every PEP or adverse-media alert goes to AML compliance"}],
    "identity_fraud": [{"rule_id": "CIP-FRD", "rule_version": "1",
                        "source": "31 CFR 1023.220(a)(2)(iii) (when the custodian can't form a reasonable belief that "
                                  "it knows a customer's true identity: when not to open, when to close, and when to "
                                  "file a SAR); platform policy: a suspected fraudulent identity goes to AML compliance"}],
    "confirmation": [{"rule_id": "FRD-001", "rule_version": "1",
                      "source": "Platform policy: address, bank, or contact changes together within 30 days need "
                                "RIA confirmation and a client callback"}],
    "verification": [{"rule_id": "CIP-NEW", "rule_version": "1",
                      "source": f"Customer identification program for new parties: {CIP_SOURCE}; verification under "
                                "31 CFR 1023.220(a)(2)(ii)"}],
    "release": _r2("MM-005", "MM-006", "MM-003"),
    "risk_review": _r2("MM-004"),
    "product": _r2("PA-001", "PA-002", "PA-003"),
    "estate_docs": _r2("DEC-002"),
    "legal_hold": _r2("ADV-001"),
    "merger_classification": _r2("MRG-001"),
    "negative_consent": _r2("MRG-001"),
}


def _in_force(rule_id: str, on: date) -> dict | None:
    """The library version of a rule in force on a date."""
    best = None
    for r in _LIBRARY:
        if r["rule_id"] != rule_id or date.fromisoformat(r["effective_date"]) > on:
            continue
        if r.get("expiry_date") and date.fromisoformat(r["expiry_date"]) <= on:
            continue
        if best is None or r["effective_date"] > best["effective_date"]:
            best = r
    return best


def basis(case: Case, decision_type: str) -> list[dict]:
    result = case.result or {}
    on = date.fromisoformat(result.get("evaluation_date") or date.today().isoformat())
    items: list[dict] = [dict(s) for s in STATIC.get(decision_type, [])]
    findings = (result.get("findings") or {}).get("decisions") or []
    if decision_type == "sanctions" and result.get("escalation"):
        items[0]["flagged_entries"] = sorted({h.get("entry_id") for h in result["escalation"].get("hits", [])} - {None})
    if decision_type in ("edd", "alert_review"):
        for f in (result.get("risk") or {}).get("factors") or []:
            lib = _in_force(f["factor_id"], on)
            items.append({"rule_id": f["factor_id"], "rule_version": lib["version"] if lib else "1",
                          "source": f.get("source") or (lib or {}).get("source", "")})
    if decision_type in ("compliance_signoff", "acceptance", "verification", "confirmation"):
        items += [{"rule_id": d["rule_id"], "rule_version": d["rule_version"], "source": d["source"]} for d in findings]
    if decision_type in ("confirmation", "verification", "compliance_signoff") or decision_type in R2_DECISIONS:
        for rid in sorted({r for s in result.get("steps", []) for r in s.get("rule_ids", [])}):
            lib = _in_force(rid, on)
            if lib:
                items.append({"rule_id": rid, "rule_version": lib["version"], "source": lib.get("source", "")})
    seen, out = set(), []
    for i in items:
        if i["rule_id"] not in seen and i.get("source"):
            seen.add(i["rule_id"]); out.append(i)
    return out


def stamp(case: Case, decision_type: str) -> dict:
    """Audit-event fields for a decision: the policy version and the rules, with citations, it rests on."""
    return {"policy_version": (case.result or {}).get("policy_version") or POLICY_VERSION,
            "basis": basis(case, decision_type)}
