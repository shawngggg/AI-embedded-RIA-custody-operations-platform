"""
Asset and money transfers (map 08): ACH deposits and withdrawals, wires out, journals, ACATS in and out, and non-ACATS
re-registration by LOI.

    process_transfer(request, ctx, decisions, events) -> FlowResult

The request is what the RIA, the client, or operations submitted (stored on the case). ctx is what the app knows today
(the account, its codes, cash and positions, the security master, the client's grant and bank links). decisions are
what people approved under maker-checker (sanctions, risk review, release). events are what happened since: a fix
applied, a callback, the request going to the rail, the rail's answers, a correction approved, a chase sent.

Incoming wires aren't requests: they arrive at the bank, and cash settlement (settlement.py) applies them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from applications import Position
from flagged_list import Kind, Subject, screen
from restrictions import CATALOG, Activity
from transferability import Route, review_transfer

from . import policy
from .calendar import add_bdays, bday, next_bday
from .flow import AI, AML, LEDGER, OPS, RULES, FlowResult, events_of, last_event, on_date
from .ledger import CASH, LOCATION_GL, client_move, gl_move, rebucket, units

TYPES = {
    "ach_deposit": {"label": "ACH deposit", "direction": "in", "rail": "ACH", "cash": True},
    "ach_withdrawal": {"label": "ACH withdrawal", "direction": "out", "rail": "ACH", "cash": True},
    "wire_out": {"label": "Wire out", "direction": "out", "rail": "Fedwire", "cash": True},
    "journal": {"label": "Journal between accounts", "direction": "internal", "rail": "Journal", "cash": True},
    "acats_in": {"label": "ACATS transfer in", "direction": "in", "rail": "ACATS", "cash": False},
    "acats_out": {"label": "ACATS transfer out", "direction": "out", "rail": "ACATS", "cash": False},
    "loi_in": {"label": "Re-registration by LOI", "direction": "in", "rail": "LOI", "cash": False},
}
ACTIVITY = {"ach_deposit": [Activity.DEPOSIT], "acats_in": [Activity.DEPOSIT], "loi_in": [Activity.DEPOSIT],
            "ach_withdrawal": [Activity.WITHDRAWAL], "wire_out": [Activity.WITHDRAWAL],
            "journal": [Activity.WITHDRAWAL], "acats_out": [Activity.TRANSFER_OUT]}
CAPABILITY = {"ach_withdrawal": "ach_out", "wire_out": "wire_out", "journal": "journal_out"}
RETURN_CODES = {"R01": "Insufficient funds", "R02": "Account closed", "R03": "No account or unable to locate account",
                "R04": "Invalid account number", "R10": "Customer advises not authorized"}
ACATS_REJECTS = {"title": "Account title on the transfer form doesn't match the delivering firm's records",
                 "ssn": "Tax ID mismatch", "nof": "Account number not on file at the delivering firm"}


@dataclass
class TransferContext:
    as_of: date
    account: dict                       # id, name, firm_crd, group, registration_type, status, codes, owners, lpoa,
    #                                     purpose, recent_changes [[type, iso]]
    cash_available: float = 0.0         # settled, unrestricted cash
    holdings: dict = field(default_factory=dict)       # security id -> settled quantity
    destination: dict | None = None     # a journal's receiving account (same shape as account)
    security_master: dict = field(default_factory=dict)   # security id -> transferability.SecurityRecord
    prices: dict = field(default_factory=dict)         # security id -> price
    locations: dict = field(default_factory=dict)      # security id -> where it's held (ledger.LOCATION_GL keys)
    grant: dict | None = None           # the client's active grant for this capability
    bank_links: dict = field(default_factory=dict)     # id -> {bank, last4, owner, status}
    products: dict = field(default_factory=dict)       # security id -> pending | accepted | declined
    flagged: list = field(default_factory=list)
    client_requests: list = field(default_factory=list)   # [{on, amount}] earlier self-service requests (ENT-003)
    account_value: float = 0.0


def _norm(name: str) -> str:
    return " ".join("".join(ch for ch in (name or "").lower() if ch.isalnum() or ch == " ").split())


def _amount(req: dict) -> float:
    return float(req.get("amount") or 0)


def subjects(req: dict) -> list[Subject]:
    out = []
    cp = req.get("counterparty") or {}
    if cp.get("owner_name"):
        out.append(Subject("payee", Kind.INDIVIDUAL if cp.get("owner_kind", "individual") == "individual"
                           else Kind.ENTITY, cp["owner_name"], "payee", country=cp.get("country")))
    if cp.get("institution"):
        ids = {"bic": cp["bic"]} if cp.get("bic") else {}
        out.append(Subject("institution", Kind.INSTITUTION, cp["institution"], "counterparty institution",
                           country=cp.get("country"), identifiers=ids))
    for i, p in enumerate(req.get("positions") or []):
        ids = {"isin": p["isin"]} if p.get("isin") else {}
        out.append(Subject(f"product-{i}", Kind.PRODUCT, p.get("description", p["security_id"]), "product",
                           identifiers=ids))
    return out


def authority(req: dict, ctx: TransferContext) -> str | None:
    """Why the requester can't make this request (MM-001), or None."""
    kind, who = req["type"], (req.get("requester") or {}).get("role")
    acct = ctx.account
    direction = TYPES[kind]["direction"]
    if acct.get("status") != "OPEN":
        return "The account isn't open"
    if who == "system":
        return None if req.get("parent_case") else "Only a lifecycle case opens a request on its own authority"
    if who == "receiving_firm":
        return None if kind == "acats_out" else "A receiving firm can only request an ACATS transfer out"
    if who == "signed_form":
        return None if req.get("client_signed") else "A request by mail needs the client's signature"
    if who == "ria":
        if acct.get("group") == "self_directed":
            return "The RIA has view-only access to the self-directed account; the client moves money there"
        if not acct.get("lpoa"):
            return "The RIA has no LPOA on this account"
        return None
    if who == "client":
        if acct.get("group") != "self_directed":
            return "Money movement on a pure RIA account comes through the RIA, or by the client's signed request"
        if kind == "acats_out":
            return "An ACATS transfer out starts at the receiving firm, with the client's signature"
        if direction == "in":
            return None
        return grant_problem(req, ctx)
    return "Operations doesn't originate money movement: it comes from the RIA, the client, a signed request, or a " \
           "receiving firm"


