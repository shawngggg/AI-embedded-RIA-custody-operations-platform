"""
The three-layer ledger's rules: the chart of accounts, how a movement becomes balanced legs, and the rollup invariant.

Every entry posts legs at three layers:
- client: one ledger account per client account (ACC-2001), its balance what the custodian owes that client;
- master: one per RIA (MASTER-310001), always the sum of its clients;
- gl: the custodian's general ledger, where every entry balances (debits equal credits) per asset.

Assets are "USD" or a security id. Units are integers in millionths (UNIT), so cash and fractional fund shares are exact.
Client and master legs are positive when the client's balance grows. GL legs are positive for a debit. The client
liability control accounts (2100 for cash, 2500 for securities) carry the same buckets as the client layer, and the
invariant is: for every asset and bucket, the clients of each RIA sum to its master, and the masters sum to minus the
control account (a credit balance).

Buckets: settled; pending (in transit, not yet the client's to use); restricted (returnable ACH funds on hold).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

UNIT = 1_000_000
CASH = "USD"

CHART = {
    "1010": ("Settlement bank", "asset"),
    "1020": ("Cash in transit: ACH and wires due in", "asset"),
    "1030": ("Special reserve bank account for the exclusive benefit of customers (15c3-3)", "asset"),
    "1300": ("Fails to deliver: receivable from the street", "asset"),
    "1500": ("Securities at DTC", "asset"),
    "1510": ("Securities at transfer agents (Fund/SERV)", "asset"),
    "1520": ("Securities held at sponsors", "asset"),
    "1590": ("Securities in transit: incoming transfers", "asset"),
    "2100": ("Client cash (owed to clients)", "liability"),
    "2190": ("Suspense: unapplied receipts", "liability"),
    "2200": ("In-transit suspense: outgoing cash", "liability"),
    "2210": ("Securities due out: outgoing transfers", "liability"),
    "2300": ("Fails to receive: payable to the street", "liability"),
    "2400": ("Advisory fees payable to RIAs", "liability"),
    "2500": ("Client securities (owed to clients)", "liability"),
    "2600": ("Unclaimed property (held for the states)", "liability"),
    "2700": ("Federal withholding payable", "liability"),
    "4100": ("Custody fee income", "income"),
    "5100": ("Bank fees", "expense"),
}
CONTROL = {"cash": "2100", "securities": "2500"}
LOCATION_GL = {"DTC": "1500", "Fund/SERV (transfer agent)": "1510", "Sponsor (non-traded)": "1520"}
BUCKETS = ("settled", "pending", "restricted")


def units(amount: float) -> int:
    return int(round(amount * UNIT))


def amount(u: int) -> float:
    return round(u / UNIT, 6)


def control_for(asset: str) -> str:
    return CONTROL["cash"] if asset == CASH else CONTROL["securities"]


def master_id(firm_crd: str) -> str:
    return f"MASTER-{firm_crd}"


@dataclass(frozen=True)
class Leg:
    layer: str          # client | master | gl
    account: str        # ACC-2001, MASTER-310001, or a GL number
    asset: str          # USD or a security id
    bucket: str         # settled | pending | restricted
    units: int

    def to_dict(self) -> dict:
        return {"layer": self.layer, "account": self.account, "asset": self.asset, "bucket": self.bucket,
                "units": self.units, "amount": amount(self.units)}


def client_move(account_id: str, firm_crd: str, asset: str, qty: int, bucket: str, contra: str | None,
                contra_bucket: str = "settled") -> list[Leg]:
    """A change of qty in a client's balance, mirrored at its RIA master and the GL control account, with the GL
    contra leg that pays for it (None when another client move in the same entry balances it)."""
    ctl = control_for(asset)
    legs = [Leg("client", account_id, asset, bucket, qty), Leg("master", master_id(firm_crd), asset, bucket, qty),
            Leg("gl", ctl, asset, bucket, -qty)]
    if contra:
        legs.append(Leg("gl", contra, asset, contra_bucket, qty))
    return legs


def rebucket(account_id: str, firm_crd: str, asset: str, qty: int, src: str, dst: str) -> list[Leg]:
    """Move qty from one bucket to another inside a client's balance (pending to settled, restricted to settled)."""
    return client_move(account_id, firm_crd, asset, -qty, src, None) + \
        client_move(account_id, firm_crd, asset, qty, dst, None)


def gl_move(debit: str, credit: str, asset: str, qty: int) -> list[Leg]:
    return [Leg("gl", debit, asset, "settled", qty), Leg("gl", credit, asset, "settled", -qty)]


def relink(account_id: str, old_crd: str, new_crd: str, balances: dict[tuple[str, str], int]) -> list[Leg]:
    """An account moving from one RIA's master to another's: the client layer and the GL don't change."""
    legs = []
    for (asset, bucket), qty in balances.items():
        if qty:
            legs += [Leg("master", master_id(old_crd), asset, bucket, -qty),
                     Leg("master", master_id(new_crd), asset, bucket, qty)]
    return legs


