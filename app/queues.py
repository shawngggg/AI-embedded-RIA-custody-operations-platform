"""Work queues, SLA clocks, and who may decide what. SLA hours are demo policy."""
from __future__ import annotations

QUEUES = {
    # Every value the AI reads from a package waits here until a person accepts, edits, or rejects it: onboarding
    # reviews the firm and Form ADV facts, CIP the identity facts
    "ai_review": {"label": "AI suggestions to review", "owner": "Onboarding and CIP",
                  "roles": ["ops_analyst", "ops_supervisor"], "teams": ["onboarding", "cip"], "sla_hours": 24},
    "items_requested": {"label": "Items requested", "owner": "Onboarding (waiting on the RIA)",
                        "roles": ["ops_analyst", "ops_supervisor"], "teams": ["onboarding"], "sla_hours": 120},
    "compliance_review": {"label": "Compliance review", "owner": "AML compliance",
                          "roles": ["aml_compliance"], "sla_hours": 48},
    "sanctions_review": {"label": "Sanctions review", "owner": "Sanctions team",
                         "roles": ["sanctions"], "sla_hours": 24},
    "edd_review": {"label": "EDD pending", "owner": "AML compliance",
                   "roles": ["aml_compliance"], "sla_hours": 120},
    "confirmation": {"label": "Fraud-pattern confirmation", "owner": "Client service (account maintenance)",
                     "roles": ["ops_analyst", "ops_supervisor"], "teams": ["client_service"], "sla_hours": 24},
    # Account maintenance sits with client service (Shawn, Oct. 13, 2026): a change returned to the RIA or client
    # for correction waits here, not in onboarding's queue
    "maintenance_items": {"label": "Account changes: items requested", "owner": "Client service (account maintenance)",
                          "roles": ["ops_analyst", "ops_supervisor"], "teams": ["client_service"], "sla_hours": 120},
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
    "PENDING_AI_REVIEW": "ai_review",
    "PENDING_ITEMS": "items_requested",
    "PENDING_REVIEW": "compliance_review",
    "PENDING_ALERT_REVIEW": "compliance_review",   # a PEP or adverse-media alert the risk rating didn't send to EDD
    "PENDING_FRAUD_REVIEW": "compliance_review",   # a suspected fraudulent identity (app/identity_check.py)
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
    if kind == "change" and status in ("RETURNED", "PENDING_ITEMS"):
        return "maintenance_items"
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
    # Every PEP or adverse-media alert goes to AML compliance (Shawn, Oct. 20, 2026); sanctions hits go to the
    # sanctions team. Compliance clears the alert or requires EDD.
    "alert_review": {"label": "PEP or adverse-media alert", "statuses": ["PENDING_ALERT_REVIEW"],
                     "values": {"cleared": "Cleared: no EDD needed", "edd": "Require EDD"},
                     "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    # A suspected fraudulent identity goes to AML compliance (Shawn, Oct. 20, 2026): confirmed declines the case and
    # keeps it held; not fraudulent sends it back to identity verification
    "identity_fraud": {"label": "Suspected fraudulent identity", "statuses": ["PENDING_FRAUD_REVIEW"],
                       "values": {"confirmed": "Fraudulent identity: decline", "not_fraud": "Not fraudulent: verify again"},
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