def grant_problem(req: dict, ctx: TransferContext) -> str | None:
    """ENT-002: a client's self-service outgoing request needs an active grant, a passcode, an approved and verified
    destination, and room under the limits."""
    g = ctx.grant
    cap = CAPABILITY.get(req["type"])
    if not g or g.get("status") != "active" or g.get("capability") != cap:
        return "No active grant from the RIA for this kind of request; a signed request still works"
    if g.get("expires_on") and date.fromisoformat(g["expires_on"]) < ctx.as_of:
        return "The RIA's grant has expired"
    if not req.get("otp_verified"):
        return "The one-time passcode wasn't confirmed"
    link_id = req.get("bank_link_id")
    if req["type"] != "journal":
        link = ctx.bank_links.get(link_id)
        if not link or link.get("status") != "verified":
            return "The bank link isn't verified"
        if link_id not in (g.get("destinations") or []):
            return "The grant doesn't approve that destination"
    amt = _amount(req)
    if g.get("per_request") and amt > g["per_request"]:
        return f"Over the grant's ${g['per_request']:,.0f} per-request limit"
    today = sum(r["amount"] for r in ctx.client_requests if r.get("on") == ctx.as_of.isoformat())
    if g.get("per_day") and today + amt > g["per_day"]:
        return f"Over the grant's ${g['per_day']:,.0f} daily limit"
    return None


def risk_signals(req: dict, ctx: TransferContext) -> list[str]:
    """MM-004 pre-screen signals that need a person's risk review."""
    out = []
    kind, amt = req["type"], _amount(req)
    cp = req.get("counterparty") or {}
    owners = {_norm(n) for n in ctx.account.get("owners", [])} | {_norm(ctx.account.get("name", ""))}
    if TYPES[kind]["direction"] == "out" and TYPES[kind]["cash"] and cp.get("owner_name") \
            and _norm(cp["owner_name"]) not in owners:
        out.append(f"Third-party payee: {cp['owner_name']} isn't an owner of the account")
    if kind == "journal" and ctx.destination and ctx.destination.get("household") != ctx.account.get("household"):
        out.append(f"Journal to another owner's account ({ctx.destination.get('id')})")
    same_owner = kind == "journal" and ctx.destination and \
        ctx.destination.get("household") == ctx.account.get("household")
    if TYPES[kind]["direction"] == "out" or (kind == "journal" and not same_owner):
        days = policy.param("MM-004", "days")
        for change, on in ctx.account.get("recent_changes") or []:
            if 0 <= (ctx.as_of - date.fromisoformat(on)).days <= days:
                out.append(f"{change.replace('_', ' ').capitalize()} changed on {on}, within {days} days")
        if amt > policy.param("MM-004", "large"):
            out.append(f"Outgoing amount over ${policy.param('MM-004', 'large'):,}")
    if kind in ("acats_in", "loi_in"):
        for p in req.get("positions") or []:
            price = ctx.prices.get(p["security_id"], p.get("price") or 0)
            if 0 < price < policy.param("MM-004", "low_price"):
                out.append(f"Deposit of a low-priced security: {p.get('description', p['security_id'])} at "
                           f"${price:,.2f}")
    return out


