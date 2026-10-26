"""
The nightly batch: what runs at the end of every business day, in order.

1. The settlement cycle (rail answers, ACH holds, unapplied cash, the three-layer check, customer protection under Rule
   15c3-3, net funding, SLOA reconfirmations).
2. Reconciliation against DTC, the transfer agents, the sponsors, and the banks.
3. Ongoing sanctions screening of everyone against the flagged list.
4. NIGO aging: reminders, and withdrawals at 30 days.
5. After a quarter ends, its account statements (once).

Cloud Scheduler calls POST /api/nightly/run with the X-Nightly-Token header (the token lives in Secret Manager; see
docs/DEPLOY.md). The operations supervisor can also run it by hand. Without the token configured, only the hand run
works.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import audit, nigo, recon, screening, settlement, statements
from .config import settings
from .orm import User


def run(db: Session, actor: User | None) -> dict:
    cycle = settlement.run_cycle(db, actor)
    rec = recon.run(db, actor)
    screened = screening.run(db, actor, "nightly")
    aged = nigo.run(db, actor)
    from .orm import Statement
    from sqlalchemy import select
    start, end = statements.previous_quarter()
    due = db.scalar(select(Statement.id).where(Statement.period_end == end)) is None
    made = statements.generate(db, actor, (start, end)) if due else None      # once, after the quarter ends
    protect = (cycle.result or {}).get("protection") or {}
    out = {"on": settings.today().isoformat(), "by": actor.display_name if actor else "Scheduler",
           "settlement": {"run": cycle.id, "steps": len(cycle.result["steps"]),
                          "reserve_deficit": (protect.get("reserve") or {}).get("deficit", 0),
                          "control_deficits": len((protect.get("possession") or {}).get("deficits", []))},
           "recon": {"run": rec.id, "breaks": rec.result["breaks"], "certified": rec.result["certifiable"]},
           "screening": {"parties": screened["parties"], "alerts": len(screened["alerts"])},
           "nigo": {"reminded": len(aged["reminded"]), "withdrawn": len(aged["withdrawn"])},
           "statements": {"period_end": end.isoformat(), "generated": made["generated"] if made else 0}}
    audit.record(db, actor, "nightly_run", "batch", out["on"], **{k: v for k, v in out.items() if k != "on"})
    return out
