"""
The people-steps of the release 2 workflows that aren't maker-checker decisions: applying a suggested fix, recording a
callback, approving a drafted correction, recommending a product, verifying a death certificate, recording a consent.

Each action says which cases and statuses it applies to, who may take it, and the fields it asks for. The case page
shows the ones open to the person looking, and POST /api/cases/{id}/actions/{key} takes one: it records an event (or
updates the request) and re-runs the case. Every action is in the audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from custody import money
from custody.flow import last_event
from custody.lifecycle import ownership

from . import audit, documents, ledger
from .config import settings
from .orm import Account, Case, Firm, User

OPS = {"ops_analyst", "ops_supervisor"}


def _team(*teams: str) -> Callable[[User, Case], bool]:
    return lambda u, c: u.role == "ops_supervisor" or (u.role == "ops_analyst" and u.team in teams)


@dataclass
class Action:
    key: str
    label: str
    kind: str
    statuses: tuple
    who: Callable[[User, Case], bool]
    fields: Callable[[Session, Case], list[dict]]
    handle: Callable[[Session, Case, User, dict], str]
    help: str = ""
    demo_only: bool = False


def _event(case: Case, user: User, kind: str, **data) -> None:
    extra = dict(case.extra or {})
    extra["events"] = list(extra.get("events", [])) + [{"type": kind, "by": user.display_name,
                                                        "on": settings.today().isoformat(), **data}]
    case.extra = extra


def _n(case: Case) -> int:
    return 1 + sum(1 for e in (case.extra or {}).get("events", []) if e.get("type") == "correction")


def _sample(db: Session, case: Case, user: User, doc_type: str, lines: list[str]) -> None:
    label = documents.DOC_TYPES[doc_type]["label"]
    data = documents.make_pdf(label, ["SYNTHETIC SAMPLE: NOT A REAL DOCUMENT", ""] + lines +
                              ([f"Signature: /s/ {lines[0].split(': ')[-1]}", f"Date: {settings.today()}"]
                               if documents.DOC_TYPES[doc_type].get("signed") else []))
    doc = documents.store(db, case, user, doc_type, f"{doc_type}-{case.id}-sample.pdf", data, "mail", synthetic=True)
    documents.mark(db, doc, "in_good_order", user.display_name, user, "Checked against the case")


# --------------------------------------------------------------------------- transfers
def _fix(db, case, user, data):
    applied = data.get("choice", "apply") == "apply"
    if applied:
        acct = db.get(Account, case.application["account_id"])
        case.application = {**case.application, "title_on_form": acct.display_name}
    _event(case, user, "fix", applied=applied, note=data.get("note") or "")
    return "Fix applied; the request goes on to the controls gate." if applied else "Sent as is."


def _callback(db, case, user, data):
    ok = data.get("confirmed") == "yes"
    if not (data.get("phone") or "").strip():
        raise HTTPException(422, "Record the number you called: one already on file, not one on the request")
    _event(case, user, "callback", confirmed=ok, phone=data["phone"], note=data.get("note") or "")
    return "Callback recorded: confirmed." if ok else "Callback recorded: not confirmed. The request is rejected."


def _correction(db, case, user, data):
    summary = (case.result or {}).get("summary", {})
    if "title" in (summary.get("Exception") or "").lower():
        acct = db.get(Account, case.application["account_id"])
        case.application = {**case.application, "title_on_form": acct.display_name}
    extra = dict(case.extra or {})
    extra["simulate"] = {}                      # the corrected request goes through clean
    case.extra = extra
    _event(case, user, "correction", n=_n(case) - 0, note=data.get("note") or summary.get("Drafted correction", ""))
    return "Correction approved; the request is resubmitted."


def _chase(db, case, user, data):
    _event(case, user, "chase", n=_n(case), note=data.get("note") or "")
    return "Inquiry sent to the other side."


def _basis(db, case, user, data):
    _event(case, user, "basis", note=data.get("note") or "")
    return "Cost basis recorded."


def _rail(db, case, user, data):
    """The rail simulator (principle 8): deliver the other side's answers now instead of waiting for the date."""
    n = _n(case)
    sub = last_event((case.extra or {}).get("events", []), "submitted", n=n)
    if not sub:
        raise HTTPException(409, "Nothing has gone to the rail yet")
    submitted = date.fromisoformat(sub["on"])
    choice = data.get("outcome", "confirm")
    script = {"reject": data.get("code") or "nof"} if choice == "reject" else \
        {"return": data.get("code") or "R01"} if choice == "return" else {}
    dates = money.expected_dates(case.application["type"], submitted)
    through = max(dates.values())
    from custody.calendar import add_bdays
    through = add_bdays(through, 1) if choice == "return" else through
    answers = money.rail_response(case.application, submitted, through, script)
    have = {(e.get("outcome"), e.get("n")) for e in (case.extra or {}).get("events", []) if e.get("type") == "rail"}
    today = settings.today()
    for a in answers:
        if (a["outcome"], n) not in have:
            on = min(date.fromisoformat(a["on"]), today).isoformat()
            _event(case, user, "rail", n=n, outcome=a["outcome"], code=a.get("code"), simulated=True)
            case.extra["events"][-1]["on"] = on
    return "The simulator delivered the rail's answer."