def fixable(req: dict, ctx: TransferContext) -> list[str]:
    """Pre-screen findings an analyst can fix before submission (the AI-suggest path)."""
    out = []
    if req["type"] in ("acats_in",) and req.get("title_on_form") and \
            _norm(req["title_on_form"]) != _norm(ctx.account.get("name", "")):
        out.append(f"The transfer form reads \"{req['title_on_form']}\"; our account title is "
                   f"\"{ctx.account.get('name')}\". Suggested fix: send the form with our title, which the "
                   "delivering firm's records should match")
    return out


def _positions(req: dict) -> list[Position]:
    return [Position(p["security_id"], p.get("description", p["security_id"]), p.get("asset_type", "equity"),
                     float(p["quantity"]), float(p.get("market_value") or 0)) for p in req.get("positions") or []]


def process_transfer(req: dict, ctx: TransferContext, decisions: dict | None = None,
                     events: list[dict] | None = None) -> FlowResult:
    decisions, events = decisions or {}, events or []
    kind, spec = req["type"], TYPES[req["type"]]
    acct = ctx.account
    amt = _amount(req)
    res = FlowResult("transfer", f"{spec['label']}: {acct.get('name', acct['id'])}", acct["id"], ctx.as_of)
    who = (req.get("requester") or {}).get("role", "ria")
    res.summary = {"Type": spec["label"], "Account": f"{acct['id']} ({acct.get('name', '')})",
                   "Requested by": {"ria": "The RIA", "client": "The client (self-service)",
                                    "signed_form": "Client-signed request (by mail)",
                                    "receiving_firm": "Receiving firm (ACATS)"}.get(who, who),
                   "Rail": spec["rail"]}
    if spec["cash"]:
        res.summary["Amount"] = f"${amt:,.2f}"
    if req.get("counterparty"):
        cp = req["counterparty"]
        res.summary["Counterparty"] = ", ".join(x for x in (cp.get("owner_name"), cp.get("institution"),
                                                            f"...{cp['account_last4']}" if cp.get("account_last4")
                                                            else None) if x)
    res.add(OPS, "Transfer or disbursement request received", spec["label"],
            [f"Channel: {res.summary['Requested by']}"] + ([f"${amt:,.2f}"] if spec["cash"] else
                                                           [f"{len(req.get('positions') or [])} positions"]))

    # Pre-screen: rules stand in for the model until the AI gateway reads transfer forms (map 08)
    signals, fixes = risk_signals(req, ctx), fixable(req, ctx)
    res.add(AI, "Pre-screen: likely rejects and risk signals",
            "Clean" if not signals and not fixes else f"{len(signals) + len(fixes)} to look at",
            signals + fixes + ["Rules stand in for the model here; the AI gateway arrives with transfer-form reading"],
            ["MM-004"])

    why = authority(req, ctx)
    res.add(RULES, "Validate requester and authority", "Authorized" if not why else "Not authorized",
            [why] if why else [], ["MM-001"])
    if why:
        res.status, res.requested_items = "REJECTED", [why]
        return res

    blocking = sorted({c for a in ACTIVITY[kind] for c in acct.get("codes", []) if a in CATALOG[c].blocks})
    if kind == "journal" and ctx.destination:
        blocking += sorted({c for c in ctx.destination.get("codes", []) if Activity.DEPOSIT in CATALOG[c].blocks})
    if blocking:
        res.add(RULES, "Restriction codes", f"Blocked by {', '.join(blocking)}",
                [f"{c}: {CATALOG[c].description}" for c in blocking], ["MM-002"])
        res.status = "BLOCKED"
        return res

    if kind == "acats_out" and float(req.get("cash") or 0) > ctx.cash_available + 1e-9:
        res.status, res.requested_items = "REJECTED", [f"Only ${ctx.cash_available:,.2f} cash is available to send"]
        return res
    if spec["cash"] and spec["direction"] != "in" and not (kind == "journal" and req.get("positions")):
        if amt <= 0:
            res.status, res.requested_items = "REJECTED", ["The amount must be more than zero"]
            return res
        if amt > ctx.cash_available + 1e-9:
            res.add(RULES, "Available cash", "Not enough settled cash",
                    [f"${ctx.cash_available:,.2f} settled and unrestricted; ${amt:,.2f} requested"], ["MM-003"])
            res.status, res.requested_items = "REJECTED", [f"Only ${ctx.cash_available:,.2f} is available to send"]
            return res
    if kind in ("acats_out", "journal") and req.get("positions"):
        short = [p for p in req.get("positions") or [] if ctx.holdings.get(p["security_id"], 0) + 1e-9
                 < float(p["quantity"])]
        if short:
            res.status = "REJECTED"
            res.requested_items = [f"The account doesn't hold {p['quantity']} of {p.get('description')}" for p in short]
            return res

    # First-line check against the sanctions team's flagged list (shared subprocess)
    hits = screen(subjects(req), ctx.flagged, ctx.as_of) if ctx.flagged else []
    if hits:
        names = sorted({h.subject.name for h in hits})
        res.screening = [h.to_dict() for h in hits]
        res.escalation = {"hits": [h.to_dict() for h in hits], "target_id": acct["id"]}
        verdict = decisions.get("sanctions")
        res.add(RULES, "First-line check against the flagged list", f"Flagged: {', '.join(names)}",
                [f"{h.subject.role}: {h.subject.name} ~ {h.matched} ({h.match_type}, {h.score:.2f})" for h in hits],
                ["SCR-001"])
        if verdict is None:
            res.add(OPS, "Sanctions escalation (shared subprocess)", "Held for the sanctions team",
                    ["Nothing moves until the sanctions team decides"])
            res.status = "PENDING_SANCTIONS"
            return res
        if verdict == "true_match":
            res.add(OPS, "Sanctions escalation (shared subprocess)", "True match: transfer stopped",
                    ["The sanctions team directs what happens to the funds"])
            res.status = "BLOCKED"
            return res
        res.add(OPS, "Sanctions escalation (shared subprocess)", "False positive: released")
    else:
        res.add(RULES, "First-line check against the flagged list", "No match", [], ["SCR-001"])

    # Transferability review for assets (shared subprocess)
    loi_positions: list[dict] = []
    if not spec["cash"]:
        direction = "outgoing" if spec["direction"] == "out" else "incoming"
        review = review_transfer(_positions(req), direction, ctx.as_of, security_master=ctx.security_master)
        res.transfer = review
        waiting, declined, manual, liquidated = [], [], [], []
        keep = []
        for d in review.dispositions:
            p = next(x for x in req["positions"] if x["security_id"] == d.security_id)
            if d.route is Route.PRODUCT_ACCEPTANCE:
                state = ctx.products.get(d.security_id, "pending")
                (declined if state == "declined" else waiting).append(p)
            elif d.route is Route.REVIEW:
                manual.append((p, d.reason))
            elif d.route is Route.LOI and kind == "acats_in":
                loi_positions.append(p)
            elif d.route is Route.LIQUIDATE and kind == "acats_in":
                liquidated.append(p)             # the delivering firm sells it; the proceeds come as cash
            else:
                keep.append((p, d))
        detail = [f"{d.description}: {d.route.value.replace('_', ' ')} ({d.reason})" for d in review.dispositions]
        if declined:
            detail.append("Declined by product acceptance, so it stays at the delivering firm: "
                          + ", ".join(p.get("description", p["security_id"]) for p in declined))
        res.add(LEDGER, "Transferability review (shared subprocess)", f"Release: {review.release}", detail,
                [d.rule_id for d in review.dispositions] + ["MM-010"])
        if waiting:
            res.add(OPS, "Product acceptance", "The transfer waits for the decision",
                    [p.get("description", p["security_id"]) for p in waiting])
            res.status = "WAITING_PRODUCT"
            res.summary["Waiting for product acceptance"] = ", ".join(p["security_id"] for p in waiting)
            res.spawn += [{"key": f"product:{p['security_id']}", "kind": "product", "security_id": p["security_id"],
                           "description": p.get("description"), "asset_type": p.get("asset_type")} for p in waiting]
            return res
        if manual:
            signals += [f"Manual transfer review: {p.get('description')} ({why})" for p, why in manual]
        if loi_positions:
            res.spawn.append({"key": "loi", "kind": "transfer", "positions": loi_positions})
            res.add(LEDGER, "Non-ACATS positions", "Re-registration by LOI opens as its own transfer",
                    [p.get("description", p["security_id"]) for p in loi_positions])
        proceeds = round(sum(float(p.get("market_value") or 0) or
                             float(p["quantity"]) * ctx.prices.get(p["security_id"], 0) for p in liquidated), 2)
        if liquidated:
            res.add(LEDGER, "Positions the platform can't hold", "The delivering firm liquidates them; cash follows",
                    [f"{p.get('description', p['security_id'])}: about ${proceeds:,.2f}" for p in liquidated], ["MM-010"])
            res.summary["Cash from liquidation"] = f"${proceeds:,.2f}"
        req = {**req, "positions": [p for p, _d in keep],
               "cash": round(float(req.get("cash") or 0) + proceeds, 2)}
        res.summary["Positions moving"] = str(len(req["positions"]))
        if not req["positions"]:
            res.add(LEDGER, "Nothing left to move in kind", "Completed")
            res.status = "COMPLETED"
            return res

    # Pre-screen result: an AI-suggested fix, a human-only risk review, or clean
    if fixes and not last_event(events, "fix"):
        res.add(OPS, "Review and apply the suggested fix", "Waiting for an analyst", fixes)
        res.status = "PENDING_FIX"
        return res
    if fixes:
        fx = last_event(events, "fix")
        res.add(OPS, "Review and apply the suggested fix", "Fix applied" if fx.get("applied") else "Sent as is",
                [f"{fx.get('by')}: {fx.get('note')}"] if fx.get("note") else [f"By {fx.get('by')}"])
    if signals:
        verdict = decisions.get("risk_review")
        res.add(AML, "Risk review: third-party payee, recent changes, low-priced deposits",
                {"approved": "Approved", "rejected": "Rejected"}.get(verdict, "Waiting for compliance"), signals,
                ["MM-004"])
        if verdict is None:
            res.status = "PENDING_RISK_REVIEW"
            return res
        if verdict == "rejected":
            res.status, res.requested_items = "REJECTED", ["The risk review didn't approve this request"]
            return res

    # Controls gate
    gate = controls_gate(req, ctx, decisions, events)
    res.add(RULES, "Controls gate: dollar authority, maker-checker; RIA confirmation, client passcode and verified "
            "link, or callback", gate["outcome"], gate["detail"], ["MM-005", "MM-006"])
    if gate["status"]:
        res.status = gate["status"]
        if gate.get("items"):
            res.requested_items = gate["items"]
        res.summary.update(gate.get("summary", {}))
        return res

    return _rail(req, ctx, res, events)


