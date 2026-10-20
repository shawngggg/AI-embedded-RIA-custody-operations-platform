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
from .queues import DECISIONS, QUEUES, works_queue
from .security import current_user

# Maker-checker approvers (acceptance is a principal's direct act, so it isn't an approval)
APPROVERS = {r for spec in DECISIONS.values() if not spec.get("direct") for r in spec["approver"]}
REVIEWERS = {"periodic_review", "aml_compliance", "ops_supervisor"}
OVERSIGHT = {"ops_supervisor", "aml_compliance", "sanctions", "ria_service_lead", "platform_admin"}
TEAM_LEADS = {"ops_supervisor", "ria_service_lead"}     # the operations dashboard is for the people who run queues
# Operations teams whose work sends notices to RIAs and clients in release 1 (onboarding, identity verification,
# account maintenance); the other teams' functions arrive in later releases
NOTICE_TEAMS = {"onboarding", "cip", "client_service"}
# The planned workbenches (release 2 and 3) show only to the people who will run them: the operations team for that
# function, the operations supervisor who oversees every team, and the RIA service team, which works with RIAs on
# trading and transfers (Shawn, Oct. 13, 2026)
PLANNED = {"transfers": ("asset_transfer", {"ria_service", "ria_service_lead"}),
           "trading": ("trade", {"ria_service", "ria_service_lead"}),
           "billing": ("billing", set())}


def _planned(u: User, which: str) -> bool:
    team, roles = PLANNED[which]
    return u.role == "ops_supervisor" or (u.role == "ops_analyst" and u.team == team) or u.role in roles


def capabilities(u: User) -> dict[str, bool]:
    internal = u.role in INTERNAL_ROLES
    # The platform administrator runs users, roles, and rules, not cases: no client data in queues or case files
    cases = internal and u.role != "platform_admin"
    return {
        "work": internal, "messages": internal, "risk_events": internal,
        "cases": cases, "queues": cases and any(works_queue(u, k) for k in QUEUES),
        "approvals": u.role in APPROVERS,
        "reviews": u.role in REVIEWERS,
        "intake": u.role in OPS_ROLES and u.team == "onboarding",
        # Onboarding and CIP review what the AI read from a package, each their own values (app/ai_suggestions.py)
        "ai_review": u.role == "ops_supervisor" or (u.role == "ops_analyst" and u.team in ("onboarding", "cip")),
        "restricted_accounts": internal and u.role != "platform_admin",
        "audit": u.role in OVERSIGHT,
        "dashboard": u.role in TEAM_LEADS,
        "outbox": u.role in {"ops_supervisor", "platform_admin", "ria_service_lead"}
                  or (u.role == "ops_analyst" and u.team in NOTICE_TEAMS),
        "planned_transfers": _planned(u, "transfers"), "planned_trading": _planned(u, "trading"),
        "planned_billing": _planned(u, "billing"),
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
