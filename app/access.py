"""
Who sees which part of the workbench. The screens and the API use the same answers, so a page that isn't in a
person's menu also refuses them (and logs it) if they reach it directly.
"""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from . import audit
from .db import get_db
from .orm import INTERNAL_ROLES, OPS_ROLES, PORTAL_ROLES, User
from .queues import DECISIONS
from .security import current_user

# Maker-checker approvers (acceptance is a principal's direct act, so it isn't an approval)
APPROVERS = {r for spec in DECISIONS.values() if not spec.get("direct") for r in spec["approver"]}
REVIEWERS = {"periodic_review", "aml_compliance", "ops_supervisor"}
OVERSIGHT = {"ops_supervisor", "aml_compliance", "sanctions", "ria_service_lead", "platform_admin"}


def capabilities(u: User) -> dict[str, bool]:
    internal = u.role in INTERNAL_ROLES
    return {
        "work": internal, "queues": internal, "messages": internal, "risk_events": internal,
        "approvals": u.role in APPROVERS,
        "reviews": u.role in REVIEWERS,
        "intake": u.role in OPS_ROLES and u.team == "onboarding",
        "restricted_accounts": internal and u.role != "platform_admin",
        "audit": u.role in OVERSIGHT,
        "dashboard": u.role in OVERSIGHT,
        "outbox": u.role in OPS_ROLES | {"platform_admin", "ria_service_lead"},
        "planned": internal and u.role != "platform_admin",
        "admin": u.role == "platform_admin",
        "portal": u.role in PORTAL_ROLES,
    }


def require_capability(name: str):
    def guard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if not capabilities(user).get(name):
            audit.refuse(db, user, "access", "This part of the workbench isn't available to your role",
                         target_type="endpoint", target_id=f"{request.method} {request.url.path}")
        return user
    return guard
