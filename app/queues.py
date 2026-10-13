"""Work queues, SLA clocks, and who may decide what. SLA hours are demo policy."""
from __future__ import annotations

QUEUES = {
    "items_requested": {"label": "Items requested", "owner": "Onboarding (waiting on the RIA)",
                        "roles": ["ops_analyst", "ops_supervisor"], "teams": ["onboarding"], "sla_hours": 120},
    "compliance_review": {"label": "Compliance review", "owner": "AML compliance",
                          "roles": ["aml_compliance"], "sla_hours": 48},
    "sanctions_review": {"label": "Sanctions review", "owner": "Sanctions team",
                         "roles": ["sanctions"], "sla_hours": 24},
    "edd_review": {"label": "EDD pending", "owner": "AML compliance",
                   "roles": ["aml_compliance"], "sla_hours": 120},
    "confirmation": {"label": "Fraud-pattern confirmation", "owner": "Client service",
                     "roles": ["ops_analyst", "ops_supervisor"], "teams": ["client_service"], "sla_hours": 24},
    "verification": {"label": "Identity verification", "owner": "CIP and identity verification",
                     "roles": ["ops_analyst", "ops_supervisor"], "teams": ["cip"], "sla_hours": 72},
    "principal_acceptance": {"label": "Principal acceptance", "owner": "RIA service team (registered principal)",
                             "roles": ["ria_service", "ria_service_lead"], "sla_hours": 24},
    "rework": {"label": "Rework after a rescission", "owner": "Onboarding",
               "roles": ["ops_analyst", "ops_supervisor"], "teams": ["onboarding"], "sla_hours": 24},
}


def works_queue(user, key: str) -> bool:
    """Whether this queue is the user's: their role works it, and for operations analysts, their team does.
    Supervisors oversee every operations queue."""
    spec = QUEUES[key]
    if user.role not in spec["roles"]:
        return False
    return user.role != "ops_analyst" or not spec.get("teams") or user.team in spec["teams"]

_BY_STATUS = {
    "PENDING_ITEMS": "items_requested",
    "PENDING_REVIEW": "compliance_review",
    "PENDING_SANCTIONS": "sanctions_review",
    "PENDING_EDD": "edd_review",
    "PENDING_CONFIRMATION": "confirmation",
    "PENDING_VERIFICATION": "verification",
    "PENDING_ACCEPTANCE": "principal_acceptance",
    "OPEN_RESTRICTED": "items_requested",     # open, but waiting on non-regulatory items from the RIA
    "PENDING_REWORK": "rework",
}

CLOSED = {"ACTIVE", "OPEN", "APPLIED", "DECLINED", "BLOCKED", "REJECTED", "REDIRECTED"}


def queue_for(kind: str, status: str) -> str | None:
    if kind == "change" and status == "RETURNED":
        return "items_requested"
    return _BY_STATUS.get(status)


def is_closed(kind: str, status: str) -> bool:
    return status in CLOSED or (status == "RETURNED" and kind in ("firm", "account"))


# Decisions people make while a case waits. Maker-checker: a different person
# from the approver roles must approve what the proposer submits.
DECISIONS = {
    "sanctions": {"label": "Sanctions determination", "statuses": ["PENDING_SANCTIONS"],
                  "values": {"false_positive": "False positive: release", "true_match": "True match: block"},
                  "proposer": ["sanctions"], "approver": ["sanctions"]},
    "edd": {"label": "EDD decision", "statuses": ["PENDING_EDD"],
            "values": {"approved": "Approve EDD", "declined": "Decline"},
            "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    "compliance_signoff": {"label": "Compliance sign-off", "statuses": ["PENDING_REVIEW"],
                           "values": {"signed_off": "Sign off the registration findings"},
                           "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    "confirmation": {"label": "RIA confirmation and client callback", "statuses": ["PENDING_CONFIRMATION"],
                     "values": {"confirmed": "Confirmed by the RIA and the client", "rejected": "Not confirmed"},
                     "proposer": ["ops_analyst", "ops_supervisor"], "approver": ["ops_supervisor"]},
    "verification": {"label": "Identity verification of new parties", "statuses": ["PENDING_VERIFICATION"],
                     "values": {"verified": "Identity verified"},
                     "proposer": ["ops_analyst", "ops_supervisor"], "approver": ["ops_supervisor"]},
    # Not maker-checker: operations prepared the case, and a registered principal accepts it directly.
    # Gated on the person's FINRA registration, not only the role (see routers/cases.py).
    "acceptance": {"label": "Principal acceptance (FINRA Rule 4512)", "statuses": ["PENDING_ACCEPTANCE"],
                   "values": {"accepted": "Accepted", "declined": "Declined"},
                   "proposer": ["ria_service", "ria_service_lead"], "approver": ["ria_service", "ria_service_lead"],
                   "direct": True},
    # Requested through routers/cases.py (it restricts the account at once); approved by the team lead.
    "rescission": {"label": "Rescind the principal's acceptance", "statuses": ["OPEN", "OPEN_RESTRICTED", "ACTIVE"],
                   "values": {"review": "Back to the principal for review", "operations": "Back to operations for rework"},
                   "proposer": ["ria_service", "ria_service_lead"], "approver": ["ria_service_lead"], "custom": True},
}
