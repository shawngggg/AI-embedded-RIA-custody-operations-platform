"""
What RIA users and clients see of a case. The custodian's operations stay inside: no engine steps or rule ids, no
risk rating or screening results, no due-diligence flags, no queue, SLA, or internal status, no staff names, no
restriction owners, and no audit trail. Every review state reads as "In review", so a sanctions or EDD escalation is
never disclosed through the portal.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from restrictions import CATALOG

from .documents import case_documents, doc_dict
from .orm import PORTAL_ROLES, Account, Case, CaseTransition, Firm, Restriction, User, iso

# Internal case status -> what the portal shows. Anything not listed is a review state.
STATUS = {
    "PENDING_ITEMS": "PENDING_ITEMS", "RETURNED": "RETURNED", "OPEN": "OPEN", "OPEN_RESTRICTED": "OPEN_RESTRICTED",
    "APPLIED": "APPLIED", "REDIRECTED": "REDIRECTED", "DECLINED": "DECLINED", "BLOCKED": "DECLINED",
    "REJECTED": "DECLINED", "ACTIVE": "ACTIVE", "PENDING_CONFIRMATION": "CONFIRMING",
}
NOTES = {
    "PENDING_ITEMS": "We need the items below before we can continue.",
    "RETURNED": "We couldn't process this request as submitted. Correct the items below and submit it again.",
    "OPEN": "The account is open.",
    "OPEN_RESTRICTED": "The account is open, but trading and fee deduction wait until we receive the items below.",
    "APPLIED": "The change has been applied.",
    "REDIRECTED": "This request has to come through the other channel: changes to a pure RIA account come from the RIA, "
                  "and changes to the self-directed account come from the client.",
    "DECLINED": "We weren't able to approve this request. Your RIA service team will follow up.",
    "CONFIRMING": "We'll contact you and the client to confirm this change before it's applied.",
    "IN_REVIEW": "We're reviewing this request. We'll let you know if we need anything.",
    "ACTIVE": "Active.",
}
ACCOUNT_STATUS = {"OPEN": "OPEN", "PENDING": "PENDING", "RETURNED": "RETURNED", "DECLINED": "DECLINED",
                  "BLOCKED": "DECLINED"}
# Restriction codes in the portal's words; the rest read as a review by the custodian, whatever the code
CODE_REASON = {
    "DOCS": "Waiting for documents from you",
    "KYCR": "Waiting for updated client information",
    "CIPV": "Identity verification in progress",
    "DECD": "Account owner deceased: the RIA's authority has ended",
}
UNDER_REVIEW = "Under review by the custodian"
BLOCK_WORDS = {"buy": "trading", "sell": "trading", "fee_deduction": "fee deduction", "deposit": "deposits",
               "transfer_out": "transfers out", "withdrawal": "withdrawals"}
ATTENTION = ("PENDING_ITEMS", "RETURNED", "OPEN_RESTRICTED")


def is_portal(user) -> bool:
    return user.role in PORTAL_ROLES


def status(internal: str) -> str:
    return STATUS.get(internal, "IN_REVIEW")


def account_status(internal: str) -> str:
    return ACCOUNT_STATUS.get(internal, "PENDING")


def restrictions_for(db: Session, target_id: str | None) -> list[dict]:
    """Active restrictions on the account, as what's limited and a reason the RIA can act on (or a neutral one)."""
    if not target_id:
        return []
    rows = db.scalars(select(Restriction).where(Restriction.target_id == target_id, Restriction.active.is_(True))
                      .order_by(Restriction.placed_at)).all()
    out: dict[str, set[str]] = {}
    for r in rows:
        reason = CODE_REASON.get(r.code, UNDER_REVIEW)
        out.setdefault(reason, set()).update(BLOCK_WORDS[a.value] for a in CATALOG[r.code].blocks)
    order = ["trading", "fee deduction", "deposits", "transfers out", "withdrawals"]
    return [{"reason": reason, "limits": [w for w in order if w in limits]} for reason, limits in out.items()]


