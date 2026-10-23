"""
Product acceptance (map 24): whether the custodian can hold, price, and service a product. Operations reviews and
recommends; risk and compliance decide. Suitability stays with the RIA, and the sponsor checks investor qualification
through its subscription documents.

Also: posting a valuation (with its checks) and putting a product that's out of policy under restriction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from flagged_list import Kind, Subject, screen

from . import policy
from .flow import AI, AML, LEDGER, OPS, RULES, FlowResult, last_event

FUND_TYPES = {"mutual_fund", "etf"}
ALTERNATIVE_TYPES = {"alternative", "private_placement", "annuity", "lp"}
DOCS = {"offering": "Prospectus or private placement memorandum", "subscription": "Subscription documents",
        "financials": "Audited financials"}


@dataclass
class ProductContext:
    as_of: date
    already: dict | None = None            # the security master row, if the product is there
    flagged: list = field(default_factory=list)
    documents: list = field(default_factory=list)   # doc types received


def checks(req: dict, ctx: ProductContext) -> tuple[list[str], list[str], list[str]]:
    """(passes, problems, rule ids) for the product's type."""
    kind = req.get("asset_type", "")
    ok, bad = [], []
    if kind in FUND_TYPES:
        rule = ["PA-002"]
        if kind == "mutual_fund":
            (ok if req.get("fundserv_eligible") else bad).append("Fund/SERV eligibility")
            (ok if req.get("fund_agreement") else bad).append("Fund agreement with the fund company")
            (ok if req.get("rule_22c2") else bad).append("Rule 22c-2 shareholder information agreement")
        else:
            (ok if req.get("dtc_eligible") else bad).append("DTC eligibility")
        return ok, bad, rule
    rule = ["PA-003"]
    sponsor = req.get("sponsor") or ""
    hits = screen([Subject("sponsor", Kind.ENTITY, sponsor, "sponsor")], ctx.flagged, ctx.as_of) if sponsor else []
    (bad if hits else ok).append(f"Sponsor on the flagged list: {sponsor}" if hits else
                                 f"Sponsor clear of the flagged list: {sponsor}")
    (bad if req.get("adverse_media") else ok).append("Adverse media about the sponsor" if req.get("adverse_media")
                                                     else "No adverse media found")
    for key, label in (("audited_financials", "Audited financials"), ("nominee_registration",
                       "Registration in the custodian's name (nominee)"), ("administrator", "A fund administrator"),
                       ("valuation_source", "A valuation source")):
        (ok if req.get(key) else bad).append(label + (f": {req[key]}" if isinstance(req.get(key), str) else ""))
    if req.get("capital_calls"):
        (ok if req.get("aip") else bad).append("Capital calls through DTCC AIP or a set funding process")
    return ok, bad, rule


def process_product(req: dict, ctx: ProductContext, decisions: dict | None = None,
                    events: list[dict] | None = None) -> FlowResult:
    decisions, events = decisions or {}, events or []
    name = req.get("description") or req.get("security_id")
    res = FlowResult("product", f"Product acceptance: {name}", req.get("security_id") or name, ctx.as_of)
    kind = req.get("asset_type", "")
    group = "Fund or ETF" if kind in FUND_TYPES else "Alternative"
    res.summary = {"Product": name, "Type": kind.replace("_", " "), "Requested by": req.get("requested_by", "")}
    if req.get("sponsor"):
        res.summary["Sponsor"] = req["sponsor"]
    res.add(OPS, "Product request", req.get("source", "From the RIA"), [req.get("reason") or ""])
    if kind in ALTERNATIVE_TYPES or kind not in FUND_TYPES:
        missing = [label for key, label in DOCS.items() if key not in ctx.documents]
        res.add(AI, "Extract offering documents: prospectus or PPM, subscription documents, financials",
                "Received" if not missing else "Waiting for documents",
                [f"Missing: {', '.join(missing)}"] if missing else [], [])
        if missing:
            res.status, res.requested_items = "PENDING_DOCUMENTS", missing
            return res
    if ctx.already and ctx.already.get("accepted"):
        res.add(RULES, "Classify the product; check whether it is already held", "Already on the platform")
        res.status = "AVAILABLE"
        return res
    res.add(RULES, "Classify the product; check whether it is already held", group, [], ["PA-001"])
    ok, bad, rules = checks(req, ctx)
    res.add(RULES if kind in FUND_TYPES else AI,
            "Check fund agreements (Fund/SERV, Rule 22c-2) and ETF DTC eligibility" if kind in FUND_TYPES
            else "Check the sponsor and feasibility: background, financials, registration, administrator, valuation",
            "All checks pass" if not bad else f"{len(bad)} open", [f"Yes: {x}" for x in ok] + [f"No: {x}" for x in bad],
            rules)
    rec = last_event(events, "recommendation")
    if not rec:
        res.add(OPS, "Review: sponsor agreement, operational fit, sponsor background; recommend", "Waiting for operations")
        res.status = "PENDING_PRODUCT_REVIEW"
        return res
    res.add(OPS, "Review: sponsor agreement, operational fit, sponsor background; recommend",
            f"Recommends {rec['recommend']}", [f"{rec.get('by')}: {rec.get('note', '')}"], ["PA-001"])
    verdict = decisions.get("product")
    if verdict is None:
        res.add(AML, "Decide: accept or decline", "Waiting for risk and compliance", [], ["PA-001"])
        res.status = "PENDING_PRODUCT_DECISION"
        return res
    if verdict == "declined":
        res.add(AML, "Decide: accept or decline", "Declined", [], ["PA-001"])
        res.add(OPS, "Decline with the reason; options: hold away or use an alternate custodian", "Sent to the RIA")
        res.status = "DECLINED"
        res.requested_items = ["Declined. The client can keep it at the current firm, or the RIA can use another "
                               "custodian for it"]
        return res
    res.add(AML, "Decide: accept or decline", "Accepted", [], ["PA-001"])
    setup = last_event(events, "setup")
    res.add(LEDGER, "Set up the security: security master, pricing and valuation source, settlement method",
            "Set up" if setup else "Waiting for setup",
            [f"Settlement: {settlement_method(kind)}", f"Valuation: {req.get('valuation_source') or 'market price'}"])
    if not setup:
        res.status = "PENDING_SETUP"
        return res
    res.add(LEDGER, "Execute the custody or servicing agreement; connect AIP or Fund/SERV", "Done",
            [setup.get("note") or ""])
    res.effects.append({"type": "accept_security", "security_id": req.get("security_id"),
                        "settlement": settlement_method(kind)})
    res.add(LEDGER, "Notify the RIA: product available, fees, subscription documents", "Sent")
    res.status = "AVAILABLE"
    return res


def settlement_method(kind: str) -> str:
    return {"mutual_fund": "Fund/SERV", "etf": "DTC"}.get(kind, "Sponsor or administrator (AIP where connected)")


def valuation_checks(value: float, as_of: date, prior: float | None, today: date) -> list[str]:
    """PA-004: why a valuation needs a person before it posts."""
    out = []
    if (today - as_of).days > policy.param("PA-004", "stale_days"):
        out.append(f"As of {as_of.isoformat()}: more than {policy.param('PA-004', 'stale_days')} days old")
    if prior and abs(value - prior) / prior > policy.param("PA-004", "change"):
        out.append(f"{(value - prior) / prior:+.0%} from the prior value of ${prior:,.2f}")
    if value <= 0:
        out.append("Not a positive value")
    return out