# --------------------------------------------------------------------------- products
def _recommend(db, case, user, data):
    _event(case, user, "recommendation", recommend=data.get("recommend", "accept"), note=data.get("note") or "")
    return "Recommendation recorded; risk and compliance decide."


def _setup(db, case, user, data):
    _event(case, user, "setup", note=data.get("note") or "")
    return "Security set up; the RIA is told it's available."


def _product_docs(db, case, user, data):
    name = case.application.get("description", "")
    for t in ("offering", "subscription", "financials"):
        if t not in {d.doc_type for d in documents.case_documents(db, case)}:
            _sample(db, case, user, t, [f"Product: {name}", f"Sponsor: {case.application.get('sponsor') or ''}"])
    return "Offering documents attached (synthetic samples)."


# --------------------------------------------------------------------------- offboarding
def _tif(db, case, user, data):
    if not (data.get("receiving_firm") or "").strip():
        raise HTTPException(422, "Name the receiving firm")
    _event(case, user, "tif", receiving_firm=data["receiving_firm"].strip())
    return "Transfer request recorded; the ACATS transfer out opens."


def _moved(db, case, user, data):
    _event(case, user, "moved", firm=data.get("firm") or "", note=data.get("note") or "")
    return "Recorded: the client picked an RIA on the platform. Open the advisor move with the new LPOA."


def _residual(db, case, user, data):
    items = {a: sum(b.values()) for a, b in ledger.account_balances(db, case.application["account_id"]).items()
             if abs(sum(b.values())) > 1e-9}
    summary = (case.result or {}).get("summary", {})
    deliver = "deliver" in (summary.get("Proposed disposition") or "")
    _event(case, user, "residual", items=items, deliver=deliver, note=data.get("note") or "")
    return "Residual disposition approved and posted."


# --------------------------------------------------------------------------- deceased accounts
def _certificate(db, case, user, data):
    acct = db.get(Account, case.application["account_id"])
    _sample(db, case, user, "death_certificate", [f"Decedent: {acct.display_name}",
                                                  f"Date of death: {case.application.get('date_of_death', '')}"])
    return "Death certificate attached (synthetic sample)."


def _verify_death(db, case, user, data):
    _event(case, user, "death_verified", verified=data.get("verified") == "yes", note=data.get("note") or "")
    return "Death verified: full lockdown." if data.get("verified") == "yes" else "Not verified: the hold comes off."


def _claimants(db, case) -> list[str]:
    from .servicing import account_view, mark_decedent
    acct = db.get(Account, case.application["account_id"])
    _kind, who = ownership(mark_decedent(account_view(db, acct), case))
    return who


