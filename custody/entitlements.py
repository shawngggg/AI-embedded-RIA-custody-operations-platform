"""
Client entitlements (map 05): the RIA grants or revokes self-service capabilities on the client's self-directed
account, with limits, approved destinations, and an expiry; money movement waits for a verified bank link; every
client request then needs a one-time passcode (money.grant_problem). The RIA is alerted to unusual client activity.

Defaults: a pure RIA account has no client capabilities; the self-directed account has client trading and incoming
cash and assets. Options levels, margin, and IRA limited margin stay custodian approvals and aren't granted here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from restrictions import CATALOG, Activity

from . import policy
from .calendar import bdays_between
from .flow import LEDGER, OPS, RULES, FlowResult, last_event

CAPABILITIES = {
    "ach_out": {"label": "Outgoing ACH to a verified bank link", "money": True, "activity": Activity.WITHDRAWAL},
    "wire_out": {"label": "Outgoing wires to a verified bank link", "money": True, "activity": Activity.WITHDRAWAL},
    "journal_out": {"label": "Journals to the client's other accounts", "money": False,
                    "activity": Activity.WITHDRAWAL},
}
CUSTODIAN_ONLY = {"margin": "Margin is a custodian approval, not an RIA grant",
                  "options": "Options levels are a custodian approval (FINRA Rule 2360)",
                  "short_selling": "Short selling needs margin, a custodian approval"}


@dataclass
class GrantContext:
    as_of: date
    account: dict                          # id, name, group, registration_type, status, codes, firm_crd
    requester_firm: str | None = None      # the RIA user's firm CRD
    bank_links: dict = field(default_factory=dict)   # id -> {bank, last4, owner, status}


def grant_problems(req: dict, ctx: GrantContext) -> list[str]:
    """ENT-001: why this grant can't be recorded."""
    acct, out = ctx.account, []
    cap = req.get("capability")
    if ctx.requester_firm and ctx.requester_firm != acct.get("firm_crd"):
        out.append("The account belongs to another RIA")
    if acct.get("group") != "self_directed":
        out.append("Client capabilities go on the self-directed account only; the RIA moves money on pure RIA accounts")
    if acct.get("status") != "OPEN":
        out.append("The account isn't open")
    if cap in CUSTODIAN_ONLY:
        out.append(CUSTODIAN_ONLY[cap])
        return out
    if cap not in CAPABILITIES:
        out.append(f"Unknown capability {cap}")
        return out
    if acct.get("registration_type") == "ira" and cap in ("margin", "short_selling"):
        out.append("Retirement accounts get no borrowing or short selling")
    blocking = [c for c in acct.get("codes", []) if CAPABILITIES[cap]["activity"] in CATALOG[c].blocks]
    if blocking:
        out.append(f"Blocked by {', '.join(blocking)}: no grant while the code is in place")
    expires = req.get("expires_on")
    if not expires:
        out.append("A grant needs an expiry")
    else:
        e = date.fromisoformat(expires)
        if e <= ctx.as_of:
            out.append("The expiry must be in the future")
        elif e > ctx.as_of + timedelta(days=policy.param("ENT-001", "max_days")):
            out.append("A grant can run a year at most")
    if not req.get("per_request") or not req.get("per_day"):
        out.append("A grant needs a per-request and a daily limit")
    elif req["per_request"] > req["per_day"]:
        out.append("The per-request limit can't be more than the daily limit")
    if CAPABILITIES[cap]["money"] and not req.get("destinations"):
        out.append("Money movement needs at least one approved destination (a bank link in the client's name)")
    for d in req.get("destinations") or []:
        if d not in ctx.bank_links:
            out.append(f"Bank link {d} isn't on this account")
    return out


