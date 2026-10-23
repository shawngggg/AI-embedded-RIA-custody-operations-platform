"""
One consistent synthetic book, built from the demo's open accounts: positions, trades and fails, money movement, asset
transfers, a billing run, three-layer settlement, and reconciliation breaks. Every security, price, bank, and number is
invented. It's deterministic: the same accounts and date give the same book, so a page and its export always agree.

How the pieces tie together:
- Each account holds its model, sized from the funding on its application, once its incoming transfer completes. An
  account whose ACATS transfer is still running holds only its opening deposit.
- Restriction codes do what the catalog says (onboarding/restrictions.py): DOCS stops trading and fee deduction, and
  CIPV stops outgoing money.
- Yesterday's trades settle today (T+1), so today's settlement is yesterday's blotter netted by channel.
- Incoming and outgoing transfers run through the platform's transferability rules (onboarding/transferability.py).
- Billing follows each RIA's fee schedule, tiers by household, pro-rates new accounts, and nothing posts until the RIA
  approves it.
- The general ledger is off by exactly the one cash break that's still open, and every open fail is either a
  reconciliation break or a receivable or payable on the ledger.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from applications import Position
from restrictions import CATALOG, Activity
from transferability import SECURITY_MASTER, SecurityRecord, review_transfer

ASSUMPTIONS = [
    "Every firm, client, security, price, bank, and amount is synthetic.",
    "Business days skip weekends and the fixed-date market holidays (New Year's Day, Juneteenth, Independence Day, "
    "Christmas), without weekend observance or the floating holidays.",
    "ACATS timing follows FINRA Rule 11870: the delivering firm validates within 1 business day, then the transfer "
    "completes within 3 business days.",
    "Equities, ETFs, and bonds settle T+1 through NSCC and DTC; mutual funds settle T+1 through Fund/SERV.",
    "A sponsor re-registers non-traded REIT shares within about 40 business days of the LOI, and operations follows "
    "up after 20 (assumptions for the demo; sponsors vary).",
    "Fee schedules, the custody fee (0.04% a year, $10 a quarter minimum), and the $100,000 callback threshold are "
    "illustrative, not any custodian's terms.",
]

ACATS_DAYS = 4          # 1 business day to validate, then 3 to complete
LOI_DAYS, LOI_FOLLOW_UP = 40, 20
HOLIDAYS = {(1, 1), (6, 19), (7, 4), (12, 25)}
CALLBACK_OVER = 100_000
CUSTODY_RATE, CUSTODY_MIN_QUARTER = 0.0004, 10.00


# --------------------------------------------------------------------------- calendar
def is_bday(d: date) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in HOLIDAYS


def bday(d: date, n: int = 0) -> date:
    """The business day n business days from d (n may be negative); n=0 rolls a non-business day back."""
    while not is_bday(d):
        d -= timedelta(days=1)
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        d += timedelta(days=step)
        while not is_bday(d):
            d += timedelta(days=step)
    return d


def next_bday(d: date) -> date:
    """d itself on a business day, otherwise the next one."""
    while not is_bday(d):
        d += timedelta(days=1)
    return d


def bdays_between(a: date, b: date) -> int:
    """Business days after a, up to and including b (0 when b <= a)."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_bday(d)
    return n


def quarter_bounds(d: date, offset: int = 0) -> tuple[date, date]:
    q = (d.month - 1) // 3 + offset
    year, q = d.year + q // 4, q % 4
    start = date(year, 3 * q + 1, 1)
    end = date(year + (q == 3), (3 * q + 3) % 12 + 1, 1) - timedelta(days=1)
    return start, end


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


# --------------------------------------------------------------------------- securities
def cusip_check(base: str) -> str:
    """The ninth character of a CUSIP: the modulus 10 double-add-double check digit of the first eight."""
    total = 0
    for i, c in enumerate(base):
        v = int(c) if c.isdigit() else ord(c) - 55
        if i % 2:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


@dataclass(frozen=True)
class Security:
    cusip: str
    ticker: str
    name: str
    asset_type: str          # equity | etf | mutual_fund | bond | alternative
    price: float             # per share, or per 100 of face for bonds
    location: str            # where the custodian holds it

    @property
    def per_unit(self) -> float:
        return self.price / 100 if self.asset_type == "bond" else self.price

    def to_dict(self) -> dict:
        return {"cusip": self.cusip, "ticker": self.ticker, "name": self.name, "asset_type": self.asset_type,
                "price": self.price, "location": self.location}


def _sec(n: int, ticker: str, name: str, kind: str, price: float) -> Security:
    base = f"99{n:04d}{'AB' if kind == 'bond' else 'AA'}"     # issuer numbers starting 99 are fictional here
    location = "Fund/SERV (transfer agent)" if kind == "mutual_fund" else "DTC"
    return Security(base + cusip_check(base), ticker, name + " (synthetic)", kind, price, location)


SECURITIES = [
    _sec(101, "BNVR", "Bonneville Robotics Inc.", "equity", 142.30),
    _sec(102, "GBEC", "Great Basin Energy Corp.", "equity", 58.75),
    _sec(103, "UNTH", "Uinta Health Systems Inc.", "equity", 211.40),
    _sec(104, "WSMC", "Wasatch Semiconductor Corp.", "equity", 318.20),
    _sec(105, "SFLX", "Salt Flats Logistics Inc.", "equity", 47.10),
    _sec(106, "KLBS", "Kolob Software Inc.", "equity", 96.85),
    _sec(107, "PCFD", "Provo Canyon Foods Co.", "equity", 33.60),
    _sec(108, "DSRM", "Deseret Materials Inc.", "equity", 74.25),
    _sec(109, "TTPY", "Teton Payments Inc.", "equity", 128.90),
    _sec(110, "ARBT", "Arches Biotech Inc.", "equity", 22.15),
    _sec(201, "STUM", "Summit Total US Market ETF", "etf", 268.40),
    _sec(202, "SIEQ", "Summit International Equity ETF", "etf", 61.20),
    _sec(203, "SAGB", "Summit Aggregate Bond ETF", "etf", 97.85),
    _sec(204, "SSTR", "Summit Short Treasury ETF", "etf", 50.12),
    _sec(205, "CDVG", "Canyon Dividend Growth ETF", "etf", 84.30),
    _sec(206, "CSCV", "Canyon Small Cap Value ETF", "etf", 41.75),
    _sec(301, "EVCBX", "Evergreen Core Bond Fund Institutional", "mutual_fund", 9.84),
    _sec(302, "RDGIX", "Ridgeline Growth Fund Institutional", "mutual_fund", 41.27),
    _sec(303, "RDMIX", "Ridgeline Municipal Income Fund", "mutual_fund", 10.62),
    _sec(401, "UST30", "US Treasury note 4.25% 2030", "bond", 100.84),
    _sec(402, "GBE31", "Great Basin Energy 5.10% notes 2031", "bond", 98.40),
    # The transferability rules' own synthetic REIT, so the LOI route is the engine's decision
    Security("SYN-REIT-01", "SMREIT", SECURITY_MASTER["SYN-REIT-01"].description, "alternative", 9.62,
             "Sponsor (non-traded)"),
]
BY_TICKER = {s.ticker: s for s in SECURITIES}
BY_CUSIP = {s.cusip: s for s in SECURITIES}

