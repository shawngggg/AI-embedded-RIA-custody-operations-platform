"""
Read-only previews of the release 2 and 3 workbenches, drawn from the synthetic book (app/book.py): transfers and money
movement, trading, billing, and settlement and reconciliation. Nothing here changes anything.

Operations teams see the whole book for their area. An RIA sees its own firm's accounts, and a client their own account.
The portal never sees the custodian's settlement, ledger, fails, or reconciliation, and never a restriction code: a held
item reads as the reason the portal already shows for that code (app/portal_view.py).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import audit, book
from ..access import capabilities
from ..db import get_db
from ..orm import PORTAL_ROLES, Firm, User
from ..portal_view import CODE_REASON, UNDER_REVIEW
from ..security import current_user

router = APIRouter(prefix="/api/preview", tags=["preview"])

CAPABILITY = {"transfers": "planned_transfers", "trading": "planned_trading", "billing": "planned_billing",
              "settlement": "planned_settlement"}
RELEASE = {"transfers": 2, "settlement": 2, "trading": 3, "billing": 3}
PORTAL_AREAS = {"ria_admin": {"transfers", "trading", "billing"}, "ria_user": {"transfers", "trading", "billing"},
                "client": {"transfers", "trading"}}


@router.get("/{area}")
def preview(area: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if area not in CAPABILITY:
        raise HTTPException(404, "No such preview")
    portal = user.role in PORTAL_ROLES
    if (area not in PORTAL_AREAS.get(user.role, set())) if portal else not capabilities(user).get(CAPABILITY[area]):
        audit.refuse(db, user, "access", f"This part of the {'portal' if portal else 'workbench'} isn't available "
                     f"to your role", target_type="endpoint", target_id=f"GET /api/preview/{area}")
    b = book.current(db)
    head = {"area": area, "release": RELEASE[area], "as_of": b["summary"]["as_of"], "assumptions": b["assumptions"],
            "label": f"Preview: synthetic data. The workflow arrives in release {RELEASE[area]}; nothing here can be "
                     f"changed."}
    if not portal:
        return head | _ops(area, b)
    if user.role == "client":
        scope = {"account": user.client_account_id}
    else:
        scope = {"firm_crd": db.get(Firm, user.firm_id).crd if user.firm_id else None}
    return head | _portal(area, b, scope)


def _ops(area: str, b: dict) -> dict:
    if area == "transfers":
        return {"transfers": b["transfers"], "movements": b["movements"]}
    if area == "trading":
        return {"orders": b["orders"], "allocations": b["allocations"], "fails": b["fails"],
                "holdings": _by_security(b["positions"]), "accounts": b["accounts"]}
    if area == "billing":
        return {"runs": b["billing_runs"], "fees": b["fees"]}
    return {"summary": b["summary"], "settlement": b["settlement"], "breaks": b["breaks"]}


def _by_security(positions: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for p in positions:
        row = out.setdefault(p["ticker"], {"ticker": p["ticker"], "security": p["security"], "cusip": p["cusip"],
                                           "asset_type": p["asset_type"], "location": p["location"], "accounts": 0,
                                           "quantity": 0.0, "market_value": 0.0})
        row["accounts"] += 1
        row["quantity"] = round(row["quantity"] + p["quantity"], 3)
        row["market_value"] = round(row["market_value"] + p["market_value"], 2)
    return sorted(out.values(), key=lambda r: -r["market_value"])


def _hold_reason(code: str | None) -> str:
    return CODE_REASON.get(code or "", UNDER_REVIEW)


def _portal(area: str, b: dict, scope: dict) -> dict:
    """An RIA's scope is its firm's accounts ({"firm_crd"}); a client's is their own account ({"account"})."""
    def mine(row: dict) -> bool:
        if "account" in scope:
            return row.get("account") == scope["account"]
        return row.get("firm_crd") == scope["firm_crd"] and row.get("account") is not None

    if area == "transfers":
        moves = []
        for m in filter(mine, b["movements"]):
            shown = {k: v for k, v in m.items() if k not in ("hold_code", "portal_note")} | {"note": m["portal_note"]}
            if m["hold_code"]:
                shown |= {"status": "On hold", "note": f"{_hold_reason(m['hold_code'])}: this waits until it's done"}
            moves.append(shown)
        return {"transfers": [t for t in b["transfers"] if mine(t)], "movements": moves}
    if area == "trading":
        if "account" in scope:      # a client sees the orders in their own account, not the RIA's blocks
            orders = [o for o in b["orders"] if o.get("account") == scope["account"]]
        else:
            orders = [o for o in b["orders"] if o["firm_crd"] == scope["firm_crd"]]
        return {"orders": orders, "allocations": [x for x in b["allocations"] if mine(x)],
                "positions": [p for p in b["positions"] if mine(p)]}
    runs = [r for r in b["billing_runs"] if r["firm_crd"] == scope.get("firm_crd")]
    return {
        "runs": [r | {"status": r["status"].replace(" after maker-checker", ""),
                      "timeline": [t.split(" after maker-checker")[0] for t in r["timeline"]]} for r in runs],
        "fees": [{k: v for k, v in f.items() if k != "hold_code"} |
                 ({"note": f"{_hold_reason(f['hold_code'])}: no fee is deducted until that's resolved"}
                  if f["hold_code"] else {}) for f in b["fees"] if mine(f)]}