def _claim(db, case, user, data):
    if "claim_form" not in {d.doc_type for d in documents.case_documents(db, case)}:
        who = _claimants(db, case)
        _sample(db, case, user, "claim_form", [f"Claimant: {data.get('claimant') or (who[0] if who else '')}",
                                               f"Account: {case.application['account_id']}"])
    _event(case, user, "claim_verified", claimant=data.get("claimant") or "")
    return "Claim documents verified."


def _estate_docs(db, case, user, data):
    acct = db.get(Account, case.application["account_id"])
    executor = (data.get("executor") or "").strip()
    if not executor:
        raise HTTPException(422, "Name the executor or administrator on the letters")
    _sample(db, case, user, "letters_testamentary", [f"Estate of: {acct.display_name}", f"Executor: {executor}"])
    case.application = {**case.application, "executor": executor}
    return "Letters attached; compliance and legal verify them."


def _distribution(db, case, user, data):
    method = data.get("method", "new_account")
    if method == "new_account":
        dest = db.get(Account, (data.get("account_id") or "").strip())
        if dest is None or dest.status != "OPEN" or dest.id == case.application["account_id"]:
            raise HTTPException(422, "Name an open account in the new owner's name")
        _event(case, user, "distribution", method=method, account_id=dest.id)
    else:
        if not (data.get("receiving_firm") or "").strip():
            raise HTTPException(422, "Name the receiving firm")
        _event(case, user, "distribution", method=method, receiving_firm=data["receiving_firm"].strip())
    return "Distribution instruction recorded."


# --------------------------------------------------------------------------- advisor moves, mergers, transitions
def _lpoa(db, case, user, data):
    acct = db.get(Account, case.application["account_id"])
    if not any(d.doc_type == "lpoa_new_ria" for d in documents.case_documents(db, case)):
        firm = db.scalar(select(Firm).where(Firm.crd == case.application["new_firm_crd"]))
        _sample(db, case, user, "lpoa_new_ria", [f"Client: {acct.display_name}",
                                                 f"grants {firm.legal_name} limited power of attorney"])
    case.application = {**case.application, "lpoa_signed": True, "signature_ok": True,
                        "advisor_registered": data.get("registered", "yes") == "yes"}
    return "LPOA in good order."


def _consent(db, case, user, data):
    acct = data.get("account_id")
    if acct not in (case.application.get("accounts") or []):
        raise HTTPException(422, "Pick one of the firm's accounts")
    _event(case, user, "consent", account_id=acct, answer=data.get("answer", "consented"))
    return "Consent recorded."


def _plan(db, case, user, data):
    _event(case, user, "plan_confirmed", note=data.get("note") or "")
    return "Plan confirmed."


def _release_batch(db, case, user, data):
    _event(case, user, "batch_released", n=int(data.get("n") or 1))
    return "Batch released: its transfers open."


def _transition_basis(db, case, user, data):
    _event(case, user, "basis", account=data.get("account"), breaks=data.get("breaks") or None,
           resolved=data.get("resolved") == "yes" or not data.get("breaks"))
    return "Cost-basis statement recorded."


def _forms(db, case, user, data):
    _event(case, user, "forms", account_id=data.get("account_id"))
    return "Client-signed transfer form recorded; the transfer out opens."


def _revoke(db, case, user, data):
    reason = (data.get("reason") or "").strip()
    if len(reason) < 5:
        raise HTTPException(422, "Say why the capability is revoked")
    _event(case, user, "revoked", reason=reason)
    return "Capability revoked."


# --------------------------------------------------------------------------- fields
def _none(db, case):
    return []


def _note(label="Note", default=""):
    return lambda db, case: [{"name": "note", "label": label, "type": "textarea", "default": default}]


def _yesno(name, label):
    return {"name": name, "label": label, "type": "select", "options": [["yes", "Yes"], ["no", "No"]], "default": "yes"}


def _firm_options(db, case):
    return [[f.legal_name, f.legal_name] for f in db.scalars(select(Firm).where(Firm.status == "ACTIVE")).all()]