def controls_gate(req: dict, ctx: TransferContext, decisions: dict, events: list[dict]) -> dict:
    """Which control releases this request, and whether it's done."""
    kind, amt = req["type"], _amount(req)
    who = (req.get("requester") or {}).get("role")
    spec = TYPES[kind]
    outgoing_cash = spec["cash"] and spec["direction"] in ("out", "internal")
    same_owner_journal = kind == "journal" and ctx.destination and \
        ctx.destination.get("household") == ctx.account.get("household")
    detail = []
    if spec["direction"] == "in":
        if kind == "ach_deposit":
            link = ctx.bank_links.get(req.get("bank_link_id"))
            if not link or link.get("status") != "verified":
                return {"status": "REJECTED", "outcome": "Bank link not verified", "detail": [],
                        "items": ["An ACH pull needs a verified bank link in the client's name"]}
            detail.append(f"Pull from a verified link: {link.get('bank')} ...{link.get('last4')}")
        return {"status": None, "outcome": "Incoming: no release needed", "detail": detail}
    if who == "system":
        return {"status": None, "outcome": f"Authorized by case #{req.get('parent_case')}",
                "detail": ["Opened by the lifecycle case that verified the instruction"]}
    if kind == "acats_out":
        return {"status": None, "outcome": "The receiving firm's request, signed by the client",
                "detail": ["We validate within 1 business day; the account freezes for the transfer"]}
    if same_owner_journal and who != "client":
        return {"status": None, "outcome": "Journal between the same owner's accounts", "detail": []}
    callback_over = policy.param("MM-006", "callback_over")
    needs_callback = who == "signed_form" or (outgoing_cash and amt > callback_over)
    straight = (who == "client" and outgoing_cash and req.get("otp_verified")
                and amt <= policy.param("MM-005", "straight_through") and not needs_callback)
    if who == "client":
        detail.append("Client passcode confirmed; destination is a verified link the RIA's grant approves")
    elif who == "ria":
        detail.append("Submitted by the RIA under its LPOA: RIA confirmation is the submission itself")
    if needs_callback:
        cb = last_event(events, "callback")
        why = "a signed request" if who == "signed_form" else f"over ${callback_over:,}"
        if not cb:
            return {"status": "PENDING_CALLBACK", "outcome": f"Callback needed ({why})",
                    "detail": detail + ["Call the client on a phone number already on file, not one on the request"],
                    "summary": {"Control": f"Callback ({why})"}}
        if not cb.get("confirmed"):
            return {"status": "REJECTED", "outcome": "Callback failed", "detail": [cb.get("note") or ""],
                    "items": ["The client didn't confirm the request on the callback"]}
        detail.append(f"Callback confirmed by {cb.get('by')} on {cb.get('on')}")
    if straight:
        detail.append(f"Within the grant and under ${policy.param('MM-005', 'straight_through'):,}: straight through")
        return {"status": None, "outcome": "Released straight through", "detail": detail}
    release = decisions.get("release")
    authority_limit = policy.param("MM-005", "analyst_authority")
    checker = "an asset transfer analyst" if amt <= authority_limit else "the operations supervisor"
    if release is None:
        return {"status": "PENDING_RELEASE", "outcome": f"Waiting for release: a maker, and {checker} as checker",
                "detail": detail + [f"Analyst dollar authority: ${authority_limit:,}"],
                "summary": {"Control": f"Maker-checker; checker: {checker}"}}
    if release == "rejected":
        return {"status": "REJECTED", "outcome": "Not released", "detail": detail,
                "items": ["Operations didn't release this request"]}
    detail.append("Released under maker-checker")
    return {"status": None, "outcome": "Released", "detail": detail}


