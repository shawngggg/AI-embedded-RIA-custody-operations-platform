"""
SAR referrals (walkthrough note 9, Nov. 1, 2026). When AML compliance confirms a fraudulent identity, the case is
referred to the BSA/AML officer, who decides whether a suspicious activity report is due. A SAR is filed within 30
calendar days of the initial detection of facts that may be a basis for filing (31 CFR 1023.320(b)(3)); the referral
records who referred it, who decided, and the dates, so the clock is evidenced.

The filing itself (narrative, BSA E-Filing, acknowledgment) is release 4. Until then a "file" decision says the SAR is
filed by hand. Nothing here reaches the RIA or the client: SAR confidentiality (31 CFR 1023.320(e)).
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import settings
from .orm import Case, User

DAYS = 30
SEES = ("aml_compliance", "bsa_officer")
DECISIONS = {"file": "File a SAR (filed by hand until release 4 files it through BSA E-Filing)",
             "no_sar": "No SAR: the facts don't meet the reporting threshold"}
MIN_REASON = 30


def refer(db: Session, case: Case, proposer: User | None, approver: User, reference: str, why: str) -> dict:
    """Open a referral to the BSA/AML officer, once per case."""
    extra = dict(case.extra or {})
    if extra.get("sar_referral"):
        return extra["sar_referral"]
    on = settings.today()
    ref = {"reason": why, "reference": reference, "referred_on": on.isoformat(),
           "referred_by": proposer.display_name if proposer else "AML compliance", "approved_by": approver.display_name,
           "due": (on + timedelta(days=DAYS)).isoformat(), "status": "open"}
    extra["sar_referral"] = ref
    case.extra = extra
    audit.record(db, approver, "sar_referred", "case", case.id, case_id=case.id, reference=reference, due=ref["due"])
    return ref


def view(case: Case, user: User) -> dict | None:
    ref = (case.extra or {}).get("sar_referral")
    if not ref or user.role not in SEES:
        return None
    days_left = (date.fromisoformat(ref["due"]) - settings.today()).days
    return {**ref, "case_id": case.id, "case_title": case.title, "days_left": days_left,
            "overdue": ref["status"] == "open" and days_left < 0, "can_decide": user.role == "bsa_officer"
            and ref["status"] == "open", "decisions": DECISIONS}


def open_referrals(db: Session, user: User) -> list[dict]:
    if user.role not in SEES:
        return []
    rows = db.scalars(select(Case).where(Case.kind.in_(("account", "firm", "change")))).all()
    out = [view(c, user) for c in rows if (c.extra or {}).get("sar_referral")]
    return sorted((r for r in out if r), key=lambda r: (r["status"] != "open", r["due"]))


def decide(db: Session, case: Case, user: User, decision: str, note: str) -> dict:
    ref = (case.extra or {}).get("sar_referral")
    if user.role != "bsa_officer":
        audit.refuse(db, user, "sar_decision", "The BSA/AML officer decides whether a SAR is filed", case_id=case.id)
    if not ref or ref["status"] != "open":
        raise HTTPException(409, "No open SAR referral on this case")
    if decision not in DECISIONS:
        raise HTTPException(422, f"Decide one of: {', '.join(DECISIONS)}")
    if len((note or "").strip()) < MIN_REASON:
        raise HTTPException(422, f"Say why, in {MIN_REASON} characters or more")
    extra = dict(case.extra)
    extra["sar_referral"] = {**ref, "status": "decided", "decision": decision, "decision_label": DECISIONS[decision],
                             "note": note.strip(), "decided_by": user.display_name,
                             "decided_on": settings.today().isoformat()}
    case.extra = extra
    audit.record(db, user, "sar_decided", "case", case.id, case_id=case.id, decision=decision,
                 due=ref["due"], on_time=settings.today().isoformat() <= ref["due"])
    return view(case, user)