def _residual_fields(db, case):
    s = (case.result or {}).get("summary", {})
    return [{"name": "note", "label": "Proposed disposition", "type": "textarea",
             "default": s.get("Proposed disposition", "")}]


def _correction_fields(db, case):
    s = (case.result or {}).get("summary", {})
    return [{"name": "note", "label": "Drafted correction (edit before approving)", "type": "textarea",
             "default": s.get("Drafted correction", "")}]


def _chase_fields(db, case):
    cp = (case.application.get("counterparty") or {}).get("institution") or "the other firm"
    s = (case.result or {}).get("summary", {})
    return [{"name": "note", "label": "Inquiry (drafted; edit before sending)", "type": "textarea",
             "default": f"To {cp}: our {money.TYPES[case.application['type']]['label'].lower()} for "
                        f"{case.application['account_id']}, submitted {s.get('Submitted', '')}, was expected to settle "
                        f"{s.get('Expected to settle', '')}. Please confirm its status or the reason it's held."}]


def _claim_fields(db, case):
    who = _claimants(db, case)
    return [{"name": "claimant", "label": "Claimant", "type": "select", "options": [[w, w] for w in who],
             "default": who[0] if who else ""}]


def _merger_fields(db, case):
    done = {e["account_id"] for e in (case.extra or {}).get("events", []) if e.get("type") == "consent"}
    left = [a for a in case.application.get("accounts") or [] if a not in done]
    return [{"name": "account_id", "label": "Account", "type": "select", "options": [[a, a] for a in left],
             "default": left[0] if left else ""},
            {"name": "answer", "label": "Answer", "type": "select",
             "options": [["consented", "Consented (signed a new LPOA)"], ["objected", "Objected"]],
             "default": "consented"}]


def _batch_fields(db, case):
    done = {e["n"] for e in (case.extra or {}).get("events", []) if e.get("type") == "batch_released"}
    left = [b["n"] for b in case.application.get("batches", []) if b["n"] not in done]
    return [{"name": "n", "label": "Batch", "type": "select", "options": [[str(n), f"Batch {n}"] for n in left],
             "default": str(left[0]) if left else ""}]


def _basis_fields(db, case):
    done = {e["account"] for e in (case.extra or {}).get("events", []) if e.get("type") == "basis"}
    keys = [a["key"] for b in case.application.get("batches", []) for a in b["accounts"] if a["key"] not in done]
    return [{"name": "account", "label": "Account", "type": "select", "options": [[k, k] for k in keys],
             "default": keys[0] if keys else ""},
            {"name": "breaks", "label": "Breaks against the prior statement (blank if it matches)", "type": "text"},
            _yesno("resolved", "Breaks resolved with the prior custodian")]


def _forms_fields(db, case):
    done = {e["account_id"] for e in (case.extra or {}).get("events", []) if e.get("type") == "forms"}
    left = [a["account_id"] for a in case.application.get("accounts") or [] if a["account_id"] not in done]
    return [{"name": "account_id", "label": "Account", "type": "select", "options": [[a, a] for a in left],
             "default": left[0] if left else ""}]


