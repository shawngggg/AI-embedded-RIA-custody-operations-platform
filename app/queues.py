"""Work queues, SLA clocks, and who may decide what. SLA hours are demo policy."""
from __future__ import annotations

QUEUES = {
    "items_requested": {"label": "Items requested", "owner": "Custody operations (waiting on the RIA)",
                        "roles": ["ops_analyst", "ops_supervisor"], "sla_hours": 120},
    "compliance_review": {"label": "Compliance review", "owner": "AML compliance",
                          "roles": ["aml_compliance"], "sla_hours": 48},
    "sanctions_review": {"label": "Sanctions review", "owner": "Sanctions team",
                         "roles": ["sanctions"], "sla_hours": 24},
    "edd_review": {"label": "EDD pending", "owner": "AML compliance",
                   "roles": ["aml_compliance"], "sla_hours": 120},
    "confirmation": {"label": "Fraud-pattern confirmation", "owner": "Custody operations",
                     "roles": ["ops_analyst", "ops_supervisor"], "sla_hours": 24},
    "verification": {"label": "Identity verification", "owner": "Custody operations",
                     "roles": ["ops_analyst", "ops_supervisor"], "sla_hours": 72},
}

_BY_STATUS = {
    "PENDING_ITEMS": "items_requested",
    "PENDING_REVIEW": "compliance_review",
    "PENDING_SANCTIONS": "sanctions_review",
    "PENDING_EDD": "edd_review",
    "PENDING_CONFIRMATION": "confirmation",
    "PENDING_VERIFICATION": "verification",
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
}
