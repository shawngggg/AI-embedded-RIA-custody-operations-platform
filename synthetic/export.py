"""
Write the demo's synthetic data to files: the onboarding and CIP cases as the seed creates them, the release 2 books
(the three-layer ledger, every transfer and money movement case, receipts, settlement, and reconciliation breaks), and
the release 3 book of trading and billing built on the ledger's positions.

    python -m synthetic.export                 # writes synthetic/data/
    python -m synthetic.export --out somewhere

It seeds a throwaway SQLite database, exactly as the demo does on start, so the files match what the app shows on the
day they're written. The seed dates cases relative to now, so a rerun on another day moves the dates with it.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = {
    # Onboarding and CIP (release 1)
    "cases.csv": "Every case the seed creates (onboarding, accounts, changes, transfers, money movement): status and queue",
    "people.csv": "CIP: each person on a firm or account application, with what was screened and verified",
    "restrictions.csv": "Active restriction codes and why they were placed",
    # The books (release 2): what the ledger holds and how it got there
    "accounts.csv": "The open accounts: RIA, registration, restriction codes, and the ledger's cash and value",
    "securities.csv": "The security master: synthetic CUSIP-format IDs with valid check digits, prices, where held, "
                      "and whether the platform accepts each one",
    "positions.csv": "Settled positions by account, from the ledger",
    "balances.csv": "Every client account's balance by asset and bucket (settled, pending, restricted)",
    "masters.csv": "Each RIA master account: cash by bucket and securities value; it equals the sum of its clients",
    "ledger.csv": "The general ledger: cash and securities value by chart account",
    "ledger_checks.csv": "The three-layer checks: clients equal their RIA master; masters equal the GL; the GL balances",
    "ledger_entries.csv": "Every posting, with the case or receipt behind it",
    "ledger_legs.csv": "Each posting's legs at the client, RIA master, and general ledger layers (they sum to zero)",
    "transfers.csv": "Every transfer and money movement case: wires, ACH, journals, IRA distributions, ACATS, LOI",
    "transfer_positions.csv": "Each ACATS or LOI case's positions and the route the transferability rules gave them",
    "bank_links.csv": "Verified bank accounts in the client's name (money moves only to or from these)",
    "receipts.csv": "Money that arrived at the settlement bank with no request behind it, and what happened to it",
    "settlement_flows.csv": "Today's cash at the settlement bank, netted by channel",
    "recon_breaks.csv": "Books against the street (DTC, transfer agents, sponsors, the bank): each break, its cause, and "
                        "how it was resolved",
    # Trading and billing (release 3 previews), built on the ledger's positions
    "orders.csv": "The trading blotter for yesterday and today: block orders, fund orders, self-directed orders",
    "allocations.csv": "Each order's allocations to accounts at one average price",
    "fails.csv": "Open settlement fails, with Reg SHO close-out dates where they apply",
    "billing_runs.csv": "Fee runs by RIA: schedule, period, status, totals",
    "fees.csv": "Fees by account in each run, with exceptions",
    "book.json": "The release 3 book in one file, as the preview API serves it to operations",
}


def _rows(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for r in rows:
        fields += [k for k in r if k not in fields]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in r.items()})


def _check_layers(legs: list[dict]) -> None:
    """Fail the export if any posting breaks the rollup, so the README's claim stays true."""
    sums: dict[tuple, list[float]] = {}
    for g in legs:
        s = sums.setdefault((g["entry"], g["asset"]), [0.0, 0.0, 0.0, 0.0])    # client, master, gl, control
        i = {"client": 0, "master": 1, "gl": 2}[g["layer"]]
        s[i] += g["amount"]
        if g["layer"] == "gl" and g["account"] in ("2100", "2500"):
            s[3] += g["amount"]
    bad = [k for k, (c, m, gl, ctl) in sums.items() if abs(c - m) > 1e-6 or abs(m + ctl) > 1e-6 or abs(gl) > 1e-6]
    if bad:
        raise SystemExit(f"{len(bad)} postings break the three-layer rollup: {bad[:5]}")