def document(db: Session, d) -> dict:
    """A document as the portal sees it: who at the custodian added or checked it stays inside."""
    full = doc_dict(d)
    uploader = db.get(User, d.uploaded_by) if d.uploaded_by else None
    by_portal = uploader is not None and uploader.role in PORTAL_ROLES
    return {k: full[k] for k in ("id", "case_id", "doc_type", "type_label", "filename", "content_type", "size",
                                 "sha256", "channel", "channel_label", "received_on", "uploaded_at", "status",
                                 "review_note", "synthetic")} | {
        "uploaded_by": full["uploaded_by"] if by_portal else "Custodian operations",
        "reviewed_by": "Custodian operations" if full["reviewed_by"] else None, "reviewed_at": full["reviewed_at"],
        "checks": [], "checks_passed": None, "reopen_refusal": None}


def _details(c: Case) -> list[dict]:
    """What the RIA or client submitted, in their terms: none of the due-diligence fields the custodian adds."""
    a = c.application or {}
    rows: list[tuple[str, str]] = []
    if c.kind == "account":
        rows.append(("Registration", str(a.get("registration_type", "")).replace("_", " ")))
        rows.append(("Account group", "Self-directed" if a.get("group") == "self_directed" else "Pure RIA"))
        if a.get("entity_name"):
            rows.append(("Entity", a["entity_name"]))
        for h in a.get("holders", []):
            rows.append((str(h.get("role", "holder")).replace("_", " ").capitalize(),
                         ", ".join(x for x in (h.get("name"), h.get("address"), h.get("occupation")) if x)))
        for key, label in (("account_purpose", "Account purpose"), ("source_of_funds", "Source of funds"),
                           ("source_of_wealth", "Source of wealth")):
            if a.get(key):
                rows.append((label, a[key]))
        if a.get("expected_initial_funding"):
            rows.append(("Expected initial funding", f"${a['expected_initial_funding']:,.0f}"))
    elif c.kind == "change":
        rows.append(("Change", str(a.get("change_type", "")).replace("_", " ").capitalize()))
        for k, v in (a.get("details") or {}).items():
            rows.append((k.replace("_", " ").capitalize(), str(v)))
        if a.get("new_bank"):
            b = a["new_bank"]
            rows.append(("New bank instruction", f"{b.get('name', '')} ··{b.get('account_last4', '')}"))
        for p in a.get("new_parties") or []:
            rows.append(("New party", p.get("name", "")))
    return [{"label": k, "value": v} for k, v in rows if v]


def _history(db: Session, c: Case) -> list[dict]:
    """Submitted, then each change of portal status (consecutive review states collapse into one)."""
    out = [{"status": "SUBMITTED", "at": iso(c.created_at)}]
    for t in db.scalars(select(CaseTransition).where(CaseTransition.case_id == c.id).order_by(CaseTransition.id)):
        s = status(t.to_status)
        if out[-1]["status"] != s:
            out.append({"status": s, "at": iso(t.at)})
    return out


def summary(db: Session, c: Case) -> dict:
    firm = db.get(Firm, c.firm_id) if c.firm_id else None
    return {"id": c.id, "kind": c.kind, "title": c.title, "status": status(c.status),
            "firm": firm.legal_name if firm else None, "account_id": c.account_id, "target_id": c.target_id,
            "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}


def detail(db: Session, c: Case, user) -> dict:
    from .views import corrections_for
    s = status(c.status)
    account = db.get(Account, c.account_id) if c.account_id else None
    items = (c.result or {}).get("requested_items") or []
    docs = {d.id: document(db, d) for d in case_documents(db, c)}
    corrections = [{**x, "rule_id": None, "documents": [docs[d["id"]] for d in x["documents"] if d["id"] in docs]}
                   for x in corrections_for(db, c)]
    return {**summary(db, c), "status_note": NOTES[s],
            "requested_items": items if s in ATTENTION else [],
            "corrections": corrections, "documents": list(docs.values()),
            "restrictions_portal": restrictions_for(db, c.account_id if c.kind != "firm" else None),
            "account": {"id": account.id, "status": account_status(account.status), "group": account.group,
                        "display_name": account.display_name} if account else None,
            "details": _details(c), "history": _history(db, c), "closed_at": iso(c.closed_at), "portal": True}