def process_grant(req: dict, ctx: GrantContext, events: list[dict] | None = None) -> FlowResult:
    """A grant or revoke request (map 05), replayed from its events: link verifications and revocations."""
    events = events or []
    acct = ctx.account
    cap = req.get("capability")
    label = CAPABILITIES.get(cap, {}).get("label", cap)
    res = FlowResult("entitlement", f"{label}: {acct.get('name', acct['id'])}", acct["id"], ctx.as_of)
    res.summary = {"Capability": label, "Account": f"{acct['id']} ({acct.get('name', '')})"}
    if req.get("per_request"):
        res.summary["Limits"] = f"${req['per_request']:,.0f} a request, ${req['per_day']:,.0f} a day"
    if req.get("expires_on"):
        res.summary["Expires"] = req["expires_on"]
    res.add(OPS, "Grant or revoke request from the RIA", "Grant", [f"By {req.get('requested_by', 'the RIA')}"])
    res.add(RULES, "Check the RIA user's entitlement to manage client permissions",
            "Allowed" if not ctx.requester_firm or ctx.requester_firm == acct.get("firm_crd") else "Not allowed",
            [], ["ENT-001"])
    problems = grant_problems(req, ctx)
    res.add(RULES, "Account-type rules: no borrowing or shorting in retirement accounts; no blocking codes",
            "Allowed" if not problems else "Declined", problems, ["ENT-001"])
    if problems:
        res.status, res.requested_items = "DECLINED", problems
        return res
    res.add(LEDGER, "Record the grant: capability, limits, approved destinations, expiry", "Recorded",
            [f"Destinations: {', '.join(req.get('destinations') or []) or 'the client’s own accounts'}"], ["ENT-001"])
    res.add(LEDGER, "Enroll the account in monitoring of client-initiated activity", "Enrolled", [], ["ENT-003"])
    revoked = last_event(events, "revoked")
    if revoked:
        res.add(LEDGER, "Revoke the capability immediately; log who, when, why", f"Revoked {revoked['on']}",
                [f"{revoked.get('by')}: {revoked.get('reason')}"], ["ENT-001"])
        res.status = "REVOKED"
        return res
    if date.fromisoformat(req["expires_on"]) < ctx.as_of:
        res.add(LEDGER, "Grant expired", req["expires_on"])
        res.status = "EXPIRED"
        return res
    if CAPABILITIES[cap]["money"]:
        pending = [d for d in req["destinations"] if ctx.bank_links.get(d, {}).get("status") != "verified"]
        res.add(LEDGER, "Verify the client's bank link: deposits or instant verification",
                "Verified" if not pending else f"Waiting for {len(pending)} link{'s' if len(pending) > 1 else ''}",
                [f"{ctx.bank_links[d]['bank']} ...{ctx.bank_links[d]['last4']}: {ctx.bank_links[d]['status']}"
                 for d in req["destinations"]], ["ENT-002"])
        if pending:
            res.status = "PENDING_LINK_VERIFICATION"
            return res
    res.add(OPS, "Notify the client: capability active; a passcode on every request", "Sent", [], ["ENT-002"])
    res.status = "ACTIVE"
    return res


def monitor(amount: float, account_value: float, earlier: list[dict], as_of: date) -> list[str]:
    """ENT-003: alerts for the RIA on a client's self-service request."""
    out = []
    share = policy.param("ENT-003", "share")
    if account_value and amount > share * account_value:
        out.append(f"${amount:,.2f} is more than {share:.0%} of the account's value")
    recent = [r for r in earlier if bdays_between(date.fromisoformat(r["on"]), as_of) < policy.param("ENT-003", "days")]
    if len(recent) + 1 >= policy.param("ENT-003", "count"):
        out.append(f"The {len(recent) + 1}th client request in {policy.param('ENT-003', 'days')} business days")
    return out


def micro_deposits(link_id: str) -> tuple[float, float]:
    """The two small deposits a bank link verification sends (synthetic, deterministic per link)."""
    h = sum(ord(c) * (i + 1) for i, c in enumerate(link_id))
    return round(0.05 + (h % 40) / 100, 2), round(0.11 + (h // 40 % 40) / 100, 2)
