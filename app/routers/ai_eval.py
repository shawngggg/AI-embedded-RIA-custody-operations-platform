"""
The AI evaluation: the NIGO detection test set (onboarding/nigo_eval.py) run against the live model, on demand.

PRD guardrail 5 (pre-release testing): each prompt or model change runs against a set of known cases, tracking
detection and false positives, before it goes live. The platform administrator, who maintains rules and adapters,
runs it; every run is written to the audit log with the model and prompt version, and the latest one is the MVP
gate's measure of NIGO detection recall.
"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from extraction import DEFAULT_MODEL, SYSTEM, build_prompt, extract_firm_application
from nigo_eval import NIGO_RULES, PACKAGES, TARGET_RECALL, package_text, run

from .. import audit
from ..config import settings
from ..db import get_db
from ..orm import AuditEvent, User
from ..security import require_roles
from ..views import iso
from .intake import _usage

router = APIRouter(prefix="/api/ai-evaluation", tags=["ai evaluation"])
admin = require_roles("platform_admin")
WORKERS = 10           # read packages in parallel, so a run fits well inside the 60-second request timeout


def prompt_version() -> str:
    """A short fingerprint of the extraction instructions: a change to the prompt shows as a new version."""
    return hashlib.sha256((SYSTEM + build_prompt("")).encode()).hexdigest()[:10]


def _latest(db: Session) -> dict | None:
    e = db.scalar(select(AuditEvent).where(AuditEvent.action == "ai_evaluation_run").order_by(AuditEvent.id.desc()))
    return {**e.detail, "at": iso(e.at), "by": e.actor_name} if e else None


@router.get("")
def evaluation(user: User = Depends(admin), db: Session = Depends(get_db)):
    used = _usage(db, settings.today()).count
    db.commit()
    can_run, why = True, None
    if not settings.ai_live:
        can_run, why = False, "Live AI isn't configured: add workload identity federation or an API key (docs/DEPLOY.md)"
    elif used + len(PACKAGES) > settings.ai_daily_cap:
        can_run, why = False, (f"A run makes {len(PACKAGES)} model calls; {settings.ai_daily_cap - used} are left under "
                               f"today's cap of {settings.ai_daily_cap}")
    return {"latest": _latest(db), "can_run": can_run, "why_not": why, "model": DEFAULT_MODEL,
            "prompt_version": prompt_version(), "target": TARGET_RECALL, "rules": NIGO_RULES,
            "packages": [{"key": p.key, "about": p.about, "name": p.name, "seeded": sorted(p.seeded),
                          "text": package_text(p)} for p in PACKAGES]}


@router.post("")
def run_evaluation(user: User = Depends(admin), db: Session = Depends(get_db)):
    if not settings.ai_live:
        raise HTTPException(503, "Live AI isn't configured: add workload identity federation or an API key")
    usage = _usage(db, settings.today())
    if usage.count + len(PACKAGES) > settings.ai_daily_cap:
        raise HTTPException(409, f"A run makes {len(PACKAGES)} model calls; {settings.ai_daily_cap - usage.count} are "
                                 "left under today's cap")
    usage.count += len(PACKAGES)
    db.commit()                        # the calls count against the cap even if the run fails part way
    out = run(extract_firm_application, workers=WORKERS)
    detail = {**out, "model": DEFAULT_MODEL, "prompt_version": prompt_version(), "auth": settings.ai_auth}
    detail.pop("rules", None)
    audit.record(db, user, "ai_evaluation_run", "ai_model", DEFAULT_MODEL, **detail)
    db.commit()
    return _latest(db)