def expected_dates(kind: str, submitted: date) -> dict:
    p = policy.BY_ID["MM-008"]["params"]
    if kind == "journal":
        return {"settle": submitted}
    if kind in ("ach_deposit", "ach_withdrawal"):
        return {"settle": add_bdays(submitted, p["ach_days"])}
    if kind == "wire_out":
        return {"settle": next_bday(submitted)}
    if kind in ("acats_in", "acats_out"):
        validate = add_bdays(submitted, p["acats_validate"])
        return {"validate": validate, "settle": bday(validate, p["acats_complete"])}
    return {"settle": add_bdays(submitted, p["loi_days"]), "follow_up": add_bdays(submitted, p["loi_follow_up"])}


def _rail(req: dict, ctx: TransferContext, res: FlowResult, events: list[dict]) -> FlowResult:
    kind = req["type"]
    acct = ctx.account
    crd = acct["firm_crd"]
    amt = _amount(req)
    n = 1 + len(events_of(events, "correction"))
    sub = last_event(events, "submitted", n=n)
    if sub is None:
        res.record["submitted"] = {"n": n, "on": ctx.as_of.isoformat()}
        submitted = ctx.as_of
    else:
        submitted = on_date(sub, ctx.as_of)
    dates = expected_dates(kind, submitted)
    res.expected = dates["settle"]
    res.summary["Submitted"] = submitted.isoformat()
    res.summary["Expected to settle"] = dates["settle"].isoformat()

    # Every earlier submission was rejected; its pending entries were reversed when the reject arrived
    for k in range(1, n):
        _post_submission(req, ctx, res, k, on_date(last_event(events, "submitted", n=k), ctx.as_of))
        rej = last_event(events, "rail", n=k, outcome="rejected")
        _post_reversal(req, ctx, res, k, on_date(rej, ctx.as_of))
    _post_submission(req, ctx, res, n, submitted)
    label = {"ACATS": "Submit the ACATS transfer (TIF)", "LOI": "Send the LOI; the sponsor re-registers",
             "Journal": "Book the internal journal", "ACH": "Release through the ACH rail",
             "Fedwire": "Release through Fedwire"}[TYPES[kind]["rail"]]
    res.add(LEDGER, "Post the pending bucket and in-transit suspense (three layers)", "Posted",
            [f"Submission {n} on {submitted.isoformat()}"], ["LED-001", "LED-002"])
    res.add(LEDGER, label, "Sent" if kind != "journal" else "Booked", [], ["MM-008"])

    if kind == "journal":
        _post_settlement(req, ctx, res, n, submitted)
        res.add(LEDGER, "Settle: clear suspense, move pending to settled (three layers)", "Settled")
        res.status = "COMPLETED"
        return res

    responses = events_of(events, "rail", n=n)
    validated = next((e for e in responses if e.get("outcome") == "validated"), None)
    confirmed = next((e for e in responses if e.get("outcome") == "confirmed"), None)
    rejected = next((e for e in responses if e.get("outcome") == "rejected"), None)
    if kind in ("acats_in", "acats_out") and validated:
        res.add(LEDGER, "Validated by the other firm" if kind == "acats_in" else "Validated by us",
                f"On {validated['on']}: the account freezes for the transfer" if kind == "acats_out"
                else f"On {validated['on']}", [], ["MM-008"])
    if rejected:
        code = rejected.get("code") or ""
        reason = RETURN_CODES.get(code) or ACATS_REJECTS.get(code) or rejected.get("reason") or "Rejected"
        _post_reversal(req, ctx, res, n, on_date(rejected, ctx.as_of))
        res.add(LEDGER, "Reject or exception received", f"{code + ': ' if code else ''}{reason}",
                [f"On {rejected['on']}; the pending entries are reversed"], ["LED-002"])
        draft = draft_correction(req, code, reason)
        res.add(AI, "Triage the exception and draft a correction", "Drafted", [draft], [])
        res.summary["Exception"] = f"{code + ': ' if code else ''}{reason}"
        res.summary["Drafted correction"] = draft
        if kind in ("ach_deposit", "ach_withdrawal") or rejected.get("final"):
            res.status = "RETURNED"
            res.requested_items = [f"{reason}. {draft}"]
            return res
        res.status = "PENDING_CORRECTION"
        return res
    if confirmed:
        settled_on = on_date(confirmed, ctx.as_of)
        _post_settlement(req, ctx, res, n, settled_on)
        res.add(LEDGER, "Settlement confirmation received", f"On {settled_on.isoformat()}", [], [])
        res.add(LEDGER, "Settle: clear suspense, move pending to settled (three layers)", "Settled", [],
                ["LED-001", "LED-002"])
        if kind == "acats_in":
            res.add(LEDGER, "Track cost basis as its own pending item",
                    "Basis received" if last_event(events, "basis") else "Waiting for the transfer statement",
                    [f"Due within {policy.param('TRN-002', 'days')} days of settlement (IRC 6045A)"], ["TRN-002"])
        returned = last_event(events, "rail", n=n, outcome="returned")
        if returned:
            code = returned.get("code") or ""
            _post_return(req, ctx, res, n, on_date(returned, ctx.as_of), settled_on)
            res.add(LEDGER, "Return received after settlement", f"{code}: {RETURN_CODES.get(code, 'Returned')}",
                    [f"On {returned['on']}; the entries are reversed"], ["MM-007"])
            res.summary["Returned"] = f"{code}: {RETURN_CODES.get(code, '')}"
            res.status = "RETURNED"
            res.requested_items = [f"{RETURN_CODES.get(code, 'Returned by the bank')} ({code})"]
            return res
        if kind == "ach_deposit":
            release_on = add_bdays(settled_on, policy.param("MM-007", "hold_days"))
            res.summary["Funds available"] = release_on.isoformat()
            if ctx.as_of >= release_on:
                res.post(f"release-{n}", "ach_hold_release", "Returnable ACH funds released",
                         rebucket(acct["id"], acct["firm_crd"], CASH, units(amt), "restricted", "settled"), release_on)
                res.add(LEDGER, "Release settled cash", f"Available on {release_on.isoformat()}", [], ["MM-007"])
            else:
                res.add(LEDGER, "Hold returnable ACH funds as restricted", f"Until {release_on.isoformat()}", [],
                        ["MM-007"])
        res.add(OPS, "Transfer complete", "Completion notice to the requester")
        res.status = "COMPLETED"
        return res
    if ctx.as_of > dates["settle"]:
        chases = events_of(events, "chase", n=n)
        res.add(OPS, "Expected settlement date passed: chase the contra party",
                f"{len(chases)} inquir{'y' if len(chases) == 1 else 'ies'} sent" if chases else "Not chased yet",
                [f"Expected {dates['settle'].isoformat()}"] + [f"{c['on']}: {c.get('note', '')}" for c in chases])
        res.status = "OVERDUE"
        return res
    if kind == "loi_in" and ctx.as_of >= dates["follow_up"]:
        res.add(OPS, "Follow up with the sponsor", f"Due {dates['follow_up'].isoformat()}", [], ["MM-008"])
    res.status = "AWAITING_REREGISTRATION" if kind == "loi_in" else "AWAITING_SETTLEMENT"
    return res