ACTIONS = [
    Action("fix", "Apply the suggested fix", "transfer", ("PENDING_FIX",), _team("asset_transfer"),
           lambda db, c: [{"name": "choice", "label": "Fix", "type": "select",
                           "options": [["apply", "Apply the suggested fix"], ["as_is", "Send as is"]],
                           "default": "apply"}, {"name": "note", "label": "Note", "type": "textarea"}],
           _fix, "The pre-screen found a likely reject. AI suggests the fix; an analyst applies it."),
    Action("callback", "Record the callback", "transfer", ("PENDING_CALLBACK",), _team("asset_transfer"),
           lambda db, c: [_yesno("confirmed", "The client confirmed the request"),
                          {"name": "phone", "label": "Number called (one already on file)", "type": "text"},
                          {"name": "note", "label": "Note", "type": "textarea"}],
           _callback, "Call the client on a number already on file, never one on the request (MM-006)."),
    Action("correction", "Approve the correction and resubmit", "transfer", ("PENDING_CORRECTION",),
           _team("asset_transfer"), _correction_fields, _correction,
           "AI drafted the correction from the reject reason; an analyst approves it before it goes out."),
    Action("chase", "Send an inquiry to the other side", "transfer", ("OVERDUE",), _team("asset_transfer"),
           _chase_fields, _chase, "The expected settlement date passed. The inquiry is drafted; edit it first."),
    Action("basis", "Record the cost-basis statement", "transfer", ("COMPLETED",), _team("asset_transfer"),
           _note("What arrived"), _basis, "Due within 15 days of settlement (TRN-002)."),
    Action("rail", "Simulator: deliver the other side's answer now", "transfer",
           ("AWAITING_SETTLEMENT", "AWAITING_REREGISTRATION", "OVERDUE"), _team("asset_transfer", "settlement"),
           lambda db, c: [{"name": "outcome", "label": "Answer", "type": "select",
                           "options": [["confirm", "Confirm on its dates"], ["reject", "Reject"],
                                       ["return", "Confirm, then return (ACH)"]], "default": "confirm"},
                          {"name": "code", "label": "Reject or return code", "type": "select",
                           "options": [["", "None"], ["title", "ACATS: account title mismatch"],
                                       ["nof", "ACATS: account not on file"], ["R01", "ACH R01: insufficient funds"],
                                       ["R03", "ACH R03: no account"]], "default": ""}],
           _rail, "Stands in for the rail's adapter (principle 8); the cycle delivers answers on their dates.",
           demo_only=True),
    Action("recommend", "Recommend", "product", ("PENDING_PRODUCT_REVIEW",), _team("product"),
           lambda db, c: [{"name": "recommend", "label": "Recommendation", "type": "select",
                           "options": [["accept", "Accept"], ["decline", "Decline"]], "default": "accept"},
                          {"name": "note", "label": "Why (sponsor agreement, operational fit, sponsor background)",
                           "type": "textarea"}],
           _recommend, "Operations recommends; risk and compliance decide (PA-001)."),
    Action("setup", "Record the setup", "product", ("PENDING_SETUP",), _team("product"),
           _note("Agreement executed and connection made", "Custody agreement executed; settlement connection made"),
           _setup, "Security master, pricing and valuation source, settlement method, agreement."),
    Action("product_docs", "Attach the offering documents (synthetic samples)", "product", ("PENDING_DOCUMENTS",),
           _team("product"), _none, _product_docs, "In production these come from the RIA or the sponsor."),
    Action("tif", "Record the receiving firm's transfer request", "offboarding", ("AWAITING_TRANSFER",),
           _team("client_service"),
           lambda db, c: [{"name": "receiving_firm", "label": "Receiving firm", "type": "text"}], _tif,
           "The client signs the receiving firm's transfer form; the TIF arrives through ACATS."),
    Action("moved", "The client picked an RIA on the platform", "offboarding", ("AWAITING_TRANSFER",),
           _team("client_service"),
           lambda db, c: [{"name": "firm", "label": "New RIA", "type": "select", "options": _firm_options(db, c)},
                          {"name": "note", "label": "Note", "type": "textarea"}], _moved),
    Action("residual", "Approve the residual disposition", "offboarding", ("PENDING_RESIDUAL",),
           _team("client_service"), _residual_fields, _residual, "AI proposed it; an analyst approves it (OFF-003)."),
    Action("certificate", "Attach the death certificate (synthetic sample)", "deceased", ("AWAITING_CERTIFICATE",),
           _team("client_service"), _none, _certificate, "In production it arrives by mail or upload."),
    Action("verify_death", "Verify the death certificate", "deceased", ("PENDING_DEATH_VERIFICATION",),
           _team("client_service"), lambda db, c: [_yesno("verified", "Certificate verified"),
                                                    {"name": "note", "label": "Note", "type": "textarea"}],
           _verify_death),
    Action("claim", "Verify the claim documents", "deceased", ("PENDING_CLAIM_REVIEW",), _team("client_service"),
           _claim_fields, _claim, "A TOD or beneficiary designation, or a surviving joint owner (DEC-002)."),
    Action("estate_docs", "Attach the letters testamentary (synthetic sample)", "deceased", ("AWAITING_ESTATE_DOCS",),
           _team("client_service"), lambda db, c: [{"name": "executor", "label": "Executor or administrator",
                                                     "type": "text"}], _estate_docs),
    Action("distribution", "Record the new owner's instruction", "deceased", ("PENDING_DISTRIBUTION",),
           _team("client_service"),
           lambda db, c: [{"name": "method", "label": "Distribution", "type": "select",
                           "options": [["new_account", "Journal to an account in the new owner's name"],
                                       ["transfer_out", "Transfer out to another firm"]], "default": "new_account"},
                          {"name": "account_id", "label": "New owner's account (journal)", "type": "text"},
                          {"name": "receiving_firm", "label": "Receiving firm (transfer out)", "type": "text"}],
           _distribution),
    Action("lpoa", "Record the LPOA in good order", "advisor_move", ("PENDING_ITEMS",), _team("transitions"),
           lambda db, c: [_yesno("registered", "The advisor is registered with the new RIA (IAPD)")], _lpoa),
    Action("consent", "Record a client's consent", "merger", ("COLLECTING_CONSENT",), _team("transitions"),
           _merger_fields, _consent),
    Action("plan", "Confirm the plan", "transition", ("PLANNING", "CONFIRMING_PLAN"), _team("transitions"),
           _note(), _plan),
    Action("release_batch", "Release a transfer batch", "transition", ("OPENING_ACCOUNTS", "TRANSFERRING"),
           _team("transitions"), _batch_fields, _release_batch),
    Action("transition_basis", "Record a cost-basis statement", "transition", ("PENDING_BASIS",),
           _team("transitions"), _basis_fields, _transition_basis),
    Action("forms", "Record a client-signed transfer form", "transition", ("AWAITING_FORMS",), _team("transitions"),
           _forms_fields, _forms),
    Action("revoke", "Revoke the capability", "entitlement", ("ACTIVE", "PENDING_LINK_VERIFICATION"),
           lambda u, c: u.role == "ops_supervisor" or (u.role in ("ria_admin", "ria_user") and u.firm_id == c.firm_id),
           lambda db, c: [{"name": "reason", "label": "Why", "type": "textarea"}], _revoke,
           "Takes effect at once; logged with who, when, and why."),
]
BY_KEY = {a.key: a for a in ACTIONS}


