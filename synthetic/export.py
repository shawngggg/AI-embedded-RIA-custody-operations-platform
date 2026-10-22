"""
Write the demo's synthetic data to files: the onboarding and CIP cases as the seed creates them, and the book built from
their open accounts (positions, trades, fails, money movement, transfers, billing, settlement, reconciliation).

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
    # name: (book key or loader, what it is)
    "accounts.csv": "The open accounts the book is built from: RIA, registration, model, restriction codes, cash, value",
    "securities.csv": "The synthetic security master: CUSIP-format IDs with valid check digits, prices, where held",
    "positions.csv": "Positions by account",
    "orders.csv": "The trading blotter for yesterday and today: block orders, fund orders, self-directed orders",
    "allocations.csv": "Each order's allocations to accounts at one average price",
    "fails.csv": "Open settlement fails, with Reg SHO close-out dates where they apply",
    "money_movements.csv": "ACH, wires, journals, IRA distributions, returns, holds, and an unapplied wire",
    "transfers.csv": "Incoming and outgoing ACATS transfers and LOI re-registrations",
    "transfer_positions.csv": "Each transfer's positions and the route the transferability rules gave them",
    "billing_runs.csv": "Fee runs by RIA: schedule, period, status, totals",
    "fees.csv": "Fees by account in each run, with exceptions",
    "settlement_flows.csv": "Today's settlement netted by channel",
    "ledger.csv": "General ledger balances",
    "ledger_checks.csv": "The three-layer checks: client accounts, RIA master accounts, general ledger",
    "recon_breaks.csv": "Reconciliation breaks: books against the street, with cause, owner, and next action",
    "cases.csv": "Every onboarding, account, and change case the seed creates, with its status and queue",
    "people.csv": "CIP: each person on a firm or account application, with what was screened and verified",
    "restrictions.csv": "Active restriction codes and why they were placed",
    "book.json": "The whole book in one file, as the preview API serves it to operations",
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


def main(out: Path) -> None:
    tmp = tempfile.mkdtemp(prefix="synthetic-export-")
    os.environ.update({"DATABASE_URL": f"sqlite:///{tmp}/export.db", "DEMO_MODE": "true", "ANTHROPIC_API_KEY": "",
                       "SECRET_KEY": os.environ.get("SECRET_KEY", "export-only-secret-key-not-used-for-sessions")})
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import select

    from app import book as app_book
    from app.db import SessionLocal
    from app.orm import Case, Firm, Restriction
    from app.seed import reset_database, seed

    reset_database()
    with SessionLocal() as db:
        seed(db)
        b = app_book.current(db)
        firms = {f.id: f for f in db.scalars(select(Firm)).all()}
        cases, people = [], []
        for c in db.scalars(select(Case).order_by(Case.id)).all():
            firm = firms.get(c.firm_id)
            cases.append({"case": c.id, "kind": c.kind, "title": c.title, "firm": firm.legal_name if firm else "",
                          "account": c.account_id or "", "status": c.status, "queue": c.queue or "",
                          "created": c.created_at.date().isoformat(), "nigo_count": c.nigo_count,
                          "manual_steps": c.manual_steps,
                          "identity_issue": ((c.extra or {}).get("identity_issue") or {}).get("kind", "")})
            app = c.application or {}
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

    out.mkdir(parents=True, exist_ok=True)
    s = b["settlement"]
    tables = {
        "accounts.csv": b["accounts"], "securities.csv": b["securities"], "positions.csv": b["positions"],
        "orders.csv": b["orders"], "allocations.csv": b["allocations"], "fails.csv": b["fails"],
        "money_movements.csv": [{k: v for k, v in m.items() if k != "portal_note"} for m in b["movements"]],
        "transfers.csv": [{k: v for k, v in t.items() if k != "positions"} for t in b["transfers"]],
        "transfer_positions.csv": [{"transfer": t["id"], "account": t["account"]} | p
                                   for t in b["transfers"] for p in t["positions"]],
        "billing_runs.csv": [{k: (" | ".join(v) if k == "timeline" else v) for k, v in r.items()}
                             for r in b["billing_runs"]],
        "fees.csv": b["fees"], "settlement_flows.csv": s["flows"], "ledger.csv": s["gl"],
        "ledger_checks.csv": s["checks"], "recon_breaks.csv": b["breaks"],
        "cases.csv": cases, "people.csv": people, "restrictions.csv": restrictions,
    }
    for name, rows in tables.items():
        _rows(out / name, rows)
    (out / "book.json").write_text(json.dumps(b, indent=1, default=str) + "\n")
    counts = {name: len(rows) for name, rows in tables.items()}
    lines = [
        "# Synthetic data",
        "",
        "Everything here is invented: firms, people, tax IDs (900-00-xxxx, a range no one is issued), accounts,",
        "securities, prices, banks, and amounts. It's what the demo seeds and shows, written out by",
        "`python -m synthetic.export` on business day " + b["summary"]["as_of"] + ".",
        "",
        "The onboarding and CIP files come from the seeded cases (`synthetic/cases.py` and `app/seed.py`). The rest is",
        "the book (`synthetic/book.py`), built from the accounts those cases opened, so the files agree with each other:",
        "a DOCS account trades nothing and can't be debited for fees, every buy fail is a reconciliation break, and the",
        "general ledger is off by exactly the one open cash break.",
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