def draft_correction(req: dict, code: str, reason: str) -> str:
    if code == "title":
        return "Resubmit the transfer form with the account title exactly as the delivering firm has it, and ask " \
               "the RIA for the client's latest statement from that firm"
    if code == "ssn":
        return "Confirm the tax ID with the RIA against the prior statement; resubmit when it matches"
    if code == "nof":
        return "Ask the RIA for the client's latest statement showing the delivering account number; resubmit"
    if code in ("R02", "R03", "R04"):
        return "The bank instruction needs verifying with the client before it's used again; the money is back"
    if code == "R01":
        return "Tell the RIA the deposit came back for insufficient funds; the client can try again"
    return f"{reason}: review with the RIA and resubmit"


def _sign(legs: list, k: int = -1) -> list:
    from .ledger import Leg
    return [Leg(x.layer, x.account, x.asset, x.bucket, x.units * k) for x in legs]


def _submission_legs(req: dict, ctx: TransferContext) -> list:
    kind, acct = req["type"], ctx.account
    crd, amt = acct["firm_crd"], units(_amount(req))
    if kind == "ach_deposit":
        return client_move(acct["id"], crd, CASH, amt, "pending", "1020")
    if kind in ("ach_withdrawal", "wire_out"):
        rate = withholding(req, ctx)
        if rate:
            wh = units(_amount(req) * rate)
            from .ledger import Leg
            return client_move(acct["id"], crd, CASH, -amt, "settled", None) + \
                [Leg("gl", "2200", CASH, "settled", -(amt - wh)), Leg("gl", "2700", CASH, "settled", -wh)]
        return client_move(acct["id"], crd, CASH, -amt, "settled", "2200")
    if kind == "journal":
        dest = ctx.destination
        legs = []
        if amt:
            legs += client_move(acct["id"], crd, CASH, -amt, "settled", None) + \
                client_move(dest["id"], dest["firm_crd"], CASH, amt, "settled", None)
        for p in req.get("positions") or []:
            q = units(float(p["quantity"]))
            legs += client_move(acct["id"], crd, p["security_id"], -q, "settled", None) + \
                client_move(dest["id"], dest["firm_crd"], p["security_id"], q, "settled", None)
        return legs
    legs = []
    for p in req["positions"]:
        q = units(float(p["quantity"]))
        if kind in ("acats_in", "loi_in"):
            legs += client_move(acct["id"], crd, p["security_id"], q, "pending", "1590")
        else:
            legs += client_move(acct["id"], crd, p["security_id"], -q, "settled", "2210")
    cash = units(float(req.get("cash") or 0))
    if cash and kind == "acats_in":
        legs += client_move(acct["id"], crd, CASH, cash, "pending", "1020")
    elif cash and kind == "acats_out":
        legs += client_move(acct["id"], crd, CASH, -cash, "settled", "2200")
    return legs


