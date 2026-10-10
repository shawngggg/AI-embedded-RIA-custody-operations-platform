"""
Transferability rules (Milestone 6).

Before assets move in or out, every position gets a disposition (the
transferability review map, 07, which runs in both directions):

  ACATS_IN_KIND        moves through NSCC ACATS (DTC-eligible securities, and
                       mutual funds through ACATS-Fund/SERV)
  LOI                  re-registered with the sponsor, transfer agent, or fund
                       administrator by letter of instruction
  LIQUIDATE            sold before the transfer because it can't be held here
  PRODUCT_ACCEPTANCE   no agreement with the asset manager; if the client wants
                       to keep it, product acceptance reviews it and the
                       transfer waits
  REVIEW               needs a person: an unknown security or a restricted one

ACATS moves whole shares, so a fractional share is sold and paid as cash in
lieu (industry practice). Incoming positions must be holdable on the platform;
for outgoing positions the receiving firm decides what it accepts.

The release decision follows from the dispositions: a full release when every
position is ready to move, a partial release while letters of instruction or
liquidations await confirmation, and a hold while anything waits for product
acceptance or review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from applications import Position
from rules import Disposition, evaluate_first

BASELINE = "2026-01-01"


class Route(Enum):
    ACATS_IN_KIND = "acats_in_kind"
    LOI = "loi"
    LIQUIDATE = "liquidate"
    PRODUCT_ACCEPTANCE = "product_acceptance"
    REVIEW = "review"


@dataclass
class SecurityRecord:
    security_id: str
    description: str
    asset_type: str
    dtc_eligible: bool = False
    fundserv_eligible: bool = False
    accepted_on_platform: bool = False      # product acceptance approved
    sponsor_agreement: bool = False         # custodian's agreement with the asset manager or sponsor
    proprietary_to: str | None = None       # a product only its own firm can hold
    restricted: bool = False                # restrictive legend (for example, Rule 144)
    sponsor: str | None = None
    isin: str | None = None


SECURITY_MASTER = {s.security_id: s for s in [
    SecurityRecord("037833100", "Apple Inc. common stock", "equity", dtc_eligible=True,
                   accepted_on_platform=True),
    SecurityRecord("922908363", "Vanguard S&P 500 ETF", "etf", dtc_eligible=True, accepted_on_platform=True),
    SecurityRecord("SYN-MF-03", "Evergreen Balanced Fund Class A (synthetic)", "mutual_fund",
                   fundserv_eligible=True, accepted_on_platform=True, sponsor="Evergreen Funds (synthetic)"),
    SecurityRecord("SYN-PRV-01", "Proprietary Core Bond Fund (delivering firm only)", "mutual_fund",
                   proprietary_to="Delivering firm"),
    SecurityRecord("SYN-REIT-01", "Summit Non-Traded REIT Class I (synthetic)", "alternative",
                   accepted_on_platform=True, sponsor_agreement=True, sponsor="Summit REIT Sponsor (synthetic)"),
    SecurityRecord("SYN-LP-02", "Harbor Creek Private Credit LP (synthetic)", "alternative",
                   sponsor="Harbor Creek Capital (synthetic)"),
    SecurityRecord("SYN-ANN-05", "Pinnacle Variable Annuity (synthetic)", "annuity",
                   accepted_on_platform=True, sponsor_agreement=True, sponsor="Pinnacle Life (synthetic)"),
    SecurityRecord("SYN-RST-04", "Bluebird Robotics Inc. restricted shares (synthetic)", "equity",
                   dtc_eligible=True, accepted_on_platform=True, restricted=True),
    SecurityRecord("SYN-NWF-B", "Northwind Frontier Fund Class B (synthetic)", "alternative",
                   accepted_on_platform=True, sponsor_agreement=True, isin="KYG0000NW001",
                   sponsor="Northwind Fund Administration (synthetic)"),
]}


@dataclass
class PositionContext:
    """What a transferability rule reads about one position."""
    position: Position
    direction: str                     # incoming | outgoing
    security: SecurityRecord | None
    client_elects_liquidation: bool = False


def _sec(ctx: PositionContext, attr: str, default=None):
    return getattr(ctx.security, attr, default) if ctx.security else default


COMPUTED = {
    "known": lambda c, d: c.security is not None,
    "restricted": lambda c, d: bool(_sec(c, "restricted", False)),
    "holdable_here": lambda c, d: bool(_sec(c, "accepted_on_platform", False)) and (
        _sec(c, "dtc_eligible", False) or _sec(c, "fundserv_eligible", False)
        or _sec(c, "sponsor_agreement", False)),
    "proprietary_elsewhere": lambda c, d: bool(_sec(c, "proprietary_to")),
    "dtc_eligible": lambda c, d: bool(_sec(c, "dtc_eligible", False)),
    "fundserv_eligible": lambda c, d: bool(_sec(c, "fundserv_eligible", False)),
    "sponsor_held": lambda c, d: bool(_sec(c, "sponsor_agreement", False)) and not _sec(c, "dtc_eligible", False),
}

ACATS_SOURCE = "FINRA Rule 11870 (customer account transfers); NSCC ACATS"
XFER_RULES = [
    {"rule_id": "XFR-001", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy: every position matches the security master before it moves",
     "field": "known", "operator": "equals", "value": False,
     "disposition": Disposition.REFER, "route": Route.REVIEW,
     "reason": "Not in the security master; match it manually"},
    {"rule_id": "XFR-002", "version": "1", "effective_date": BASELINE,
     "source": "SEC Rule 144; restrictive legends are removed through the transfer agent",
     "field": "restricted", "operator": "equals", "value": True,
     "disposition": Disposition.REFER, "route": Route.REVIEW,
     "reason": "Restricted or legended security; needs a legal transfer review"},
    {"rule_id": "XFR-003", "version": "1", "effective_date": BASELINE,
     "source": "FINRA Rule 11870 (non-transferable assets; the customer chooses what happens to them)",
     "all": [{"field": "direction", "operator": "equals", "value": "incoming"},
             {"field": "proprietary_elsewhere", "operator": "equals", "value": True}],
     "disposition": Disposition.REFER, "route": Route.LIQUIDATE,
     "reason": "Proprietary product only the delivering firm can hold; liquidate before transfer"},
    {"rule_id": "XFR-004", "version": "1", "effective_date": BASELINE,
     "source": "FINRA Rule 11870 (non-transferable assets); platform policy",
     "all": [{"field": "direction", "operator": "equals", "value": "incoming"},
             {"field": "holdable_here", "operator": "equals", "value": False},
             {"field": "client_elects_liquidation", "operator": "equals", "value": True}],
     "disposition": Disposition.REFER, "route": Route.LIQUIDATE,
     "reason": "Not holdable here and the client chose to liquidate"},
    {"rule_id": "XFR-005", "version": "1", "effective_date": BASELINE,
     "source": "Platform policy (product acceptance map, 24; transferability review map, 07)",
     "all": [{"field": "direction", "operator": "equals", "value": "incoming"},
             {"field": "holdable_here", "operator": "equals", "value": False}],
     "disposition": Disposition.REFER, "route": Route.PRODUCT_ACCEPTANCE,
     "reason": "No agreement with the asset manager; product acceptance reviews it and the transfer waits"},
    {"rule_id": "XFR-006", "version": "1", "effective_date": BASELINE, "source": ACATS_SOURCE,
     "field": "dtc_eligible", "operator": "equals", "value": True,
     "disposition": Disposition.CLEAR, "route": Route.ACATS_IN_KIND,
     "reason": "DTC-eligible; transfers in kind through ACATS"},
    {"rule_id": "XFR-007", "version": "1", "effective_date": BASELINE,
     "source": ACATS_SOURCE + "; NSCC ACATS-Fund/SERV",
     "field": "fundserv_eligible", "operator": "equals", "value": True,
     "disposition": Disposition.CLEAR, "route": Route.ACATS_IN_KIND,
     "reason": "Mutual fund eligible for ACATS-Fund/SERV; re-registers in kind"},
    {"rule_id": "XFR-008", "version": "1", "effective_date": BASELINE,
     "source": "Industry practice: sponsor-held assets re-register by letter of instruction",
     "field": "sponsor_held", "operator": "equals", "value": True,
     "disposition": Disposition.REFER, "route": Route.LOI,
     "reason": "Held at the sponsor, not DTC; re-register by letter of instruction"},
]
FALLBACK = {"rule_id": "XFR-099", "version": "1", "source": "Platform policy",
            "route": Route.REVIEW, "reason": "No transfer route found; needs review"}


@dataclass
class PositionDisposition:
    security_id: str
    description: str
    route: Route
    reason: str
    rule_id: str
    rule_version: str
    source: str
    quantity_in_kind: float = 0.0
    quantity_to_liquidate: float = 0.0
    counterparty: str | None = None
    action: str | None = None
    confirmed: bool = False

    @property
    def awaits_confirmation(self) -> bool:
        return self.route in (Route.LOI, Route.LIQUIDATE) and not self.confirmed

    def to_dict(self) -> dict:
        return {"security_id": self.security_id, "description": self.description,
                "route": self.route.value, "reason": self.reason, "rule_id": self.rule_id,
                "rule_version": self.rule_version, "source": self.source,
                "quantity_in_kind": self.quantity_in_kind,
                "quantity_to_liquidate": self.quantity_to_liquidate,
                "counterparty": self.counterparty, "action": self.action, "confirmed": self.confirmed}


@dataclass
class TransferReview:
    direction: str
    as_of: date
    dispositions: list[PositionDisposition] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def release(self) -> str:
        """
        hold: something waits for product acceptance or review; nothing moves yet
        partial: in-kind positions move now; LOIs or liquidations still await confirmation
        full: every position is ready to move
        """
        routes = {d.route for d in self.dispositions}
        if routes & {Route.PRODUCT_ACCEPTANCE, Route.REVIEW}:
            return "hold"
        if any(d.awaits_confirmation for d in self.dispositions):
            return "partial"
        return "full"

    def confirm(self, security_id: str) -> PositionDisposition:
        """Record a re-registration or liquidation confirmation."""
        for d in self.dispositions:
            if d.security_id == security_id and d.route in (Route.LOI, Route.LIQUIDATE):
                d.confirmed = True
                return d
        raise KeyError(f"No LOI or liquidation pending for {security_id}")

    def to_dict(self) -> dict:
        return {"direction": self.direction, "as_of": self.as_of.isoformat(), "release": self.release,
                "dispositions": [d.to_dict() for d in self.dispositions], "warnings": list(self.warnings)}


def _action(route: Route, sec: SecurityRecord | None, direction: str) -> tuple[str | None, str | None]:
    sponsor = sec.sponsor if sec else None
    if route is Route.LOI:
        return sponsor, f"Send an LOI to {sponsor or 'the sponsor'} to re-register the position"
    if route is Route.LIQUIDATE:
        who = "the delivering firm" if direction == "incoming" else "the trading desk"
        return who, f"Request liquidation from {who} before the transfer"
    if route is Route.PRODUCT_ACCEPTANCE:
        return "Risk and compliance", "Request product acceptance; the transfer waits for the decision"
    if route is Route.REVIEW:
        return "Custody operations", "Review manually before release"
    return None, None


def review_position(position: Position, direction: str, as_of: date,
                    security_master: dict | None = None,
                    client_elects_liquidation: bool = False,
                    warnings: list[str] | None = None) -> PositionDisposition:
    master = SECURITY_MASTER if security_master is None else security_master
    sec = master.get(position.security_id)
    ctx = PositionContext(position, direction, sec, client_elects_liquidation)
    ev = evaluate_first(ctx, XFER_RULES, as_of, COMPUTED)
    if warnings is not None:
        warnings.extend(ev.warnings)
    if ev.decisions:
        d = ev.decisions[0]
        rule = next(r for r in XFER_RULES if r["rule_id"] == d.rule_id and r["version"] == d.rule_version)
        route, reason, rid, ver, src = rule["route"], d.reason, d.rule_id, d.rule_version, d.source
    else:
        route, reason, rid, ver, src = (FALLBACK["route"], FALLBACK["reason"], FALLBACK["rule_id"],
                                        FALLBACK["version"], FALLBACK["source"])
    qty_in_kind = qty_sell = 0.0
    if route is Route.ACATS_IN_KIND:
        whole = math.floor(position.quantity + 1e-9)
        qty_in_kind, qty_sell = float(whole), round(position.quantity - whole, 6)
        if qty_sell:
            reason += f"; the {qty_sell:g} fractional share is sold and paid as cash in lieu"
    elif route is Route.LIQUIDATE:
        qty_sell = position.quantity
    elif route is Route.LOI:
        qty_in_kind = position.quantity
    counterparty, action = _action(route, sec, direction)
    return PositionDisposition(position.security_id, position.description, route, reason, rid, ver, src,
                               qty_in_kind, qty_sell, counterparty, action)


def review_transfer(positions: list[Position], direction: str, as_of: date,
                    security_master: dict | None = None,
                    liquidation_elections: set[str] | None = None) -> TransferReview:
    """Give every position a disposition and decide what can be released."""
    if direction not in ("incoming", "outgoing"):
        raise ValueError("direction must be 'incoming' or 'outgoing'")
    elections = liquidation_elections or set()
    review = TransferReview(direction, as_of)
    for p in positions:
        review.dispositions.append(review_position(p, direction, as_of, security_master,
                                                   p.security_id in elections, review.warnings))
    return review
