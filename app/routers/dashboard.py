"""Operations dashboard: queues, SLA breaches, straight-through and NIGO rates, time in each state."""
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..orm import Case, CaseTransition, Restriction, User
from ..queues import QUEUES
from ..security import require_internal
from ..views import sla_state

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
WAITING = ["PENDING_ITEMS", "PENDING_REVIEW", "PENDING_SANCTIONS", "PENDING_EDD", "PENDING_CONFIRMATION",
           "PENDING_VERIFICATION", "RETURNED"]


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("")
def dashboard(user: User = Depends(require_internal), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    cases = db.scalars(select(Case)).all()
    by_status = defaultdict(int)
    for c in cases:
        by_status[c.status] += 1
    opened = [c for c in cases if c.status in ("OPEN", "ACTIVE")]
    stp = [c for c in opened if c.manual_steps == 0]
    onboarding = [c for c in cases if c.kind in ("firm", "account")]
    nigo = [c for c in onboarding if c.nigo_count > 0]
    queues = []
    for key, spec in QUEUES.items():
        in_q = [c for c in cases if c.queue == key]
        queues.append({"key": key, "label": spec["label"], "count": len(in_q),
                       "breached": sum(1 for c in in_q if sla_state(c, now)["breached"])})
    hours = defaultdict(list)
    transitions = db.scalars(select(CaseTransition).order_by(CaseTransition.case_id, CaseTransition.at)).all()
    by_case = defaultdict(list)
    for t in transitions:
        by_case[t.case_id].append(t)
    for case_id, ts in by_case.items():
        for i, t in enumerate(ts):
            if t.to_status in WAITING:
                end = _aware(ts[i + 1].at) if i + 1 < len(ts) else now
                hours[t.to_status].append((end - _aware(t.at)).total_seconds() / 3600)
    codes = db.scalars(select(Restriction).where(Restriction.active.is_(True))).all()
    code_counts = defaultdict(int)
    for r in codes:
        code_counts[r.code] += 1
    return {
        "cases": len(cases), "by_status": dict(by_status),
        "straight_through": {"opened": len(opened), "no_manual_step": len(stp),
                             "rate": round(len(stp) / len(opened), 3) if opened else None},
        "nigo": {"onboarding_cases": len(onboarding), "with_nigo": len(nigo),
                 "rate": round(len(nigo) / len(onboarding), 3) if onboarding else None},
        "queues": queues,
        "hours_in_state": {k: {"cases": len(v), "average": round(sum(v) / len(v), 1)} for k, v in hours.items()},
        "active_codes": dict(code_counts),
    }