def withholding(req: dict, ctx: TransferContext) -> float:
    """MM-009: the federal withholding rate on a cash distribution from a traditional IRA."""
    if req["type"] not in ("ach_withdrawal", "wire_out") or ctx.account.get("registration_type") != "ira":
        return 0.0
    if "roth" in (ctx.account.get("purpose") or "").lower():
        return 0.0
    rate = req.get("withholding_rate")
    return policy.param("MM-009", "rate") if rate is None else float(rate)


def _post_submission(req, ctx, res, n, on):
    res.post(f"submit-{n}", f"{req['type']}_pending", f"{TYPES[req['type']]['label']}: pending (submission {n})",
             _submission_legs(req, ctx), on)


def _post_reversal(req, ctx, res, n, on):
    res.post(f"reject-{n}", f"{req['type']}_reversal", f"{TYPES[req['type']]['label']}: rejected, reversed",
             _sign(_submission_legs(req, ctx)), on)


def _post_settlement(req: dict, ctx: TransferContext, res: FlowResult, n: int, on: date) -> None:
    kind, acct = req["type"], ctx.account
    crd, amt = acct["firm_crd"], units(_amount(req))
    legs = []
    if kind == "ach_deposit":
        legs = rebucket(acct["id"], crd, CASH, amt, "pending", "restricted") + gl_move("1010", "1020", CASH, amt)
    elif kind in ("ach_withdrawal", "wire_out"):
        net = amt - units(_amount(req) * withholding(req, ctx))
        legs = gl_move("2200", "1010", CASH, net)
    elif kind in ("acats_in", "loi_in"):
        for p in req["positions"]:
            q = units(float(p["quantity"]))
            loc = LOCATION_GL.get(ctx.locations.get(p["security_id"]) or
                                  ("Sponsor (non-traded)" if kind == "loi_in" else "DTC"), "1500")
            legs += rebucket(acct["id"], crd, p["security_id"], q, "pending", "settled") + \
                gl_move(loc, "1590", p["security_id"], q)
    elif kind == "acats_out":
        for p in req["positions"]:
            q = units(float(p["quantity"]))
            loc = LOCATION_GL.get(ctx.locations.get(p["security_id"]) or "DTC", "1500")
            legs += gl_move("2210", loc, p["security_id"], q)
    cash = units(float(req.get("cash") or 0))
    if cash and kind == "acats_in":
        legs += rebucket(acct["id"], crd, CASH, cash, "pending", "settled") + gl_move("1010", "1020", CASH, cash)
    elif cash and kind == "acats_out":
        legs += gl_move("2200", "1010", CASH, cash)
    res.post(f"settle-{n}", f"{kind}_settled", f"{TYPES[kind]['label']}: settled", legs, on)