def main(out: Path) -> None:
    tmp = tempfile.mkdtemp(prefix="synthetic-export-")
    os.environ.update({"DATABASE_URL": f"sqlite:///{tmp}/export.db", "DEMO_MODE": "true", "ANTHROPIC_API_KEY": "",
                       "SECRET_KEY": os.environ.get("SECRET_KEY", "export-only-secret-key-not-used-for-sessions")})
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import select

    from app import book as app_book
    from app import ledger, recon, settlement
    from app.config import settings
    from app.db import SessionLocal
    from app.orm import (Account, BankLink, BankReceipt, Case, Firm, LedgerEntry, LedgerLeg, Restriction, Security,
                         StreetItem)
    from app.routers.money import transfer_row
    from app.seed import reset_database, seed
    from custody.ledger import amount

    reset_database()
    with SessionLocal() as db:
        seed(db)
        today = settings.today()
        b = app_book.current(db)
        firms = {f.id: f for f in db.scalars(select(Firm)).all()}
        cases, people, transfers, transfer_positions = [], [], [], []
        for c in db.scalars(select(Case).order_by(Case.id)).all():
            firm = firms.get(c.firm_id)
            cases.append({"case": c.id, "kind": c.kind, "title": c.title, "firm": firm.legal_name if firm else "",
                          "account": c.account_id or "", "status": c.status, "queue": c.queue or "",
                          "created": c.created_at.date().isoformat(), "nigo_count": c.nigo_count,
                          "manual_steps": c.manual_steps,
                          "identity_issue": ((c.extra or {}).get("identity_issue") or {}).get("kind", "")})
            app = c.application or {}
            if c.kind == "transfer":
                transfers.append({k: v for k, v in transfer_row(db, c, portal=False).items()})
                for d in ((c.result or {}).get("transfer") or {}).get("dispositions", []):
                    transfer_positions.append({
                        "case": c.id, "account": c.account_id, "type": app.get("type"),
                        "security_id": d["security_id"], "description": d["description"], "route": d["route"],
                        "quantity_in_kind": d.get("quantity_in_kind"),
                        "quantity_to_liquidate": d.get("quantity_to_liquidate"), "reason": d["reason"],
                        "rule": d["rule_id"]})
                continue
            for p in app.get("people", []) + app.get("holders", []) + app.get("new_parties", []):
                people.append({"case": c.id, "kind": c.kind, "account": c.account_id or "",
                               "entity": app.get("entity_name") or app.get("legal_name") or "", "name": p.get("name"),
                               "role": p.get("role"), "ownership_pct": p.get("ownership_pct"),
                               "date_of_birth": p.get("date_of_birth"), "residence": p.get("country_of_residence"),
                               "nationality": p.get("nationality"), "tax_id": p.get("tax_id"),
                               "occupation": p.get("occupation"), "identity_verified": p.get("identity_verified"),
                               "pep": p.get("pep_type") if p.get("is_pep") else "",
                               "adverse_media": p.get("adverse_media"), "case_status": c.status})
        restrictions = [{"target": r.target_id, "code": r.code, "reason": r.reason, "owner": r.owner,
                         "placed_by": r.placed_by, "placed_at": r.placed_at.date().isoformat()}
                        for r in db.scalars(select(Restriction).where(Restriction.active.is_(True))
                                            .order_by(Restriction.target_id)).all()]
        securities = [{"id": s.id, "ticker": s.ticker or "", "name": s.name, "asset_type": s.asset_type,
                       "price": s.price, "price_as_of": s.price_as_of, "location": s.location,
                       "accepted": s.accepted, "dtc_eligible": s.dtc_eligible, "fundserv_eligible": s.fundserv_eligible,
                       "sponsor_agreement": s.sponsor_agreement, "proprietary_to": s.proprietary_to or "",
                       "sponsor": s.sponsor or "", "settlement_method": s.settlement_method or "",
                       "valuation_source": s.valuation_source or ""}
                      for s in db.scalars(select(Security).order_by(Security.id)).all()]
        balances = [{"account": acct, "asset": asset, "bucket": bucket,
                     "amount": round(amount(u), 2) if asset == "USD" else amount(u)}
                    for _layer, acct, asset, bucket, u in sorted(ledger.balances(db, layer="client")) if u]
        entries = [{"entry": e.id, "ref": e.ref, "kind": e.kind, "description": e.description,
                    "case": e.case_id or "", "account": e.account_id or "", "effective": e.effective_date,
                    "posted_by": e.created_by}
                   for e in db.scalars(select(LedgerEntry).order_by(LedgerEntry.id)).all()]
        legs = [{"entry": g.entry_id, "layer": g.layer, "account": g.account, "asset": g.asset, "bucket": g.bucket,
                 "amount": amount(g.units)}
                for g in db.scalars(select(LedgerLeg).order_by(LedgerLeg.entry_id, LedgerLeg.id)).all()]
        open_ids = {a.id for a in db.scalars(select(Account).where(Account.status == "OPEN")).all()}
        bank_links = [{"id": k.id, "account": k.account_id, "bank": k.bank, "last4": k.last4, "owner": k.owner,
                       "method": k.method, "status": k.status}
                      for k in db.scalars(select(BankLink).order_by(BankLink.id)).all() if k.account_id in open_ids]
        receipts = [{k: v for k, v in settlement.receipt_dict(r, today).items() if k != "candidates"}
                    | {"best_match": ((r.candidates or [{}])[0]).get("account_id", "")}
                    for r in db.scalars(select(BankReceipt).order_by(BankReceipt.id)).all()]
        breaks = [{k: v for k, v in recon.item_dict(db, i, today).items() if k not in ("proposal",)}
                  for i in db.scalars(select(StreetItem).order_by(StreetItem.id)).all()]
        checks = [{"check": c["check"], "ok": c["ok"], "differences": len(c["differences"])}
                  for c in ledger.proof(db)["checks"]]
        tables = {
            "cases.csv": cases, "people.csv": people, "restrictions.csv": restrictions,
            "accounts.csv": b["accounts"], "securities.csv": securities, "positions.csv": b["positions"],
            "balances.csv": balances, "masters.csv": ledger.masters(db), "ledger.csv": ledger.trial_balance(db),
            "ledger_checks.csv": checks, "ledger_entries.csv": entries, "ledger_legs.csv": legs,
            "transfers.csv": transfers, "transfer_positions.csv": transfer_positions, "bank_links.csv": bank_links,
            "receipts.csv": receipts, "settlement_flows.csv": settlement.net_flows(db, today),
            "recon_breaks.csv": breaks,
            "orders.csv": b["orders"], "allocations.csv": b["allocations"], "fails.csv": b["fails"],
            "billing_runs.csv": [{k: (" | ".join(v) if k == "timeline" else v) for k, v in r.items()}
                                 for r in b["billing_runs"]],
            "fees.csv": b["fees"],
        }
        entry_count = len(entries)

    _check_layers(legs)
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("money_movements.csv",):
        (out / stale).unlink(missing_ok=True)
    for name, rows in tables.items():
        _rows(out / name, rows)
    preview = {k: b[k] for k in ("summary", "assumptions", "accounts", "securities", "positions", "orders",
                                 "allocations", "fails", "billing_runs", "fees")}
    (out / "book.json").write_text(json.dumps(preview, indent=1, default=str) + "\n")
    counts = {name: len(rows) for name, rows in tables.items()}
    lines = [
        "# Synthetic data",
        "",
        "Everything here is invented: firms, people, tax IDs (900-00-xxxx, a range no one is issued), accounts,",
        "securities, prices, banks, and amounts. It's what the demo seeds and shows, written out by",
        f"`python -m synthetic.export` on business day {today.isoformat()}.",
        "",
        "Three sources, and they agree with each other:",
        "",
        "- **Onboarding and CIP** come from the seeded cases (`synthetic/cases.py` and `app/seed.py`).",
        "- **The books** come from the release 2 ledger (`app/seed_r2.py`): each open account's opening balance or",
        "  incoming ACATS, then every transfer and money movement case the seed runs through the workflows. Every",
        "  posting has legs at three layers, and in each one, asset by asset: the client legs equal the RIA master",
        "  legs, the master legs equal minus the client control accounts (GL 2100 cash, 2500 securities), and the",
        f"  general ledger legs sum to zero. All {entry_count} postings here pass all three. Positions, balances, and",
        "  `ledger.csv` are what those postings add up to.",
        "- **Trading and billing** are the release 3 previews (`synthetic/book.py`), built on the ledger's positions:",
        "  an account with a DOCS code or an outgoing transfer trades nothing, and fees bill on ledger values.",
        "",
        "| File | Rows | What it is |",
        "| --- | ---: | --- |",
    ]
    for name, what in FILES.items():
        lines.append(f"| `{name}` | {counts.get(name, '')} | {what} |")
    lines += ["", "## Assumptions", ""] + [f"- {a}" for a in b["assumptions"]] + [""]
    (out / "README.md").write_text("\n".join(lines))
    print(f"Wrote {len(tables) + 2} files to {out}: {sum(counts.values())} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "synthetic" / "data")
    main(parser.parse_args().out)