MODELS = {
    "Growth": [("STUM", .30), ("SIEQ", .14), ("CSCV", .08), ("RDGIX", .10), ("WSMC", .06), ("KLBS", .05),
               ("TTPY", .05), ("BNVR", .04), ("UNTH", .05), ("SAGB", .10)],
    "Balanced": [("STUM", .22), ("SIEQ", .10), ("CDVG", .10), ("SAGB", .22), ("EVCBX", .12), ("UST30", .10),
                 ("UNTH", .04), ("DSRM", .04), ("GBEC", .03)],
    "Income": [("SAGB", .25), ("EVCBX", .15), ("UST30", .15), ("GBE31", .10), ("CDVG", .15), ("SSTR", .10),
               ("PCFD", .03), ("GBEC", .04)],
    "Tax-aware": [("RDMIX", .30), ("STUM", .25), ("SIEQ", .10), ("CDVG", .10), ("SAGB", .10), ("SMREIT", .08),
                  ("UNTH", .04)],
    "Self-directed": [("WSMC", .25), ("ARBT", .15), ("TTPY", .20), ("BNVR", .15), ("SFLX", .10), ("KLBS", .10)],
}

BANKS = ["Wasatch Community Bank", "Lakeview Federal Credit Union", "Bear River Savings Bank", "Sawtooth National Bank",
         "Cottonwood Heights Credit Union", "Great Salt Lake Trust Company"]
PRIOR_FIRMS = ["Prior custodian A (synthetic)", "Prior custodian B (synthetic)", "Prior broker-dealer C (synthetic)"]

# Fee schedules by RIA CRD. Tiers are annual rates; None is the top tier.
FEE_SCHEDULES = {
    "310001": {"method": "custodian_calculates", "timing": "in_advance", "households": True,
               "tiers": [(1_000_000, 0.0100), (5_000_000, 0.0075), (None, 0.0050)],
               "describe": "Tiered by household: 1.00% on the first $1M, 0.75% to $5M, 0.50% above. Quarterly in "
                           "advance on the prior quarter-end value."},
    "310031": {"method": "custodian_calculates", "timing": "in_arrears", "households": False,
               "tiers": [(None, 0.0085)],
               "describe": "Flat 0.85% a year. Quarterly in arrears on the average daily value."},
    "310003": {"method": "ria_posts", "timing": "in_advance", "households": False, "max_rate": 0.0200,
               "tiers": [(None, 0.0100)],
               "describe": "The RIA posts its own fees by fee file, with its good-faith attestation. Quarterly in "
                           "advance. Client agreements cap the fee at 2.00% a year."},
}


def _rng(*parts) -> random.Random:
    return random.Random(":".join(str(p) for p in parts))


def _money(x: float) -> float:
    return round(x + 0.0, 2)


def blocking(a: dict, activity: Activity) -> list[str]:
    """The account's restriction codes that block an activity."""
    return [c for c in a["codes"] if c in CATALOG and activity in CATALOG[c].blocks]


def _model(a: dict) -> str:
    purpose = (a.get("purpose") or "").lower()
    if a["group"] == "self_directed":
        return "Self-directed"
    if a["registration_type"] == "trust":
        return "Tax-aware"
    if "income" in purpose:
        return "Income"
    if any(w in purpose for w in ("balanced", "college", "traditional ira")):
        return "Balanced"
    return "Growth"


def _blended(tiers, value: float) -> float:
    """The annual rate a tiered schedule works out to on a value."""
    if value <= 0:
        return tiers[0][1]
    fee, lower = 0.0, 0.0
    for upper, rate in tiers:
        top = value if upper is None else min(value, upper)
        if top > lower:
            fee += (top - lower) * rate
        if upper is None or value <= upper:
            break
        lower = upper
    return fee / value


def _dispositions(review, quantities: dict | None = None) -> list[dict]:
    return [{"security": d.description, "quantity": d.quantity_in_kind or d.quantity_to_liquidate
             or (quantities or {}).get(d.security_id), "route": d.route.value, "reason": d.reason,
             "action": d.action} for d in review.dispositions]


# --------------------------------------------------------------------------- the book
def build(accounts: list[dict], firms: dict[str, str], as_of: date, ledger: dict | None = None) -> dict:
    """
    accounts: the open accounts, each {id, name, firm_crd, group, registration_type, path, opened_on (date), codes
    (active restriction codes), household, purpose, funding}. firms: CRD -> legal name. as_of: the business day shown
    (a weekend rolls back to Friday).
    """
    today = bday(as_of)
    accts = []
    for raw in sorted(accounts, key=lambda a: a["id"]):
        a = dict(raw, opened_on=min(raw["opened_on"], today), codes=sorted(raw.get("codes") or []))
        a["model"] = _model(a)
        a["target_value"] = max(a.get("funding") or 0, 50_000) * _rng("value", a["id"]).uniform(0.94, 1.12)
        accts.append(a)
    b = _Book(today, accts, firms)
    b.ledger = ledger
    b.transfers_in()
    b.positions_and_cash()
    b.trading()
    b.fails_open()
    b.money()
    b.transfer_out()
    b.billing()
    b.settle()
    b.reconcile()
    return b.result()


