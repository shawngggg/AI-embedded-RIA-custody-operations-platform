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
    "rework": {"label": "Rework: sent back by a principal", "owner": "Onboarding",
               "roles": ["ops_analyst", "ops_supervisor"], "teams": ["onboarding"], "sla_hours": 24},
    # Release 2 (maps 05, 08, 16, 17, 22, 23, 24)
    "money_release": {"label": "Money movement: release", "owner": "Asset transfer (maker-checker)",
                      "roles": ["ops_analyst", "ops_supervisor"], "teams": ["asset_transfer"], "sla_hours": 4},
    "callbacks": {"label": "Callbacks before release", "owner": "Asset transfer",
                  "roles": ["ops_analyst", "ops_supervisor"], "teams": ["asset_transfer"], "sla_hours": 8},
    "transfer_exceptions": {"label": "Transfers: fixes, corrections, and chases", "owner": "Asset transfer",
                            "roles": ["ops_analyst", "ops_supervisor"], "teams": ["asset_transfer"], "sla_hours": 24},
    "in_transit": {"label": "In transit: waiting for the other side", "owner": "Asset transfer",
                   "roles": ["ops_analyst", "ops_supervisor"], "teams": ["asset_transfer"], "sla_hours": 72},
    "transfers_waiting": {"label": "Transfers waiting on product acceptance", "owner": "Asset transfer",
                          "roles": ["ops_analyst", "ops_supervisor"], "teams": ["asset_transfer"], "sla_hours": 120},
    # The rest of release 2 (entitlements, products, the account lifecycle) ships in its own pull request; until
    # then its queues stay out of every menu (LATER)
    "product_review": {"label": "Product requests", "owner": "Product acceptance",
                       "roles": ["ops_analyst", "ops_supervisor"], "teams": ["product"], "sla_hours": 72, "later": True},
    "lifecycle": {"label": "Offboarding and deceased accounts", "owner": "Client service",
                  "roles": ["ops_analyst", "ops_supervisor"], "teams": ["client_service"], "sla_hours": 72,
                  "later": True},
    "transitions": {"label": "RIA transitions, advisor moves, and mergers", "owner": "RIA transitions",
                    "roles": ["ops_analyst", "ops_supervisor"], "teams": ["transitions"], "sla_hours": 72,
                    "later": True},
}


LATER_ENABLED = False


def works_queue(user, key: str) -> bool:
    """Whether this queue is the user's: their role works it, and for operations analysts, their team does.
    Supervisors oversee every operations queue."""
    spec = QUEUES[key]
    if user.role not in spec["roles"] or (spec.get("later") and not LATER_ENABLED):
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

# Release 2 cases map their own statuses (a status with no queue is waiting on someone outside the team)
KIND_QUEUES = {
    "transfer": {"PENDING_SANCTIONS": "sanctions_review", "PENDING_RISK_REVIEW": "compliance_review",
                 "PENDING_RELEASE": "money_release", "PENDING_CALLBACK": "callbacks",
                 "PENDING_FIX": "transfer_exceptions", "PENDING_CORRECTION": "transfer_exceptions",
                 "OVERDUE": "transfer_exceptions", "AWAITING_SETTLEMENT": "in_transit",
                 "AWAITING_REREGISTRATION": "in_transit", "WAITING_PRODUCT": "transfers_waiting"},
    "product": {"PENDING_DOCUMENTS": "product_review", "PENDING_PRODUCT_REVIEW": "product_review",
                "PENDING_SETUP": "product_review", "PENDING_PRODUCT_DECISION": "compliance_review"},
    "offboarding": {"PENDING_HOLD_RESOLUTION": "compliance_review", "AWAITING_TRANSFER": "lifecycle",
                    "PENDING_RESIDUAL": "lifecycle"},
    "deceased": {"AWAITING_CERTIFICATE": "lifecycle", "PENDING_DEATH_VERIFICATION": "lifecycle",
                 "PENDING_CLAIM_REVIEW": "lifecycle", "AWAITING_ESTATE_DOCS": "lifecycle",
                 "PENDING_LEGAL_REVIEW": "compliance_review", "PENDING_DISTRIBUTION": "lifecycle"},
    "advisor_move": {"PENDING_ITEMS": "transitions", "PENDING_LEGAL_HOLD": "compliance_review"},
    "merger": {"PENDING_CLASSIFICATION": "compliance_review", "PENDING_CONSENT_PACKAGE": "compliance_review",
               "COLLECTING_CONSENT": "transitions", "WAITING_ACQUIRER": "transitions"},
    "transition": {"PLANNING": "transitions", "CONFIRMING_PLAN": "transitions", "OPENING_ACCOUNTS": "transitions",
                   "TRANSFERRING": "transitions", "PENDING_BASIS": "transitions", "AWAITING_FORMS": "transitions",
                   "OFFBOARDING_REMAINDER": "transitions", "WAITING_ONBOARDING": "transitions"},
    "entitlement": {},
}
KIND_CLOSED = {
    "transfer": {"COMPLETED", "REJECTED", "BLOCKED", "RETURNED"},
    "entitlement": {"ACTIVE", "DECLINED", "REVOKED", "EXPIRED"},
    "product": {"AVAILABLE", "DECLINED"},
    "offboarding": {"CLOSED", "MOVED"},
    "deceased": {"SETTLED", "NOT_VERIFIED"},
    "advisor_move": {"COMPLETED", "BLOCKED"},
    "merger": {"COMPLETED", "RECORDS_UPDATED"},
    "transition": {"COMPLETED"},
}


