"""
The account lifecycle: client offboarding (map 16), deceased client accounts (map 17), RIA firm transitions (map 22),
and advisor moves and RIA mergers (map 23).

Each is a pure function of its request, the decisions people approved, the events recorded (a document checked, a
plan confirmed, a consent received), and what the app knows today, including the status of the child cases it opened
(an ACATS transfer out, the account openings and transfers of a joining RIA). Child cases are named by a stable key, so
a replay never opens one twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from restrictions import Function, RestrictionRegistry

from . import policy
from .calendar import add_bdays
from .flow import AI, AML, LEDGER, OPS, RULES, FlowResult, events_of, last_event
from .ledger import CASH, client_move, relink, units

HOLD_CODES = {"LEGAL", "FRAUD", "SANC", "OFAC", "IDFR", "EDDP", "RSCN"}
SECOND_LINE = {"SANC": "the sanctions team", "OFAC": "the sanctions team", "IDFR": "AML compliance",
               "EDDP": "AML compliance", "FRAUD": "the fraud team", "LEGAL": "legal", "RSCN": "the RIA service lead"}
DONE = {"COMPLETED", "CLOSED", "OPEN", "OPEN_RESTRICTED", "SETTLED"}


@dataclass
class AccountState:
    as_of: date
    account: dict                                   # id, name, firm_crd, group, registration_type, status, codes,
    #                                                 holders, beneficiaries, household, purpose
    balances: dict = field(default_factory=dict)    # asset -> {bucket: qty}
    prices: dict = field(default_factory=dict)      # security id -> price
    children: dict = field(default_factory=dict)    # key -> {status, completed_on, id}
    fee: dict | None = None                         # {annual_rate, timing, period_start, period_end, value}
    other_managed_open: int = 1                     # the client's other open pure RIA accounts
    self_directed_id: str | None = None             # the client's self-directed account, if any
    documents: list = field(default_factory=list)   # doc types in good order on the case
    firms: dict = field(default_factory=dict)       # crd -> {name, status}


def _settled(state: AccountState) -> dict:
    return {a: b.get("settled", 0) for a, b in state.balances.items() if abs(b.get("settled", 0)) > 1e-9}


def _total(state: AccountState) -> dict:
    return {a: sum(b.values()) for a, b in state.balances.items() if abs(sum(b.values())) > 1e-9}


def _value(state: AccountState, holdings: dict) -> float:
    return sum(q if a == CASH else q * state.prices.get(a, 0) for a, q in holdings.items())


def final_fee(fee: dict | None, as_of: date) -> tuple[float, str]:
    """OFF-002: the final pro-rated advisory fee (positive, charged) or refund (negative), with how it was figured."""
    if not fee or not fee.get("annual_rate"):
        return 0.0, "No advisory fee: the RIA doesn't bill this account"
    start, end = date.fromisoformat(fee["period_start"]), date.fromisoformat(fee["period_end"])
    rate, value = fee["annual_rate"], fee["value"]
    if fee["timing"] == "in_advance":
        unused = max(0, (end - as_of).days)
        amt = -round(value * rate * unused / 365, 2)
        return amt, f"Billed in advance through {end.isoformat()}: refund {unused} unused days at {rate:.2%} a year"
    used = max(0, (as_of - start).days + 1)
    amt = round(value * rate * used / 365, 2)
    return amt, f"Billed in arrears: {used} days since {start.isoformat()} at {rate:.2%} a year"


def _fee_legs(acct: dict, amount_: float) -> list:
    return client_move(acct["id"], acct["firm_crd"], CASH, -units(amount_), "settled", "2400")


def _child(state: AccountState, key: str) -> dict:
    return state.children.get(key) or {}


# --------------------------------------------------------------------------- client offboarding (map 16)
def process_offboarding(req: dict, state: AccountState, decisions: dict | None = None,
                        events: list[dict] | None = None) -> FlowResult:
    events = events or []
    acct = state.account
    res = FlowResult("offboarding", f"Client offboarding: {acct.get('name', acct['id'])}", acct["id"], state.as_of)
    res.registry = RestrictionRegistry()
    trigger = req.get("trigger", "ria_notice")
    res.summary = {"Account": f"{acct['id']} ({acct.get('name', '')})", "Trigger": {
        "ria_notice": "The RIA: the client is leaving", "compliance_exit": "Compliance exit decision",
        "receiving_firm": "A receiving firm's ACATS request",
        "last_managed_account": "The client's last RIA-managed account is closing"}.get(trigger, trigger)}
    res.add(OPS if trigger != "compliance_exit" else AML, "Offboarding starts", res.summary["Trigger"],
            [req.get("reason") or ""])
    closed = last_event(events, "closed")
    if not closed:
        res.registry.place(acct["id"], "OUTO", "Account closing: outgoing transfers only", "Offboarding",
                           Function.OPERATIONS)
    res.add(LEDGER, "Outgoing-only restriction code; notify the client: a new RIA on the platform, or transfer out",
            "OUTO placed" if not closed else "OUTO lifted at closing", [], ["OFF-001"])

    holds = sorted(c for c in acct.get("codes", []) if c in HOLD_CODES)
    pending = {a: b.get("pending", 0) for a, b in state.balances.items() if b.get("pending")}
    detail = ["No open orders: trading arrives in release 3"]
    if pending:
        detail.append(f"{len(pending)} pending item{'s' if len(pending) > 1 else ''} still settling")
    cash = state.balances.get(CASH, {}).get("settled", 0)
    if cash < 0:
        detail.append(f"Debit balance ${cash:,.2f}")
    detail += [f"{c} hold: resolved by {SECOND_LINE[c]}" for c in holds]
    res.add(RULES, "Closure pre-checks: open orders, pending items, debit balances, other holds",
            "Clear" if not holds else "Holds to resolve", detail, ["OFF-001"])
    if holds:
        res.add(AML, "Resolve hold: garnishment, levy, court order, AML (sanctions holds stay with the sanctions team)",
                "Waiting", [f"{c}: {SECOND_LINE[c]} removes it when resolved" for c in holds])
        res.status = "PENDING_HOLD_RESOLUTION"
        return res

    fee, how = final_fee(state.fee, state.as_of) if not last_event(events, "fee") else \
        (last_event(events, "fee")["amount"], last_event(events, "fee")["how"])
    if not last_event(events, "fee"):
        res.record["fee"] = {"amount": fee, "how": how}
    fee_on = date.fromisoformat(last_event(events, "fee")["on"]) if last_event(events, "fee") else state.as_of
    if fee:
        res.post("final-fee", "advisory_fee_final" if fee > 0 else "advisory_fee_refund",
                 "Final pro-rated advisory fee" if fee > 0 else "Refund of the unused advance fee",
                 _fee_legs(acct, fee), fee_on)
    res.add(LEDGER, "Calculate the final pro-rated advisory fee (refund if billed in advance)",
            f"${abs(fee):,.2f} {'charged' if fee > 0 else 'refunded'}" if fee else "None", [how], ["OFF-002"])

    moved = last_event(events, "moved")
    if moved:
        res.add(OPS, "The client picked an RIA on the platform", f"Advisor move to {moved.get('firm')}",
                [moved.get("note") or ""], ["ADV-001"])
        res.status = "MOVED"
        return res
    tif = last_event(events, "tif") or ({"receiving_firm": req.get("receiving_firm"), "on": state.as_of.isoformat()}
                                         if trigger == "receiving_firm" else None)
    if not tif:
        res.add(LEDGER, "Transfer request from the receiving firm", "Waiting for the TIF")
        res.status = "AWAITING_TRANSFER"
        return res
    res.summary["Receiving firm"] = tif.get("receiving_firm") or ""
    holdings = _settled(state) if not _child(state, "acats_out") else (req.get("transferred") or {})
    if not _child(state, "acats_out"):
        positions = [{"security_id": a, "quantity": q} for a, q in holdings.items() if a != CASH and q > 0]
        res.spawn.append({"key": "acats_out", "kind": "transfer", "type": "acats_out",
                          "receiving_firm": tif.get("receiving_firm"), "positions": positions,
                          "cash": max(0.0, holdings.get(CASH, 0))})
    child = _child(state, "acats_out")
    res.add(LEDGER, "Transferability review, outgoing (shared subprocess); transfer assets", child.get("status", "Opened"),
            [f"Transfer case #{child['id']}" if child.get("id") else "Opening the ACATS transfer out"], ["MM-010"])
    if child.get("status") != "COMPLETED":
        res.status = "TRANSFERRING"
        return res

    done_on = date.fromisoformat(child["completed_on"]) if child.get("completed_on") else state.as_of
    window_end = done_on + timedelta(days=policy.param("OFF-003", "residual_days"))
    residual = _total(state)
    res.summary["Residual window ends"] = window_end.isoformat()
    approved = events_of(events, "residual")
    for i, r in enumerate(approved, 1):
        _post_residual(res, acct, r, i)
    if residual and not _residual_settled(residual, approved):
        proposal = propose_residual(residual, tif.get("receiving_firm"), state.as_of <= window_end, state.prices)
        res.add(AI, "Identify residual items and propose a disposition", "Proposed", proposal["lines"], ["OFF-003"])
        res.summary["Proposed disposition"] = "; ".join(proposal["lines"])
        res.status = "PENDING_RESIDUAL"
        return res
    if state.as_of < window_end and not closed:
        res.add(LEDGER, "Residual monitoring window", f"Open until {window_end.isoformat()}",
                ["Residual credits (a late dividend or interest) follow the client to the receiving firm"], ["OFF-003"])
        res.status = "RESIDUAL_WINDOW"
        return res
    res.add(RULES, "Verify zero balances at client, RIA master, and GL", "Zero", [], ["OFF-003", "LED-001"])
    res.add(LEDGER, "Close the account: revoke RIA authority and client grants; end instructions", "Closed")
    res.add(LEDGER, "Retain records; flag final-year tax reporting (1099s, 1099-R)", "Flagged")
    if not closed:
        res.record["closed"] = {"on": state.as_of.isoformat()}
        res.effects.append({"type": "close_account", "account_id": acct["id"]})
        if acct.get("group") == "pure_ria" and state.other_managed_open == 0 and state.self_directed_id:
            res.spawn.append({"key": "self_directed", "kind": "offboarding", "account_id": state.self_directed_id,
                              "trigger": "last_managed_account"})
    res.registry = RestrictionRegistry()          # nothing stays on a closed account
    res.status = "CLOSED"
    return res


def propose_residual(residual: dict, receiving_firm: str | None, in_window: bool, prices: dict) -> dict:
    lines, legs = [], []
    for asset, qty in residual.items():
        what = f"${qty:,.2f} cash" if asset == CASH else f"{qty:g} of {asset}"
        if in_window and receiving_firm:
            lines.append(f"{what}: deliver to {receiving_firm} as a residual credit")
        else:
            lines.append(f"{what}: undeliverable; move to the unclaimed-property liability for escheatment")
    return {"lines": lines, "deliver": bool(in_window and receiving_firm)}


def _residual_settled(residual: dict, approved: list[dict]) -> bool:
    return bool(approved) and all(abs(q) < 1e-9 for q in residual.values())


def _post_residual(res: FlowResult, acct: dict, r: dict, i: int) -> None:
    legs = []
    for asset, qty in (r.get("items") or {}).items():
        if not qty:
            continue
        if asset == CASH:
            contra = "2200" if r.get("deliver") else "2600"
            legs += client_move(acct["id"], acct["firm_crd"], CASH, -units(qty), "settled", contra)
            if r.get("deliver"):
                from .ledger import gl_move
                legs += gl_move("2200", "1010", CASH, units(qty))
        else:
            legs += client_move(acct["id"], acct["firm_crd"], asset, -units(qty), "settled",
                                "2210" if r.get("deliver") else "2600")
            if r.get("deliver"):
                from .ledger import gl_move
                legs += gl_move("2210", "1500", asset, units(qty))
    res.post(f"residual-{i}", "residual", "Residual delivered to the receiving firm" if r.get("deliver")
             else "Residual moved to unclaimed property", legs, date.fromisoformat(r["on"]))


# --------------------------------------------------------------------------- deceased client account (map 17)
def ownership(account: dict) -> tuple[str | None, list[str]]:
    """DEC-002: who takes the account when ownership is clear, or (None, why not)."""
    reg = account.get("registration_type")
    holders = [h for h in account.get("holders", []) if not h.get("deceased")]
    if reg == "joint" and holders:
        return "surviving joint owner", [h["name"] for h in holders]
    bens = account.get("beneficiaries") or []
    if isinstance(bens, str):
        bens = [bens]
    if bens:
        kind = "IRA beneficiary designation" if reg == "ira" else "TOD designation"
        return kind, [b if isinstance(b, str) else b.get("name", "") for b in bens]
    return None, ["No surviving joint owner and no beneficiary or TOD designation: the estate process applies"]


def process_deceased(req: dict, state: AccountState, decisions: dict | None = None,
                     events: list[dict] | None = None) -> FlowResult:
    decisions, events = decisions or {}, events or []
    acct = state.account
    res = FlowResult("deceased", f"Deceased client account: {acct.get('name', acct['id'])}", acct["id"], state.as_of)
    res.registry = RestrictionRegistry()
    res.summary = {"Account": f"{acct['id']} ({acct.get('name', '')})", "Reported by": req.get("reported_by", ""),
                   "Date of death": req.get("date_of_death", "")}
    res.add(OPS, "Death notification received (family, estate representative, or RIA)", req.get("reported_by", ""),
            [req.get("note") or ""])
    verified = last_event(events, "death_verified")
    settled = last_event(events, "settled")
    if not verified:
        res.registry.place(acct["id"], "DTHN", "Death notice received; certificate requested", "Deceased accounts",
                           Function.OPERATIONS)
    res.add(LEDGER, "Restrict on notice: deceased hold; the RIA's trading authority stops", "DTHN placed", [],
            ["DEC-001"])
    res.add(OPS, "Request the death certificate; inform the RIA", "Requested")
    if "death_certificate" not in state.documents and not verified:
        res.status, res.requested_items = "AWAITING_CERTIFICATE", ["A certified copy of the death certificate"]
        return res
    if not verified:
        res.add(RULES, "Death verified?", "Waiting for operations to check the certificate")
        res.status = "PENDING_DEATH_VERIFICATION"
        return res
    if not verified.get("verified"):
        res.add(RULES, "Death verified?", "Not verified", [verified.get("note") or ""], ["DEC-001"])
        res.add(LEDGER, "Remove the hold", "DTHN removed")
        res.registry = RestrictionRegistry()
        res.status = "NOT_VERIFIED"
        return res
    res.add(RULES, "Death verified?", "Verified", [f"By {verified.get('by')} on {verified.get('on')}"], ["DEC-001"])
    if not settled:
        res.registry.place(acct["id"], "DECD", "Owner deceased; death certificate verified", "Deceased accounts",
                           Function.OPERATIONS)
    res.add(LEDGER, "Full lockdown: deceased restriction code; the RIA's LPOA has ended",
            "DECD placed" if not settled else "Lifted at re-registration", [], ["DEC-001"])
    if not last_event(events, "lpoa_ended"):
        res.record["lpoa_ended"] = {"on": state.as_of.isoformat()}
        res.effects.append({"type": "end_lpoa", "account_id": acct["id"]})
    kind, who = ownership(acct)
    res.add(AI, "Extract the death certificate and ownership documents; identify beneficiaries, TOD, joint survivors",
            kind or "No clear ownership", who, ["DEC-002"])
    claimant = None
    if kind:
        claim = last_event(events, "claim_verified")
        if not claim:
            res.add(OPS, "Verify claim documents for the beneficiary or surviving owner", "Waiting", who)
            res.status = "PENDING_CLAIM_REVIEW"
            if "claim_form" not in state.documents:
                res.requested_items = [f"Claim form and ID from {', '.join(who)}"]
            return res
        claimant = claim.get("claimant") or who[0]
        res.add(OPS, "Verify claim documents for the beneficiary or surviving owner", f"Verified: {claimant}",
                [f"By {claim.get('by')}"], ["DEC-002"])
    else:
        res.add(AML, "Notify the family and the RIA: estate process required under state law", "Sent")
        if not ({"letters_testamentary", "small_estate_affidavit"} & set(state.documents)):
            res.status = "AWAITING_ESTATE_DOCS"
            res.requested_items = ["Letters testamentary or of administration, or small-estate documents"]
            return res
        verdict = decisions.get("estate_docs")
        if verdict is None:
            res.add(AML, "Verify the legal documents; identify the executor or administrator", "Waiting for review")
            res.status = "PENDING_LEGAL_REVIEW"
            return res
        if verdict == "rejected":
            res.add(AML, "Verify the legal documents; identify the executor or administrator", "Not in good order")
            res.status, res.requested_items = "AWAITING_ESTATE_DOCS", ["New legal documents: the ones sent weren't "
                                                                       "in good order"]
            return res
        claimant = req.get("executor") or "the estate"
        res.add(AML, "Verify the legal documents; identify the executor or administrator", f"Executor: {claimant}",
                [], ["DEC-002"])
    holdings = _settled(state)
    dod = {a: round(q * state.prices.get(a, 1 if a == CASH else 0), 2) for a, q in holdings.items()}
    res.summary["Date-of-death value"] = f"${sum(dod.values()):,.2f}"
    res.add(LEDGER, "Apply date-of-death valuation to cost basis", f"${sum(dod.values()):,.2f}",
            [f"{a}: ${v:,.2f}" for a, v in sorted(dod.items())][:12] +
            ["Prices are the platform's latest synthetic prices standing in for date-of-death closes"], ["DEC-003"])
    res.add(LEDGER, "Re-register to the beneficiary, survivor, or estate; lift the lockdown", f"To {claimant}")
    dist = last_event(events, "distribution")
    if not dist:
        res.add(RULES, "Distribution?", "Waiting for the new owner's instruction",
                ["A journal to an account in the new owner's name, or a transfer out"])
        res.status = "PENDING_DISTRIBUTION"
        return res
    key = "distribution"
    if not _child(state, key) and not settled:
        positions = [{"security_id": a, "quantity": q} for a, q in holdings.items() if a != CASH and q > 0]
        if dist["method"] == "new_account":
            res.spawn.append({"key": key, "kind": "transfer", "type": "journal", "destination": dist["account_id"],
                              "positions": positions, "amount": max(0.0, holdings.get(CASH, 0)),
                              "requester": "estate"})
        else:
            res.spawn.append({"key": key, "kind": "transfer", "type": "acats_out", "positions": positions,
                              "cash": max(0.0, holdings.get(CASH, 0)), "receiving_firm": dist.get("receiving_firm")})
    child = _child(state, key)
    res.add(LEDGER, "Journal assets to the new owner's account; the RIA manages it only under a new agreement"
            if dist["method"] == "new_account" else "Transferability review and transfer out (shared subprocesses)",
            child.get("status", "Opened"), [f"Transfer case #{child['id']}" if child.get("id") else ""])
    if child.get("status") != "COMPLETED" and not settled:
        res.status = "DISTRIBUTING"
        return res
    res.add(LEDGER, "Close the deceased account; retain records; final tax reporting", "Closed")
    if not settled:
        res.record["settled"] = {"on": state.as_of.isoformat()}
        res.effects.append({"type": "close_account", "account_id": acct["id"]})
    res.registry = RestrictionRegistry()
    res.status = "SETTLED"
    return res


# --------------------------------------------------------------------------- advisor moves (map 23)
def process_advisor_move(req: dict, state: AccountState, decisions: dict | None = None,
                         events: list[dict] | None = None) -> FlowResult:
    decisions, events = decisions or {}, events or []
    acct = state.account
    new = state.firms.get(req["new_firm_crd"], {})
    old = state.firms.get(req.get("old_firm_crd") or acct["firm_crd"], {})
    res = FlowResult("advisor_move", f"Advisor move: {acct.get('name', acct['id'])} to {new.get('name', '')}",
                     acct["id"], state.as_of)
    res.summary = {"Account": f"{acct['id']} ({acct.get('name', '')})", "From": old.get("name", ""),
                   "To": new.get("name", ""), "Advisor": req.get("advisor", "")}
    res.add(OPS, "Client-signed LPOA naming a new RIA", "Received")
    missing = [x for x, ok in (("A client-signed LPOA naming the new RIA", req.get("lpoa_signed")),
                               ("The client's signature on the LPOA matches the account record",
                                req.get("signature_ok", req.get("lpoa_signed")))) if not ok]
    res.add(AI, "Check the LPOA and signatures; categorize NIGO", "In good order" if not missing else "NIGO",
            missing + ["Rules stand in for the model here"], ["ADV-001"])
    if missing:
        res.status, res.requested_items = "PENDING_ITEMS", missing
        return res
    problems = []
    if new.get("status") != "ACTIVE":
        problems.append(f"{new.get('name', 'The new RIA')} isn't onboarded on the platform yet")
    if not req.get("advisor_registered", True):
        problems.append(f"{req.get('advisor')} isn't registered with {new.get('name')} (IAPD)")
    res.add(RULES, "Check the new RIA is onboarded and the advisor is registered with it",
            "Yes" if not problems else "Not yet", problems, ["ADV-001"])
    if problems:
        res.status, res.requested_items = "PENDING_ITEMS", problems
        return res
    if req.get("dispute") or req.get("court_order"):
        verdict = decisions.get("legal_hold")
        res.add(RULES, "Dispute or court order on file?", "Yes",
                [req.get("dispute_note") or "A documented dispute or court order"], ["ADV-001"])
        if verdict is None:
            res.add(AML, "Hold; follow the client's signed instruction or the court order", "Waiting for legal")
            res.status = "PENDING_LEGAL_HOLD"
            return res
        if verdict == "hold":
            res.add(AML, "Hold; follow the client's signed instruction or the court order", "The court order holds it")
            res.status = "BLOCKED"
            return res
        res.add(AML, "Hold; follow the client's signed instruction or the court order",
                "Follow the client's signed instruction", [], ["ADV-001"])
    else:
        res.add(RULES, "Dispute or court order on file?", "No", [], ["ADV-001"])
    _relink_steps(res, state, acct, req["new_firm_crd"], req.get("rep_code"), events, "relink")
    res.add(OPS, "Notify the client, the old RIA, and the new RIA", "Sent")
    res.status = "COMPLETED"
    return res


def _relink_steps(res: FlowResult, state: AccountState, acct: dict, new_crd: str, rep_code: str | None,
                  events: list[dict], ref: str) -> None:
    done = last_event(events, ref)
    old_crd = done["old_firm_crd"] if done else acct["firm_crd"]
    on = date.fromisoformat(done["on"]) if done else state.as_of
    bal = {(a, b): units(q) for a, buckets in state.balances.items() for b, q in buckets.items() if q}
    if not done:
        res.record[ref] = {"on": state.as_of.isoformat(), "old_firm_crd": old_crd, "balances": {
            f"{a}|{b}": u for (a, b), u in bal.items()}}
    else:
        bal = {tuple(k.split("|")): u for k, u in done["balances"].items()}
    legs = relink(acct["id"], old_crd, new_crd, bal)
    if legs:
        res.post(ref, "relink", f"{acct['id']} moves from MASTER-{old_crd} to MASTER-{new_crd}; no assets move",
                 legs, on)
        res.entries[-1]["relink"] = (acct["id"], old_crd, new_crd)
    if not done:
        res.effects.append({"type": "relink", "account_id": acct["id"], "firm_crd": new_crd, "rep_code": rep_code})
    res.add(LEDGER, "Re-link to the new RIA's master and rep code; old LPOA revoked; no assets move",
            f"MASTER-{old_crd} to MASTER-{new_crd}" + (f", rep code {rep_code}" if rep_code else ""), [], ["ADV-001"])
    res.add(LEDGER, "Fee cutover: the old RIA bills through the effective date, the new RIA from the re-link",
            f"Effective {on.isoformat()}", [], ["ADV-002"])
    res.summary["Effective"] = on.isoformat()


# --------------------------------------------------------------------------- RIA mergers (map 23)
def process_merger(req: dict, state: AccountState, decisions: dict | None = None,
                   events: list[dict] | None = None) -> FlowResult:
    """state.account here is the firm: {id: FIRM-crd, name, firm_crd}; req.accounts lists its client accounts."""
    decisions, events = decisions or {}, events or []
    firm = state.account
    acquirer = state.firms.get(req.get("acquirer_crd") or "", {})
    res = FlowResult("merger", f"RIA merger or rebrand: {firm.get('name')}", firm["id"], state.as_of)
    res.summary = {"Firm": firm.get("name", ""), "Notice": req.get("notice", "")}
    res.add(AML, "Merger, acquisition, succession, or rebrand notice", "Received", [req.get("notice") or ""])
    res.add(AI, "Extract deal terms, Form ADV changes, and account lists", "Extracted",
            [f"{len(req.get('accounts') or [])} accounts", "Rules stand in for the model here"])
    kind = decisions.get("merger_classification")
    if kind is None:
        res.add(AML, "Classify: change of control (needs consent) or a rebrand or reorganization", "Waiting", [],
                ["MRG-001"])
        res.status = "PENDING_CLASSIFICATION"
        return res
    res.add(AML, "Classify: change of control (needs consent) or a rebrand or reorganization",
            "Change of control: an assignment" if kind == "change_of_control" else "Rebrand or reorganization",
            [], ["MRG-001"])
    if kind == "rebrand":
        res.add(LEDGER, "Update the firm's name and records; LPOAs and masters stay",
                f"Now {req.get('new_name') or firm.get('name')}", [], ["MRG-001"])
        if not last_event(events, "renamed"):
            res.record["renamed"] = {"on": state.as_of.isoformat()}
            res.effects.append({"type": "rename_firm", "firm_crd": firm["firm_crd"], "name": req.get("new_name")})
        res.status = "RECORDS_UPDATED"
        return res
    res.summary["Acquirer"] = acquirer.get("name", req.get("acquirer_name", ""))
    if acquirer.get("status") != "ACTIVE":
        res.add(OPS, "RIA firm onboarding for the acquirer, if new", "Waiting for onboarding")
        res.status = "WAITING_ACQUIRER"
        return res
    res.add(OPS, "RIA firm onboarding for the acquirer, if new", "Onboarded")
    if req.get("method") == "negative":
        pkg = decisions.get("negative_consent")
        if pkg is None:
            res.add(AML, "Approve the negative-consent package", "Waiting", [], ["MRG-001"])
            res.status = "PENDING_CONSENT_PACKAGE"
            return res
        if pkg == "rejected":
            res.add(AML, "Approve the negative-consent package", "Not approved: affirmative consent needed")
    deadline = date.fromisoformat(req["consent_deadline"])
    negative_ok = req.get("method") == "negative" and decisions.get("negative_consent") == "approved"
    res.summary["Consent"] = "Negative consent (approved package)" if negative_ok else "New client-signed LPOAs"
    res.summary["Deadline"] = deadline.isoformat()
    consents = {e["account_id"]: e for e in events_of(events, "consent")}
    lines, open_ = [], 0
    for a in req.get("accounts") or []:
        c = consents.get(a)
        if c and c.get("answer") == "objected":
            lines.append(f"{a}: objected: the client picks another RIA or transfers out")
            if not _child(state, f"offboard:{a}"):
                res.spawn.append({"key": f"offboard:{a}", "kind": "offboarding", "account_id": a,
                                  "trigger": "ria_notice", "reason": "Objected to the change of control"})
        elif (c and c.get("answer") == "consented") or (negative_ok and state.as_of > deadline):
            lines.append(f"{a}: {'consented' if c else 'no objection by the deadline'}: re-link to the acquirer")
            done = last_event(events, f"relink:{a}")
            if not done:
                res.record[f"relink:{a}"] = {"on": state.as_of.isoformat(), "account_id": a}
                res.effects.append({"type": "relink", "account_id": a, "firm_crd": req["acquirer_crd"],
                                    "rep_code": (req.get("rep_codes") or {}).get(a)})
        else:
            open_ += 1
            lines.append(f"{a}: waiting for consent")
    res.add(OPS, "Collect consent: new LPOAs or an approved negative-consent package",
            f"{len(req.get('accounts') or []) - open_} of {len(req.get('accounts') or [])} answered", lines,
            ["MRG-001"])
    if open_ and state.as_of <= deadline:
        res.status = "COLLECTING_CONSENT"
        return res
    if open_:
        res.add(RULES, "Consented, or no objection by the deadline?", "Deadline passed without consent",
                [f"{open_} accounts: the client picks another RIA or transfers out"])
        for a in req.get("accounts") or []:
            if a not in consents and not negative_ok and not _child(state, f"offboard:{a}"):
                res.spawn.append({"key": f"offboard:{a}", "kind": "offboarding", "account_id": a,
                                  "trigger": "ria_notice", "reason": "No consent to the change of control"})
    res.add(LEDGER, "Re-link accounts to the acquirer's master; map rep codes; fee cutover", "Done", [], ["ADV-002"])
    res.status = "COMPLETED"
    return res


# --------------------------------------------------------------------------- RIA firm transitions (map 22)
def process_transition(req: dict, state: AccountState, decisions: dict | None = None,
                       events: list[dict] | None = None) -> FlowResult:
    events = events or []
    firm = state.account                  # {id: FIRM-crd, name, firm_crd, status}
    join = req.get("direction") == "join"
    res = FlowResult("transition", f"RIA {'joining' if join else 'leaving'}: {firm.get('name')}", firm["id"],
                     state.as_of)
    res.summary = {"Firm": firm.get("name", ""), "Direction": "Joining with its book" if join else "Leaving",
                   "Target date": req.get("target_date", "")}
    return _join(req, state, res, events) if join else _leave(req, state, res, events)


def _join(req: dict, state: AccountState, res: FlowResult, events: list[dict]) -> FlowResult:
    firm = state.account
    accounts = [a for b in req.get("batches", []) for a in b["accounts"]]
    res.add(OPS, "RIA joins with its book", f"{len(accounts)} accounts in {len(req.get('batches', []))} batches")
    if firm.get("status") != "ACTIVE":
        res.add(OPS, "RIA firm onboarding, if the firm is new", "Waiting for onboarding")
        res.status = "WAITING_ONBOARDING"
        return res
    res.add(OPS, "RIA firm onboarding, if the firm is new", "Onboarded")
    plan = last_event(events, "plan_confirmed")
    if not plan:
        res.add(OPS, "Plan the move: account list, target date, transfer batches, fee schedules", "Waiting",
                [f"Batch {b['n']}: {len(b['accounts'])} accounts" for b in req.get("batches", [])], ["TRN-001"])
        res.status = "PLANNING"
        return res
    res.add(OPS, "Plan the move: account list, target date, transfer batches, fee schedules",
            f"Confirmed {plan['on']}", [], ["TRN-001"])
    opened, opening = [], []
    for a in accounts:
        key = f"open:{a['key']}"
        child = _child(state, key)
        if not child:
            res.spawn.append({"key": key, "kind": "account", "application": a["application"]})
        (opened if child.get("status") in ("OPEN", "OPEN_RESTRICTED") else opening).append(a)
    res.add(OPS, "Open each client account (account opening)", f"{len(opened)} of {len(accounts)} open",
            [f"{a['name']}: {_child(state, 'open:' + a['key']).get('status', 'opening')}" for a in accounts],
            ["TRN-001"])
    if opened:
        res.add(LEDGER, "Link accounts to the RIA master and rep codes; set fee schedules", f"{len(opened)} linked")
    released = {e["n"] for e in events_of(events, "batch_released")}
    transferring = 0
    for b in req.get("batches", []):
        for a in b["accounts"]:
            if b["n"] not in released or a not in opened:
                continue
            key = f"acats:{a['key']}"
            if not _child(state, key):
                res.spawn.append({"key": key, "kind": "transfer", "type": "acats_in", "account_key": f"open:{a['key']}",
                                  "positions": a["positions"], "title_on_form": a.get("title_on_form"),
                                  "contra_firm": a.get("prior_custodian")})
            if _child(state, key).get("status") != "COMPLETED":
                transferring += 1
    res.add(AI, "Read prior-custodian statements; flag likely ACATS rejects and non-transferable assets",
            "Flagged with the transferability rules", ["Rules stand in for the model here"], ["MM-010"])
    if opening and not released:
        res.status = "OPENING_ACCOUNTS"
        return res
    pending_batches = [b["n"] for b in req.get("batches", []) if b["n"] not in released]
    res.add(OPS, "Bulk incoming transfers in batches (transfers process)",
            f"Released: {', '.join(map(str, sorted(released))) or 'none'}; "
            f"waiting: {', '.join(map(str, pending_batches)) or 'none'}", [f"{transferring} transfers in flight"])
    if pending_batches or transferring or opening:
        res.status = "TRANSFERRING"
        return res
    basis = {e["account"]: e for e in events_of(events, "basis")}
    missing = [a["key"] for a in accounts if a["key"] not in basis]
    breaks = [f"{k}: {e.get('breaks')}" for k, e in basis.items() if e.get("breaks") and not e.get("resolved")]
    res.add(LEDGER, "Cost-basis statements received (due within 15 days of settlement)",
            f"{len(accounts) - len(missing)} of {len(accounts)}", [], ["TRN-002"])
    if missing:
        res.status = "PENDING_BASIS"
        res.requested_items = [f"Cost-basis transfer statement for {k}" for k in missing]
        return res
    res.add(AI, "Reconcile positions and cost basis against prior statements; flag breaks",
            "Breaks to resolve" if breaks else "Matched", breaks)
    if breaks:
        res.status = "PENDING_BASIS"
        return res
    res.add(LEDGER, "Start billing from each account's funding date (pro-rated first period)", "Started", [],
            ["TRN-003"])
    if not last_event(events, "billing_started"):
        res.record["billing_started"] = {"on": state.as_of.isoformat()}
    res.status = "COMPLETED"
    return res


def _leave(req: dict, state: AccountState, res: FlowResult, events: list[dict]) -> FlowResult:
    accounts = req.get("accounts") or []
    res.add(OPS, "RIA gives notice it is leaving", f"{len(accounts)} accounts; receiving custodian "
            f"{req.get('receiving_custodian', '')}")
    plan = last_event(events, "plan_confirmed")
    if not plan:
        res.add(OPS, "Confirm the plan with the RIA: receiving custodian, date, accounts", "Waiting")
        res.status = "CONFIRMING_PLAN"
        return res
    res.add(OPS, "Confirm the plan with the RIA: receiving custodian, date, accounts", f"Confirmed {plan['on']}")
    fees = req.get("fees") or {}
    for i, a in enumerate(accounts, 1):
        amt = fees.get(a["account_id"])
        if amt:
            res.post(f"final-fee-{a['account_id']}", "advisory_fee_final", f"Final pro-rated fee: {a['account_id']}",
                     _fee_legs({"id": a["account_id"], "firm_crd": state.account["firm_crd"]}, amt),
                     date.fromisoformat(plan["on"]))
    res.add(LEDGER, "Deduct final pro-rated fees before transfers validate",
            f"{sum(1 for a in accounts if fees.get(a['account_id']))} accounts", [], ["OFF-002"])
    forms = {e["account_id"]: e for e in events_of(events, "forms")}
    deadline = date.fromisoformat(req["deadline"])
    waiting, leftover = 0, 0
    lines = []
    for a in accounts:
        aid = a["account_id"]
        if aid in forms:
            key = f"acats_out:{aid}"
            if not _child(state, key):
                res.spawn.append({"key": key, "kind": "transfer", "type": "acats_out", "account_id": aid,
                                  "receiving_firm": req.get("receiving_custodian"), "full": True})
            status = _child(state, key).get("status", "opening")
            waiting += status != "COMPLETED"
            lines.append(f"{aid}: transfer {status}")
        elif state.as_of > deadline:
            key = f"offboard:{aid}"
            if not _child(state, key):
                res.spawn.append({"key": key, "kind": "offboarding", "account_id": aid, "trigger": "ria_notice",
                                  "reason": "Left behind when the RIA moved"})
            status = _child(state, key).get("status", "opening")
            leftover += status not in ("CLOSED", "MOVED")
            lines.append(f"{aid}: offboarding {status}")
        else:
            waiting += 1
            lines.append(f"{aid}: waiting for the client-signed transfer form")
    res.add(LEDGER, "Client-signed transfer forms arrive; outgoing transferability review and transfers",
            f"{len(forms)} of {len(accounts)} forms", lines, ["MM-010"])
    if waiting:
        res.status = "AWAITING_FORMS"
        return res
    if leftover:
        res.add(RULES, "Accounts left after the deadline?", f"{leftover} in offboarding")
        res.status = "OFFBOARDING_REMAINDER"
        return res
    res.add(LEDGER, "End the RIA's authority and fee deduction; deactivate its users and master", "Done")
    ended = last_event(events, "authority_ended")
    if not ended:
        res.record["authority_ended"] = {"on": state.as_of.isoformat()}
        res.effects.append({"type": "offboard_firm", "firm_crd": state.account["firm_crd"]})
    ended_on = date.fromisoformat(ended["on"]) if ended else state.as_of
    months = policy.param("TRN-004", "months")
    sweep_end = ended_on + timedelta(days=30 * months)
    res.add(LEDGER, f"Sweep residual credits to the receiving firms for at least {months} months",
            f"Until {sweep_end.isoformat()}", [], ["TRN-004"])
    res.status = "RESIDUAL_SWEEP" if state.as_of < sweep_end else "COMPLETED"
    return res