class _Book:
    def __init__(self, today: date, accounts: list[dict], firms: dict[str, str]):
        self.today, self.yesterday, self.accounts, self.firms = today, bday(today, -1), accounts, firms
        self.positions, self.cash, self.holdings, self.value = [], {}, {}, {}
        self.orders, self.allocations, self.fails, self.movements, self.transfers = [], [], [], [], []
        self.billing_runs, self.fees, self.breaks = [], [], []
        self.settlement: dict = {}
        self.ledger: dict | None = None
        self.in_transit: set[str] = set()       # incoming ACATS still running: only the opening deposit is here
        self.loi_pending: set[str] = set()      # the REIT waits for the sponsor's re-registration
        self.closing: dict | None = None
        self.unapplied = self.r01 = self.dividend = 0.0
        self.master = dict(SECURITY_MASTER)
        for s in SECURITIES:
            self.master.setdefault(s.cusip, SecurityRecord(
                s.cusip, s.name, s.asset_type, dtc_eligible=s.location == "DTC",
                fundserv_eligible=s.asset_type == "mutual_fund", accepted_on_platform=True))

    def firm(self, crd: str) -> str:
        return self.firms.get(crd, crd)

    @staticmethod
    def _ref(a: dict) -> dict:
        return {"account": a["id"], "client": a["name"], "firm_crd": a["firm_crd"]}

    @staticmethod
    def _target(a: dict) -> list[tuple[Security, float]]:
        """What the account holds once it's funded: its model, sized from the application's funding."""
        r = _rng("target", a["id"])
        out = []
        for ticker, w in MODELS[a["model"]]:
            s = BY_TICKER[ticker]
            amount = a["target_value"] * w * r.uniform(0.9, 1.1)
            if s.asset_type == "mutual_fund":
                qty = round(amount / s.price, 3)
            elif s.asset_type == "bond":
                qty = float(max(1_000, round(amount / s.per_unit / 1_000) * 1_000))
            else:
                qty = float(max(1, int(amount / s.price)))
            out.append((s, qty))
        return out

    # ------------------------------------------------------------ incoming transfers
    def transfers_in(self) -> None:
        def since(a):
            return bdays_between(next_bday(a["opened_on"]), self.today)
        recent = [a for a in self.accounts if since(a) <= 15]
        running = sorted([a for a in recent if since(a) < ACATS_DAYS], key=lambda a: (a["opened_on"], a["id"]))
        rejected = running[0]["id"] if len(running) >= 2 else None
        for i, a in enumerate(sorted(self.accounts, key=lambda a: (a["opened_on"], a["id"]))):
            requested = next_bday(a["opened_on"])          # the RIA sends the transfer form with the application
            note = ""
            if a["id"] == rejected:
                note = (f"The delivering firm took exception on {_day(bday(requested, 1))}: the account title on the "
                        f"transfer form left out the client's middle initial. The RIA resubmitted it the next "
                        f"business day.")
                requested = bday(requested, 2)
            e = bdays_between(requested, self.today)
            if e < ACATS_DAYS:
                self.in_transit.add(a["id"])
            reit = a["model"] == "Tax-aware"
            if reit and e < LOI_DAYS:
                self.loi_pending.add(a["id"])
            if a in recent:
                target = [(s, q) for s, q in self._target(a) if s.ticker != "SMREIT"]
                pos = [Position(s.cusip, s.name, s.asset_type, q) for s, q in target]
                proprietary = sum(map(ord, a["id"])) % 3 == 0
                if proprietary:
                    pos.append(Position("SYN-PRV-01", SECURITY_MASTER["SYN-PRV-01"].description, "mutual_fund",
                                        1_250.0))
                review = review_transfer(pos, "incoming", self.today, security_master=self.master)
                validated = bday(requested, 1)
                if e == 0:
                    status = "Submitted: the delivering firm validates within 1 business day"
                elif e < ACATS_DAYS:
                    status = f"Validated {_day(validated)}: completes through NSCC by {_day(bday(validated, 3))}"
                else:
                    status = f"Completed {_day(bday(requested, ACATS_DAYS))}"
                    if proprietary:
                        review.confirm("SYN-PRV-01")             # the delivering firm sold it and sent the cash
                if a["id"] == rejected:
                    status = "Resubmitted after an exception. " + status
                if proprietary:
                    note = (note + " " if note else "") + (
                        "The proprietary fund can't be held here: the delivering firm sells it and sends the cash."
                        if e < ACATS_DAYS else "The delivering firm sold the proprietary fund and sent the cash.")
                if "CIPV" in a["codes"]:
                    note = (note + " " if note else "") + (
                        "Identity verification is still in progress: assets can come in, but nothing goes out "
                        "until it's complete.")
                self.transfers.append(self._ref(a) | {
                    "id": f"ACATS-IN-{a['id'][-4:]}", "direction": "Incoming", "type": "ACATS full transfer",
                    "other_firm": PRIOR_FIRMS[i % 3], "requested": requested.isoformat(), "business_days": e,
                    "status": status, "release": review.release, "note": note,
                    "positions": _dispositions(review)})
            if reit and e <= LOI_DAYS + 10:
                s = BY_TICKER["SMREIT"]
                qty = next(q for t, q in self._target(a) if t.ticker == "SMREIT")
                loi = review_transfer([Position(s.cusip, s.name, s.asset_type, qty)], "incoming", self.today,
                                      security_master=self.master)
                if a["id"] in self.loi_pending:
                    status = f"LOI sent {_day(requested)}: waiting for the sponsor to re-register"
                    if e >= LOI_FOLLOW_UP:
                        status += (f"; no reply after {LOI_FOLLOW_UP} business days, so operations followed up "
                                   f"{_day(bday(requested, LOI_FOLLOW_UP))}")
                else:
                    loi.confirm(s.cusip)
                    status = f"Re-registered by the sponsor {_day(bday(requested, LOI_DAYS))}"
                self.transfers.append(self._ref(a) | {
                    "id": f"LOI-{a['id'][-4:]}", "direction": "Incoming", "type": "Non-ACATS: re-registration by LOI",
                    "other_firm": SECURITY_MASTER["SYN-REIT-01"].sponsor, "requested": requested.isoformat(),
                    "business_days": e, "release": loi.release, "status": status,
                    "note": "Non-traded REIT shares don't move through ACATS: the sponsor re-registers them to the "
                            "custodian for the client's benefit once it has the LOI.",
                    "positions": _dispositions(loi, {s.cusip: qty})})

    # ------------------------------------------------------------ positions and cash
    def positions_and_cash(self) -> None:
        if self.ledger is not None:
            return self._from_ledger()
        q_start = quarter_bounds(self.today)[0]
        short_cash = set()
        for crd, sched in FEE_SCHEDULES.items():          # one fully invested account per custodian-billed RIA
            if sched["method"] != "custodian_calculates":
                continue
            pool = [a for a in self.accounts if a["firm_crd"] == crd and a["group"] == "pure_ria"
                    and a["id"] not in self.in_transit and not blocking(a, Activity.FEE_DEDUCTION)
                    and a["model"] != "Tax-aware" and a["opened_on"] < q_start]
            if pool:
                short_cash.add(min(pool, key=lambda a: a["target_value"])["id"])
        for a in self.accounts:
            r = _rng("cash", a["id"])
            if a["id"] in self.in_transit:
                self.cash[a["id"]] = _money(round(r.uniform(1_000, 5_000), -2))      # the opening deposit
                self.value[a["id"]] = self.cash[a["id"]]
                continue
            invested = 0.0
            for s, qty in self._target(a):
                if s.ticker == "SMREIT" and a["id"] in self.loi_pending:
                    continue
                mv = _money(qty * s.per_unit)
                invested += mv
                p = self._ref(a) | {"firm": self.firm(a["firm_crd"]), "cusip": s.cusip, "ticker": s.ticker,
                                    "security": s.name, "asset_type": s.asset_type, "quantity": qty,
                                    "price": s.price, "market_value": mv, "location": s.location}
                self.positions.append(p)
                self.holdings.setdefault(a["id"], {})[s.ticker] = p
            weight = 1 - sum(w for _, w in MODELS[a["model"]])
            self.cash[a["id"]] = _money(r.uniform(150, 450) if a["id"] in short_cash
                                        else a["target_value"] * weight * r.uniform(0.6, 1.4))
            self.value[a["id"]] = _money(invested + self.cash[a["id"]])

    # ------------------------------------------------------------ trading
    def _from_ledger(self) -> None:
        """Release 2 is live: positions and cash come from the ledger (app/book.py passes them in), not the models.
        An account with nothing settled yet is still being funded; one with an outgoing transfer is frozen."""
        for a in self.accounts:
            led = self.ledger.get(a["id"], {})
            invested = 0.0
            for cusip, qty in sorted((led.get("holdings") or {}).items()):
                s = BY_CUSIP.get(cusip)
                if s is None or qty <= 0:
                    continue
                mv = _money(qty * s.per_unit)
                invested += mv
                p = self._ref(a) | {"firm": self.firm(a["firm_crd"]), "cusip": s.cusip, "ticker": s.ticker,
                                    "security": s.name, "asset_type": s.asset_type, "quantity": qty,
                                    "price": s.price, "market_value": mv, "location": s.location}
                self.positions.append(p)
                self.holdings.setdefault(a["id"], {})[s.ticker] = p
            self.cash[a["id"]] = _money(led.get("cash") or 0)
            self.value[a["id"]] = _money(invested + self.cash[a["id"]])
            if not self.holdings.get(a["id"]):
                self.in_transit.add(a["id"])
            if led.get("leaving_on"):
                self.in_transit.add(a["id"])              # frozen for its outgoing transfer: no trading
        self.loi_pending = set()

    def _can_trade(self, a: dict) -> bool:
        return (a["id"] not in self.in_transit and not blocking(a, Activity.BUY)
                and not blocking(a, Activity.SELL))

    def trading(self) -> None:
        tradable = [a for a in self.accounts if a["group"] == "pure_ria" and self._can_trade(a)]
        for day in (self.yesterday, self.today):
            settle = bday(day, 1)
            for crd in sorted({a["firm_crd"] for a in tradable}):
                firm_accounts = [a for a in tradable if a["firm_crd"] == crd]
                r = _rng("orders", crd, day)
                for n, ticker in enumerate(r.sample(["STUM", "SAGB", "SIEQ", "CDVG", "CSCV", "UNTH", "KLBS"], 2)):
                    self._block(r, crd, day, settle, n, ticker, firm_accounts)
            r = _rng("funds", day)
            for a in r.sample(tradable, min(3, len(tradable))):
                self._fund_order(r, a, day)
        for a in self.accounts:
            if a["group"] == "self_directed" and self._can_trade(a):
                self._client_orders(a)

    def _block(self, r, crd, day, settle, n, ticker, firm_accounts) -> None:
        s = BY_TICKER[ticker]
        side = "Buy" if r.random() < 0.6 else "Sell"
        pool = [a for a in firm_accounts if side == "Buy" or self.holdings.get(a["id"], {}).get(ticker)]
        if len(pool) < 2:
            return
        chosen = sorted(r.sample(pool, min(len(pool), r.randint(3, 8))), key=lambda a: a["id"])
        price = round(s.price * (1 + r.uniform(-0.004, 0.004)), 2)
        partial = day == self.today and n == 1 and r.random() < 0.5
        oid = f"BLK-{day:%m%d}-{crd[-2:]}{n + 1}"
        ordered = filled = 0
        allocs = []
        for a in chosen:
            held = self.holdings.get(a["id"], {}).get(ticker, {}).get("quantity", 0)
            want = max(1, int(self.value[a["id"]] * r.uniform(0.008, 0.025) / s.price))
            qty = min(want, int(held * 0.3)) if side == "Sell" else want
            if side == "Buy" and qty * price > self.cash[a["id"]]:
                qty = int(self.cash[a["id"]] / price)        # a buy never spends more cash than the account has
            if qty <= 0:
                continue
            got = int(qty * 0.6) if partial else qty
            ordered, filled = ordered + qty, filled + got
            allocs.append(self._ref(a) | {
                "order": oid, "trade_date": day.isoformat(), "settle_date": settle.isoformat(), "side": side,
                "ticker": ticker, "cusip": s.cusip, "ordered": qty, "quantity": got, "price": price,
                "amount": _money(got * price), "status": "Allocated" if got == qty else "Allocated (partial fill)"})
        if len(allocs) < 2:
            return
        self.allocations += allocs
        self.orders.append({
            "order": oid, "trade_date": day.isoformat(), "firm": self.firm(crd), "firm_crd": crd,
            "entered_by": "RIA (block order)", "side": side, "ticker": ticker, "security": s.name,
            "quantity": ordered, "filled": filled, "avg_price": price, "accounts": len(allocs),
            "status": "Partially filled: the rest is working" if partial else "Filled and allocated",
            "venue": "In-house execution", "settle_date": settle.isoformat(), "settles_through": "NSCC and DTC"})

    def _fund_order(self, r, a, day) -> None:
        s = BY_TICKER[r.choice(["EVCBX", "RDGIX", "RDMIX"])]
        amount = min(round(r.uniform(5_000, 40_000), -2), round(self.cash[a["id"]] * 0.8, -2))
        if amount < 1_000:
            return
        priced = day < self.today                     # today's orders price at today's 4 p.m. NAV
        qty = round(amount / s.price, 3) if priced else None
        oid = f"MF-{day:%m%d}-{a['id'][-4:]}"
        settle = bday(day, 1).isoformat()
        self.orders.append({
            "order": oid, "trade_date": day.isoformat(), "firm": self.firm(a["firm_crd"]), "firm_crd": a["firm_crd"],
            "entered_by": "RIA", "account": a["id"], "side": "Buy", "ticker": s.ticker, "security": s.name,
            "amount": amount,
            "quantity": qty, "filled": qty, "avg_price": s.price if priced else None, "accounts": 1,
            "status": "Priced at NAV" if priced else "Waiting for today's NAV", "venue": "Fund/SERV",
            "settle_date": settle, "settles_through": "Fund/SERV"})
        self.allocations.append(self._ref(a) | {
            "order": oid, "trade_date": day.isoformat(), "settle_date": settle, "side": "Buy", "ticker": s.ticker,
            "cusip": s.cusip, "ordered": qty, "quantity": qty, "price": s.price if priced else None, "amount": amount,
            "status": "Allocated" if priced else "Pending NAV"})

    def _client_orders(self, a) -> None:
        r = _rng("self-directed", a["id"], self.today)
        for n, ticker in enumerate(r.sample(["WSMC", "ARBT", "TTPY", "SFLX"], 2)):
            s = BY_TICKER[ticker]
            qty, limit = r.randint(10, 60), round(s.price * (1.0 if n == 0 else 0.97), 2)
            qty = min(qty, int(self.cash[a["id"]] / limit))
            if qty <= 0:
                continue
            oid = f"SD-{self.today:%m%d}-{a['id'][-4:]}{n + 1}"
            settle = bday(self.today, 1).isoformat()
            filled = n == 0
            self.orders.append({
                "order": oid, "trade_date": self.today.isoformat(), "firm": self.firm(a["firm_crd"]),
                "firm_crd": a["firm_crd"], "entered_by": "Client (self-directed)", "account": a["id"], "side": "Buy",
                "ticker": ticker,
                "security": s.name, "quantity": qty, "filled": qty if filled else 0,
                "avg_price": limit if filled else None, "limit": limit, "accounts": 1,
                "status": "Filled" if filled else "Open: limit not reached", "venue": "In-house execution",
                "settle_date": settle, "settles_through": "NSCC and DTC"})
            if filled:
                self.allocations.append(self._ref(a) | {
                    "order": oid, "trade_date": self.today.isoformat(), "settle_date": settle, "side": "Buy",
                    "ticker": ticker, "cusip": s.cusip, "ordered": qty, "quantity": qty, "price": limit,
                    "amount": _money(qty * limit), "status": "Allocated"})

    # ------------------------------------------------------------ open fails from earlier settlement dates
    def fails_open(self) -> None:
        specs = [
            ("FL-1", "Fail to receive", "Buy", "CSCV", 400, -2,
             "The delivering side failed at NSCC (CNS): the shares are owed to the custodian",
             "Watch CNS for delivery; the client keeps the position, and the cash stays a payable until it arrives",
             False),
            ("FL-2", "Fail to deliver", "Sell", "KLBS", 150, -1,
             "The client's shares are registered at the transfer agent (DRS) and weren't moved to DTC in time",
             "Move the shares from DRS to DTC and deliver; close out under Reg SHO Rule 204 by the date shown",
             True),
            ("FL-3", "Don't know (DK)", "Buy", "DSRM", 220, -1,
             "The counterparty doesn't recognize the trade: its price is $0.05 off the confirmation",
             "Compare confirmations and correct the side that's wrong", False),
        ]
        r = _rng("fails", self.today)
        for fid, kind, side, ticker, qty, back, cause, action, close_out in specs:
            pool = [a for a in self.accounts if self._can_trade(a) and ticker in self.holdings.get(a["id"], {})]
            if not pool:
                continue
            a = r.choice(pool)
            s = BY_TICKER[ticker]
            settle = bday(self.today, back)
            self.fails.append(self._ref(a) | {
                "id": fid, "type": kind, "side": side, "ticker": ticker, "security": s.name, "quantity": qty,
                "amount": _money(qty * s.price), "trade_date": bday(settle, -1).isoformat(),
                "settle_date": settle.isoformat(), "age_days": bdays_between(settle, self.today), "cause": cause,
                "action": action, "close_out_by": bday(settle, 3).isoformat() if close_out else None})

    # ------------------------------------------------------------ money movement
    def _move(self, mid, day, a, kind, direction, amount, rail, status, note="", counterparty=None, hold=None,
              portal_note=None):
        """portal_note: what the RIA and the client read instead, when the note names a break or an internal step."""
        self.movements.append({
            "id": mid, "date": day.isoformat(), "account": a["id"] if a else None,
            "client": a["name"] if a else None, "firm_crd": a["firm_crd"] if a else None, "type": kind,
            "direction": direction, "amount": _money(amount), "rail": rail, "counterparty": counterparty,
            "status": status, "note": note, "hold_code": hold,
            "portal_note": note if portal_note is None else portal_note})

    def _settled(self, day: date, rail: str) -> str:
        if day < self.today:
            return "Settled"
        return f"Pending: settles {_day(bday(self.today, 1))}" if rail == "ACH" else "Sent"

    def money(self) -> None:
        days = [bday(self.today, -k) for k in range(9, -1, -1)]
        for a in self.accounts:
            r = _rng("money", a["id"])
            bank = f"{r.choice(BANKS)} ...{r.randint(1000, 9999)}"
            if a["id"] in self.in_transit:
                day = next_bday(a["opened_on"])
                self._move(f"DEP-{a['id'][-4:]}", day, a, "Opening deposit (ACH pull)", "In", self.cash[a["id"]],
                           "ACH", self._settled(day, "ACH"), "The rest of the account arrives by ACATS", bank)
                continue
            out_ok = not blocking(a, Activity.WITHDRAWAL)
            wired = distributed = False
            for day in days:
                if day < a["opened_on"]:
                    continue
                roll = r.random()
                if roll < 0.08 and a["registration_type"] != "trust":
                    self._move(f"ACH-{day:%m%d}-{a['id'][-4:]}", day, a, "Deposit (ACH pull)", "In",
                               round(r.uniform(500, 25_000), -1), "ACH", self._settled(day, "ACH"), counterparty=bank)
                elif roll < 0.11 and out_ok and not distributed and "traditional ira" in (a.get("purpose") or "").lower():
                    distributed = True
                    gross = min(round(r.uniform(2_000, 12_000), -2), round(self.cash[a["id"]] * 0.8, -2))
                    if gross >= 1_000:
                        self._move(f"DST-{day:%m%d}-{a['id'][-4:]}", day, a, "IRA distribution (ACH)", "Out", gross,
                                   "ACH", self._settled(day, "ACH"),
                                   f"10% federal withholding (${gross * 0.10:,.2f}), the default when the client "
                                   f"makes no election", bank)
                elif roll < 0.13 and out_ok and not wired and a["registration_type"] in ("individual", "joint"):
                    wired = True
                    amount = min(round(r.uniform(10_000, 60_000), -3), round(self.cash[a["id"]] * 0.8, -3))
                    if amount >= 1_000:
                        self._move(f"WIR-{day:%m%d}-{a['id'][-4:]}", day, a, "Wire out", "Out", amount, "Fedwire",
                                   self._settled(day, "Fedwire"), "Requested by the RIA; the client confirmed with "
                                   "a one-time passcode", bank)
        for a in self.accounts:                     # outgoing money an account's restriction stops
            codes = blocking(a, Activity.WITHDRAWAL)
            if codes:
                self._move(f"ACH-BLK-{a['id'][-4:]}", self.today, a, "Withdrawal (ACH credit)", "Out", 5_000, "ACH",
                           f"Blocked: {codes[0]}",
                           f"{CATALOG[codes[0]].description}: the request is refused until the code is removed",
                           "Client's bank on file", hold=codes[0], portal_note="")
        self._money_events()
        self._journals()
        self.movements.sort(key=lambda m: (m["date"], m["id"]), reverse=True)

    def _money_events(self) -> None:
        older = [a for a in self.accounts if a["group"] == "pure_ria" and a["id"] not in self.in_transit
                 and not blocking(a, Activity.WITHDRAWAL) and a["registration_type"] != "trust"]
        if len(older) < 5:
            return
        r = _rng("money-events", self.today)
        big = max(older, key=lambda a: self.cash[a["id"]])
        p1, p2, p4, p5 = sorted(r.sample([a for a in older if a is not big], 4), key=lambda a: a["id"])
        self.r01 = 7_500.00
        self._move("ACH-R01", bday(self.today, -3), p1, "Deposit (ACH pull)", "In", self.r01, "ACH",
                   f"Returned R01 {_day(self.yesterday)}",
                   "R01 insufficient funds at the client's bank. The deposit still shows on the client ledger: "
                   "reverse it and tell the RIA (break CB-R01)", f"{BANKS[1]} ...4417",
                   portal_note="The client's bank returned it: insufficient funds (R01). The deposit is reversed.")
        self._move("ACH-R03", bday(self.today, -2), p2, "Withdrawal (ACH credit)", "Out", 3_200, "ACH",
                   f"Returned R03 {_day(self.yesterday)}",
                   "R03 no account or unable to locate: the money is back in the account, and the bank instruction "
                   "needs verifying with the client before it's used again", f"{BANKS[2]} ...0981",
                   portal_note="The bank returned it: no account found (R03). The money is back in the account; "
                               "please confirm the bank details.")
        amount = max(CALLBACK_OVER + 10_000, min(250_000, round(self.cash[big["id"]] * 0.9, -4)))
        self._move("WIR-HOLD", self.today, big, "Wire out", "Out", amount, "Fedwire", "Held for callback",
                   f"Over ${CALLBACK_OVER:,}: a callback to the client on a phone number already on file before it's "
                   f"released", f"{BANKS[5]} ...6620",
                   portal_note="We'll call the client on the number on file to confirm before it's sent.")
        self.unapplied = 48_250.00
        wrong = p4["id"][:4] + p4["id"][4:].replace("0", "O", 1)
        self._move("WIR-IN-UNAPPLIED", self.yesterday, None, "Wire in", "In", self.unapplied, "Fedwire",
                   "In suspense (unapplied)",
                   f"The reference reads {wrong} (letter O for zero), so no account matches. It sits in suspense until "
                   f"the sender confirms it's for {p4['id']}", "Harbor Title & Escrow (synthetic)")
        self._move("WIR-IN-1", bday(self.today, -3), p5, "Wire in", "In", 125_000, "Fedwire", "Settled",
                   "Matched to the account on receipt", f"{BANKS[0]} ...2290")

    def _journals(self) -> None:
        households: dict[str, list[dict]] = {}
        for a in self.accounts:
            households.setdefault(a.get("household") or a["id"], []).append(a)
        for members in households.values():
            if len(members) < 2:
                continue
            src = next((m for m in members if not blocking(m, Activity.WITHDRAWAL) and m["id"] not in self.in_transit
                        and m["registration_type"] in ("individual", "joint")), None)
            dst = next((m for m in members if m is not src and m["id"] not in self.in_transit), None)
            if not src or not dst:
                continue
            day = bday(self.today, -4)
            amount = min(25_000, round(self.cash[src["id"]] * 0.5, -3))
            if amount < 1_000:
                continue
            jid = f"JNL-{src['id'][-4:]}-{dst['id'][-4:]}"
            self._move(jid, day, src, f"Journal to {dst['id']}", "Out", amount, "Internal journal", "Settled",
                       f"To {dst['name']}: same household, so no money leaves the custodian")
            self._move(jid + "-IN", day, dst, f"Journal from {src['id']}", "In", amount, "Internal journal",
                       "Settled", f"From {src['name']}: same household")

    # ------------------------------------------------------------ an outgoing ACATS: the client is leaving
    def transfer_out(self) -> None:
        if self.ledger is not None:                     # the ledger names the account that's leaving
            a = next((a for a in self.accounts if self.ledger.get(a["id"], {}).get("leaving_on")), None)
            if a is not None:
                a["leaves_on"] = date.fromisoformat(self.ledger[a["id"]]["leaving_on"])
                self.closing = a
            return
        pool = [a for a in self.accounts if a["registration_type"] == "ira" and self.holdings.get(a["id"])
                and not blocking(a, Activity.TRANSFER_OUT) and a["opened_on"] < quarter_bounds(self.today)[0]]
        if not pool:
            return
        a = self.closing = pool[0]
        requested = self.yesterday
        held = list(self.holdings[a["id"]].values())
        review = review_transfer([Position(p["cusip"], p["security"], p["asset_type"], p["quantity"]) for p in held],
                                 "outgoing", self.today, security_master=self.master)
        a["leaves_on"] = bday(requested, ACATS_DAYS)
        self.transfers.append(self._ref(a) | {
            "id": f"ACATS-OUT-{a['id'][-4:]}", "direction": "Outgoing", "type": "ACATS full transfer",
            "other_firm": "Receiving firm D (synthetic)", "requested": requested.isoformat(),
            "business_days": bdays_between(requested, self.today), "release": review.release,
            "status": f"Validated by us {_day(bday(requested, 1))}: completes through NSCC by {_day(a['leaves_on'])}",
            "note": "The client is moving the IRA to another firm. The account closes when the transfer completes, and "
                    "the unused part of this quarter's advance fee is refunded.",
            "positions": _dispositions(review)})

    # ------------------------------------------------------------ billing
    def billing(self) -> None:
        household_value: dict[tuple, float] = {}
        for a in self.accounts:
            k = (a["firm_crd"], a.get("household") or a["id"])
            household_value[k] = household_value.get(k, 0) + self.value[a["id"]]
        for crd, sched in FEE_SCHEDULES.items():
            firm_accounts = [a for a in self.accounts if a["firm_crd"] == crd]
            if not firm_accounts:
                continue
            advance = sched["timing"] == "in_advance"
            start, end = quarter_bounds(self.today, 0 if advance else -1)
            bill = next_bday(start if advance else end + timedelta(days=1))
            included = [a for a in firm_accounts if (a["opened_on"] < bill if advance else a["opened_on"] <= end)]
            later = [a for a in firm_accounts if a not in included]
            run_id = f"FEE-{crd[-2:]}-{start:%Y}Q{(start.month - 1) // 3 + 1}"
            rows = [self._fee(run_id, a, sched, start, end, household_value) for a in included]
            note = ""
            if later and sched["method"] == "ria_posts":
                note = f"{len(later)} accounts opened after {_day(bill)}: the RIA bills them in its next file."
            elif later and not advance:
                nxt = next_bday(quarter_bounds(self.today)[1] + timedelta(days=1))
                note = f"{len(later)} accounts opened after {_day(end)}: first billed with the next run on {_day(nxt)}."
            if rows:
                self._run(run_id, crd, sched, start, end, bill, rows, note)
            if advance and sched["method"] == "custodian_calculates" and later:
                stub = [self._fee(run_id + "-NEW", a, sched, a["opened_on"], end, household_value) for a in later]
                self._run(run_id + "-NEW", crd, sched, start, end, self.today, stub,
                          f"Catch-up bills for {len(stub)} accounts opened since {_day(bill)}, pro-rated from each "
                          f"opening date to {_day(end)}.", catch_up=True)

    def _fee(self, run_id, a, sched, start, end, household_value) -> dict:
        q_start, q_end = quarter_bounds(start)
        quarter_days = (q_end - q_start).days + 1
        days = (end - max(start, a["opened_on"])).days + 1
        factor = 0.985 if sched["timing"] == "in_advance" else 0.995    # prior quarter-end, or the average daily value
        value = self.value[a["id"]] * factor
        basis = household_value[(a["firm_crd"], a.get("household") or a["id"])] * factor if sched["households"] \
            else value
        rate = _blended(sched["tiers"], basis)
        status, note = "Calculated", (f"Pro-rated: {days} of {quarter_days} days" if days < quarter_days else "")
        if sched["method"] == "ria_posts" and a["group"] == "pure_ria" and a["id"] == min(
                x["id"] for x in self.accounts if x["firm_crd"] == a["firm_crd"] and x["group"] == "pure_ria"
                and x["opened_on"] < start):
            rate = 0.0240                                   # one line in the RIA's own file is over the cap
        advisory = value * rate * days / 365
        custody = max(CUSTODY_MIN_QUARTER * days / quarter_days, value * CUSTODY_RATE * days / 365)
        if a["group"] == "self_directed":
            advisory, rate = 0.0, 0.0
            note = "Custody fee only: the RIA doesn't bill the self-directed account"
        fee_codes = blocking(a, Activity.FEE_DEDUCTION)
        if fee_codes:
            status, note = "Can't debit", f"{fee_codes[0]} hold: no fee deduction until the outstanding item arrives"
        elif sched.get("max_rate") and rate > sched["max_rate"]:
            status, note = "Rejected line", (f"The RIA's file bills {rate:.2%} a year, above the {sched['max_rate']:.2%}"
                                             f" cap in the client agreement: sent back to the RIA")
        elif self.closing and a["id"] == self.closing["id"] and sched["timing"] == "in_advance":
            unused = max(0, (end - self.closing["leaves_on"]).days)
            status = "Refund due"
            note = (f"The account leaves by ACATS on {_day(self.closing['leaves_on'])}: refund "
                    f"${advisory * unused / days:,.2f} for the {unused} unused days")
        elif advisory + custody > self.cash[a["id"]]:
            status, note = "Cash short", (f"${self.cash[a['id']]:,.2f} in cash, below the fee: the RIA raises cash or "
                                          f"names another account in the household to bill")
        return {"run": run_id, "account": a["id"], "client": a["name"], "firm_crd": a["firm_crd"],
                "billable_value": _money(value), "tier_basis_value": _money(basis), "annual_rate": round(rate, 5),
                "days_billed": days, "period_days": quarter_days, "advisory_fee": _money(advisory),
                "custody_fee": _money(custody), "total_fee": _money(advisory + custody),
                "cash_available": self.cash[a["id"]], "status": status, "note": note,
                "hold_code": fee_codes[0] if fee_codes else None}

    def _run(self, run_id, crd, sched, start, end, bill, rows, note, catch_up=False) -> None:
        firm = self.firm(crd)
        d = bdays_between(bill, self.today)
        exceptions = [x for x in rows if x["status"] != "Calculated"]
        if sched["method"] == "ria_posts":
            timeline = [f"{_day(bill)}: fee file and good-faith attestation received from {firm}",
                        f"{_day(bill)}: each line checked against the client agreement's cap and the cash available"]
            if d < 1:
                stage, row_status = "File received: checking each line", None
            else:
                stage, row_status = (f"Posted {_day(bday(bill, 1))}; {len(exceptions)} line"
                                     f"{'' if len(exceptions) == 1 else 's'} sent back or held"), "Posted"
                timeline.append(f"{_day(bday(bill, 1))}: posted after maker-checker in billing operations")
        else:
            due, approved, posted = bday(bill, 3), bday(bill, 2), bday(bill, 5)
            timeline = [f"{_day(bill)}: calculated by the custodian and sent to {firm} to approve"]
            if catch_up or d < 3:
                stage, row_status = f"Waiting for {firm} to approve (due {_day(due)}): nothing posts until then", None
            elif d < 5:
                stage, row_status = f"Approved by {firm}: posts {_day(posted)} after maker-checker", "Approved"
                timeline.append(f"{_day(approved)}: approved by {firm}")
            else:
                stage, row_status = f"Posted {_day(posted)}", "Posted"
                timeline += [f"{_day(approved)}: approved by {firm}",
                             f"{_day(posted)}: posted after maker-checker in billing operations"]
        for x in rows:
            if row_status and x["status"] == "Calculated":
                x["status"] = row_status
        self.fees += rows
        self.billing_runs.append({
            "run": run_id, "firm": firm, "firm_crd": crd, "schedule": sched["describe"],
            "method": "RIA posts its own fees" if sched["method"] == "ria_posts" else "Custodian calculates; RIA approves",
            "kind": "Catch-up for new accounts" if catch_up else
            f"Quarterly, {'in advance' if sched['timing'] == 'in_advance' else 'in arrears'}",
            "period": f"{start.isoformat()} to {end.isoformat()}", "bill_date": bill.isoformat(),
            "accounts": len(rows), "advisory_fees": _money(sum(x["advisory_fee"] for x in rows)),
            "custody_fees": _money(sum(x["custody_fee"] for x in rows)),
            "total_fees": _money(sum(x["total_fee"] for x in rows)), "exceptions": len(exceptions),
            "status": stage, "timeline": timeline, "note": note})

    # ------------------------------------------------------------ settlement: three layers
    def settle(self) -> None:
        net: dict[str, float] = {}
        for x in self.allocations:
            if x["settle_date"] != self.today.isoformat() or not x["quantity"]:
                continue
            channel = "Fund/SERV" if BY_TICKER[x["ticker"]].asset_type == "mutual_fund" else "NSCC (CNS)"
            net[channel] = net.get(channel, 0) + (x["amount"] if x["side"] == "Sell" else -x["amount"])
        for m in self.movements:
            signed = m["amount"] if m["direction"] == "In" else -m["amount"]
            if m["rail"] == "ACH" and m["date"] == self.yesterday.isoformat() and m["status"] == "Settled":
                net["ACH (settling today)"] = net.get("ACH (settling today)", 0) + signed
            if m["rail"] == "Fedwire" and m["date"] == self.today.isoformat() and m["status"] == "Sent":
                net["Fedwire (client wires)"] = net.get("Fedwire (client wires)", 0) + signed
        flows = [{"channel": k, "net": _money(v), "direction": "Receive" if v >= 0 else "Pay"}
                 for k, v in sorted(net.items())]
        total = _money(sum(net.values()))
        by_firm: dict[str, list[float]] = {}
        for a in self.accounts:
            by_firm.setdefault(a["firm_crd"], []).append(self.cash[a["id"]])
        layers = [{"firm": self.firm(c), "firm_crd": c, "client_accounts": len(v), "client_cash": _money(sum(v)),
                   "ria_master_cash": _money(sum(v)), "difference": 0.0} for c, v in sorted(by_firm.items())]
        client_cash = _money(sum(self.cash.values()))
        ftr = _money(sum(f["amount"] for f in self.fails if f["side"] == "Buy"))
        ftd = _money(sum(f["amount"] for f in self.fails if f["side"] == "Sell"))
        held = sum(p["quantity"] for p in self.positions if p["ticker"] == "GBEC")
        self.dividend = _money(held * 0.42)
        liabilities = [("2100", "Client cash", client_cash),
                       ("2150", "Dividends received, not yet allocated", self.dividend),
                       ("2190", "Suspense: unapplied receipts", self.unapplied),
                       ("2300", "Fails to receive: payable to the street", ftr)]
        bank = _money(sum(x[2] for x in liabilities) - ftd - self.r01)    # the R01 deposit never arrived
        assets = [("1010", "Settlement bank", bank), ("1300", "Fails to deliver: receivable from the street", ftd)]
        gap = _money(sum(x[2] for x in assets) - sum(x[2] for x in liabilities))
        r01 = next((m for m in self.movements if m["id"] == "ACH-R01"), None)
        self.settlement = {
            "date": self.today.isoformat(), "flows": flows, "net": total,
            "funding": ("The settlement bank receives the net." if total >= 0 else
                        "The settlement bank pays the net from its opening balance; nothing is borrowed."),
            "layers": layers,
            "gl": [{"account": n, "name": name, "side": "Asset", "balance": v} for n, name, v in assets] +
                  [{"account": n, "name": name, "side": "Liability", "balance": v} for n, name, v in liabilities],
            "gap": gap,
            "checks": [
                {"check": "Each RIA master account equals the sum of its clients' accounts", "result": "Matches",
                 "detail": f"{len(layers)} RIAs, {len(self.accounts)} client accounts"},
                {"check": "The RIA master accounts equal general ledger client cash (2100)", "result": "Matches",
                 "detail": f"${client_cash:,.2f}"},
                {"check": "General ledger assets equal liabilities",
                 "result": "Matches" if not gap else f"Off by ${-gap:,.2f}",
                 "detail": (f"Explained by break CB-R01: the ACH return on {r01['account']} isn't reversed on the "
                            f"client ledger yet" if gap and r01 else "Nothing unexplained")},
            ]}

    # ------------------------------------------------------------ reconciliation: books vs. the street
    def reconcile(self) -> None:
        books: dict[str, float] = {}
        for p in self.positions:
            books[p["ticker"]] = round(books.get(p["ticker"], 0) + p["quantity"], 3)

        def brk(bid, kind, item, location, book, street, value, opened, cause, owner, status, action):
            if isinstance(item, Security):
                item = f"{item.ticker} {item.name}"
            self.breaks.append({
                "id": bid, "kind": kind, "item": item, "location": location, "books": book, "street": street,
                "difference": round(street - book, 3), "value": _money(value), "opened": opened.isoformat(),
                "age_days": bdays_between(opened, self.today), "cause": cause, "owner": owner, "status": status,
                "next_action": action})

        for f in self.fails:
            if f["side"] != "Buy":
                continue                        # a fail to deliver leaves books and DTC agreeing; it's a receivable
            s = BY_TICKER[f["ticker"]]
            brk(f"RB-{f['id']}", "Position", s, s.location, books[f["ticker"]],
                round(books[f["ticker"]] - f["quantity"], 3), f["amount"], date.fromisoformat(f["settle_date"]),
                f"{f['type']}: {f['cause']}", "Trade operations", "Open: clears when the shares arrive", f["action"])
        if "RDGIX" in books:
            s = BY_TICKER["RDGIX"]
            extra = round(books["RDGIX"] * 0.0042, 3)
            brk("RB-DRIP", "Position", s, s.location, books["RDGIX"], round(books["RDGIX"] + extra, 3),
                extra * s.price, self.yesterday, "The fund reinvested its dividend at the transfer agent; the new "
                "shares aren't booked to holders yet", "Corporate actions", "Researching",
                "Book each holder's reinvested shares from the fund's dividend notice")
        if "SFLX" in books:
            s = BY_TICKER["SFLX"]
            brk("RB-SFLX", "Position", s, s.location, books["SFLX"], books["SFLX"] + 12, 12 * s.price,
                bday(self.today, -7), "Unexplained: DTC holds 12 more shares than the books", "Reconciliation",
                "Escalated: open more than 5 business days",
                "Trace the DTC activity; a late transfer delivery with no client match is the likely cause")
        if self.dividend:
            brk("CB-DIV", "Cash", "GBEC cash dividend, $0.42 a share", "DTC", 0.0, self.dividend, self.dividend,
                self.today, "Paid by DTC today and held in 2150; not yet allocated to the holders' accounts",
                "Corporate actions", "Allocating: posts to holders tonight", "Allocate by record-date position")
        if self.r01:
            m = next(m for m in self.movements if m["id"] == "ACH-R01")
            brk("CB-R01", "Cash", f"ACH return R01 on {m['account']}", "Settlement bank", self.r01, 0.0, self.r01,
                self.yesterday, "The return reached the bank, but the client ledger still shows the deposit",
                "Money movement", "Open", "Reverse the deposit on the client ledger and tell the RIA")
        if self.unapplied:
            brk("CB-SUSP", "Cash", "Suspense 2190: unapplied wire", "Settlement bank", 0.0, self.unapplied,
                self.unapplied, self.yesterday, "A wire whose account reference doesn't match any account",
                "Money movement", "Open: waiting for the sender's confirmation",
                "Apply it to the account once the sender confirms")
        self.breaks.sort(key=lambda x: (-x["age_days"], x["id"]))

    # ------------------------------------------------------------ the result
    def result(self) -> dict:
        assets: dict[str, float] = {}
        for a in self.accounts:
            assets[a["firm_crd"]] = assets.get(a["firm_crd"], 0) + self.value[a["id"]]
        return {
            "summary": {"as_of": self.today.isoformat(), "accounts": len(self.accounts),
                        "assets": _money(sum(self.value.values())), "client_cash": _money(sum(self.cash.values())),
                        "by_firm": [{"firm": self.firm(c), "firm_crd": c,
                                     "accounts": sum(a["firm_crd"] == c for a in self.accounts), "assets": _money(v)}
                                    for c, v in sorted(assets.items())]},
            "assumptions": ASSUMPTIONS,
            "securities": [s.to_dict() for s in SECURITIES],
            "accounts": [self._ref(a) | {
                "firm": self.firm(a["firm_crd"]), "registration": a["registration_type"], "group": a["group"],
                "path": a.get("path"), "model": a["model"], "opened": a["opened_on"].isoformat(),
                "codes": list(a["codes"]), "funding_in_transit": a["id"] in self.in_transit,
                "cash": self.cash[a["id"]], "value": self.value[a["id"]]} for a in self.accounts],
            "positions": self.positions, "orders": self.orders, "allocations": self.allocations, "fails": self.fails,
            "movements": self.movements,
            "transfers": sorted(self.transfers, key=lambda t: (t["requested"], t["id"]), reverse=True),
            "billing_runs": self.billing_runs, "fees": self.fees, "settlement": self.settlement,
            "breaks": self.breaks,
        }