def queue_for(kind: str, status: str) -> str | None:
    if kind in KIND_QUEUES:
        return KIND_QUEUES[kind].get(status)
    if kind == "change" and status in ("RETURNED", "PENDING_ITEMS"):
        return "maintenance_items"
    return _BY_STATUS.get(status)


def is_closed(kind: str, status: str) -> bool:
    if kind in KIND_CLOSED:
        return status in KIND_CLOSED[kind]
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
    # Release 2. Money leaves only under maker-checker (MM-005): an asset transfer analyst checks up to their dollar
    # authority, the operations supervisor above it (app/routers/cases.py reads `authority`)
    "release": {"label": "Release outgoing money", "statuses": ["PENDING_RELEASE"],
                "values": {"released": "Release", "rejected": "Don't release"},
                "proposer": ["ops_analyst", "ops_supervisor"], "approver": ["ops_analyst", "ops_supervisor"],
                "teams": ["asset_transfer"], "authority": True},
    "risk_review": {"label": "Transfer risk review", "statuses": ["PENDING_RISK_REVIEW"],
                    "values": {"approved": "Approve", "rejected": "Reject"},
                    "proposer": ["aml_compliance", "ops_supervisor"], "approver": ["aml_compliance", "ops_supervisor"]},
    # Product acceptance: operations recommends, risk and compliance decide (map 24); AML compliance stands in
    "product": {"label": "Product acceptance decision", "statuses": ["PENDING_PRODUCT_DECISION"],
                "values": {"accepted": "Accept the product", "declined": "Decline"},
                "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    # Compliance and legal (maps 17 and 23); AML compliance stands in
    "estate_docs": {"label": "Estate documents and executor", "statuses": ["PENDING_LEGAL_REVIEW"],
                    "values": {"approved": "In good order: executor identified", "rejected": "Not in good order"},
                    "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    "legal_hold": {"label": "Dispute or court order on an advisor move", "statuses": ["PENDING_LEGAL_HOLD"],
                   "values": {"follow": "Follow the client's signed instruction", "hold": "The court order holds it"},
                   "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    "merger_classification": {"label": "Merger classification", "statuses": ["PENDING_CLASSIFICATION"],
                              "values": {"change_of_control": "Change of control: needs client consent",
                                         "rebrand": "Rebrand or reorganization: records only"},
                              "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    "negative_consent": {"label": "Negative-consent package", "statuses": ["PENDING_CONSENT_PACKAGE"],
                         "values": {"approved": "Approve the package", "rejected": "Require affirmative consent"},
                         "proposer": ["aml_compliance"], "approver": ["aml_compliance"]},
    # Requested through routers/cases.py (it restricts the account at once); approved by the team lead.
    "rescission": {"label": "Rescind the principal's acceptance", "statuses": ["OPEN", "OPEN_RESTRICTED", "ACTIVE"],
                   "values": {"review": "Back to the principal for review", "operations": "Back to operations for rework"},
                   "proposer": ["ria_service", "ria_service_lead"], "approver": ["ria_service_lead"], "custom": True},
}