def validate(legs: list[Leg], firm_of: dict[str, str], *, relink_of: tuple[str, str, str] | None = None) -> list[str]:
    """Why an entry can't post (an empty list means it balances). firm_of maps each client account to its RIA's CRD.
    relink_of (account, old_crd, new_crd) allows the one entry that moves a client between masters."""
    errors = []
    if not legs:
        return ["An entry needs at least one leg"]
    for leg in legs:
        if leg.layer not in ("client", "master", "gl"):
            errors.append(f"Unknown layer {leg.layer}")
        if leg.bucket not in BUCKETS:
            errors.append(f"Unknown bucket {leg.bucket}")
        if leg.layer == "gl" and leg.account not in CHART:
            errors.append(f"GL account {leg.account} isn't in the chart")
        if leg.units == 0:
            errors.append(f"A zero leg on {leg.account}")
    gl = defaultdict(int)
    for leg in legs:
        if leg.layer == "gl":
            gl[leg.asset] += leg.units
    for asset, net in gl.items():
        if net:
            errors.append(f"GL legs for {asset} don't balance: off by {amount(net)}")
    # rollup: clients of each master, and masters to the control account
    clients, masters, control = defaultdict(int), defaultdict(int), defaultdict(int)
    for leg in legs:
        key = (leg.asset, leg.bucket)
        if leg.layer == "client":
            crd = firm_of.get(leg.account)
            if crd is None:
                errors.append(f"{leg.account} has no RIA master")
                continue
            clients[(master_id(crd), *key)] += leg.units
        elif leg.layer == "master":
            masters[(leg.account, *key)] += leg.units
        elif leg.account == control_for(leg.asset):
            control[key] += leg.units
    if relink_of:
        _acct, old, new = relink_of
        net = defaultdict(int)
        for (m, asset, bucket), q in masters.items():
            net[(asset, bucket)] += q
        named = {m for (m, _a, _b) in masters} <= {master_id(old), master_id(new)}
        if any(net.values()) or clients or control or gl or not named:
            errors.append("A relink moves balances between the two masters and changes nothing else")
        return errors
    for key in set(clients) | set(masters):
        if clients.get(key, 0) != masters.get(key, 0):
            errors.append(f"{key[0]} {key[1]} {key[2]}: clients move {amount(clients.get(key, 0))}, the master "
                          f"{amount(masters.get(key, 0))}")
    totals = defaultdict(int)
    for (m, asset, bucket), q in masters.items():
        totals[(asset, bucket)] += q
    for key in set(totals) | set(control):
        if totals.get(key, 0) != -control.get(key, 0):
            errors.append(f"{key[0]} {key[1]}: masters move {amount(totals.get(key, 0))}, the control account "
                          f"{amount(-control.get(key, 0))}")
    return errors


def proof(balances: list[tuple[str, str, str, str, int]], firm_of: dict[str, str]) -> dict:
    """The three-layer proof over every balance: rows (layer, account, asset, bucket, units). Returns the checks and
    any differences, by asset and bucket."""
    clients, masters, control = defaultdict(int), defaultdict(int), defaultdict(int)
    for layer, account, asset, bucket, u in balances:
        if layer == "client":
            clients[(master_id(firm_of.get(account, "?")), asset, bucket)] += u
        elif layer == "master":
            masters[(account, asset, bucket)] += u
        elif account == control_for(asset):
            control[(asset, bucket)] += u
    master_diffs = [{"master": k[0], "asset": k[1], "bucket": k[2], "clients": amount(clients.get(k, 0)),
                     "master_balance": amount(masters.get(k, 0))}
                    for k in sorted(set(clients) | set(masters)) if clients.get(k, 0) != masters.get(k, 0)]
    totals = defaultdict(int)
    for (m, asset, bucket), q in masters.items():
        totals[(asset, bucket)] += q
    gl_diffs = [{"asset": k[0], "bucket": k[1], "masters": amount(totals.get(k, 0)),
                 "control": amount(-control.get(k, 0))}
                for k in sorted(set(totals) | set(control)) if totals.get(k, 0) != -control.get(k, 0)]
    gl_net = defaultdict(int)
    for layer, account, asset, bucket, u in balances:
        if layer == "gl":
            gl_net[asset] += u
    unbalanced = [{"asset": a, "net": amount(n)} for a, n in sorted(gl_net.items()) if n]
    return {"clients_equal_masters": not master_diffs, "masters_equal_gl": not gl_diffs,
            "gl_balances": not unbalanced, "master_differences": master_diffs, "gl_differences": gl_diffs,
            "gl_unbalanced": unbalanced}