def _post_return(req: dict, ctx: TransferContext, res: FlowResult, n: int, on: date, settled_on: date) -> None:
    kind, acct = req["type"], ctx.account
    crd, amt = acct["firm_crd"], units(_amount(req))
    if kind == "ach_deposit":     # the bank is debited for the return: the client's balance and GL 1010 come down
        released = on >= add_bdays(settled_on, policy.param("MM-007", "hold_days"))
        legs = client_move(acct["id"], crd, CASH, -amt, "settled" if released else "restricted", "1010")
    else:   # an ACH credit returned after it settled: the money comes back to the client
        net = amt - units(_amount(req) * withholding(req, ctx))
        wh = amt - net
        from .ledger import Leg
        legs = client_move(acct["id"], crd, CASH, amt, "settled", None) + [Leg("gl", "1010", CASH, "settled", net)]
        if wh:
            legs.append(Leg("gl", "2700", CASH, "settled", wh))
    res.post(f"return-{n}", f"{kind}_return", f"{TYPES[kind]['label']}: returned after settlement", legs, on)


def rail_response(req: dict, submitted: date, through: date, script: dict | None = None) -> list[dict]:
    """The rail simulator (principle 8): the answers a real adapter would deliver by `through` for a submission.
    A script (seeded scenarios) can make it reject or return; otherwise everything confirms on time."""
    script = script or {}
    kind = req["type"]
    dates = expected_dates(kind, submitted)
    out = []
    if script.get("reject"):
        on = dates.get("validate", dates["settle"])
        if on <= through:
            out.append({"outcome": "rejected", "code": script["reject"], "on": on.isoformat()})
        return out
    if "validate" in dates and dates["validate"] <= through:
        out.append({"outcome": "validated", "on": dates["validate"].isoformat()})
    if dates["settle"] <= through and not script.get("hold"):
        out.append({"outcome": "confirmed", "on": dates["settle"].isoformat()})
        if script.get("return"):
            ret_on = add_bdays(dates["settle"], 1)
            if ret_on <= through:
                out.append({"outcome": "returned", "code": script["return"], "on": ret_on.isoformat()})
    return out