def _open(a: Action, case: Case, user: User) -> bool:
    if case.kind != a.kind or case.status not in a.statuses or not a.who(user, case):
        return False
    if a.demo_only and not settings.demo_mode:
        return False
    if a.key == "basis":
        return case.application.get("type") == "acats_in" and not last_event((case.extra or {}).get("events", []),
                                                                               "basis")
    return True


def available(db: Session, case: Case, user: User) -> list[dict]:
    return [{"key": a.key, "label": a.label, "help": a.help, "fields": a.fields(db, case), "demo_only": a.demo_only}
            for a in ACTIONS if _open(a, case, user)]


def take(db: Session, case: Case, user: User, key: str, data: dict) -> str:
    from .runner import run_case
    a = BY_KEY.get(key)
    if a is None:
        raise HTTPException(404, "No such action")
    if case.kind != a.kind or case.status not in a.statuses:
        raise HTTPException(409, f"{a.label} isn't open on this case now")
    if not a.who(user, case) or (a.demo_only and not settings.demo_mode):
        audit.refuse(db, user, "case_action", f"{a.label} isn't part of your role", case_id=case.id, step=key)
    message = a.handle(db, case, user, data or {})
    case.manual_steps += 1
    audit.record(db, user, "case_action", "case", case.id, case_id=case.id, step=key,
                 detail={k: v for k, v in (data or {}).items() if v not in (None, "")} or None)
    run_case(db, case, user)
    return message
