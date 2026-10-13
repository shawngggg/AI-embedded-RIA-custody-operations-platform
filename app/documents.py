"""
Documents: the evidence behind every item marked received.

A document is stored with its SHA-256 fingerprint, the channel it came in on, and who added it. Automated
pre-checks read a PDF's text layer and look for the client's name, tax ID, and a signature line. At intake,
a document that passes every pre-check is accepted in good order by the rules; anything else waits for a
person in operations to look at it. Only operations records a document-backed item as received.

The demo can attach synthetic sample PDFs, clearly marked as such.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import settings
from .orm import Account, Case, Document, Firm, OpsRiskEvent, User, iso, utcnow

MAX_BYTES = 5 * 1024 * 1024
CHANNELS = {"portal": "RIA portal", "email": "Email", "fax": "Fax", "mail": "Mail", "intake": "Intake package"}
AUTOMATED_REVIEWER = "Rules engine (automated pre-checks)"

# doc type -> label, whether it carries a signature, and the tax form it represents
DOC_TYPES = {
    "account_application": {"label": "Client-signed account application", "signed": True},
    "lpoa": {"label": "Limited power of attorney (LPOA)", "signed": True},
    "fee_authorization": {"label": "Fee deduction authorization", "signed": True},
    "w9": {"label": "Form W-9", "signed": True, "tax_form": "W-9", "tax_id": True},
    "w8ben": {"label": "Form W-8BEN", "signed": True, "tax_form": "W-8BEN", "tax_id": True},
    "w8bene": {"label": "Form W-8BEN-E", "signed": True, "tax_form": "W-8BEN-E", "tax_id": True},
    "formation_documents": {"label": "Formation documents or trust instrument", "signed": False},
    "bo_certification": {"label": "Beneficial ownership certification", "signed": True},
    "ownership_chart": {"label": "Ownership chart", "signed": False},
    "custodial_agreement": {"label": "Custodial services agreement (signed)", "signed": True},
    "reliance_contract": {"label": "CIP reliance agreement", "signed": True},
    "identity_documents": {"label": "Identity documents for owners and control persons", "signed": False},
    "change_form": {"label": "Client-signed change form", "signed": True},
    "medallion": {"label": "Medallion signature guarantee", "signed": True},
    "application_package": {"label": "RIA application package", "signed": False},
    "other": {"label": "Other document", "signed": False},
}
TAX_DOCS = {"w9", "w8ben", "w8bene"}

# Which documents back which application field, by case kind
FIELD_DOCS = {
    "account": {"client_signature": ["account_application"], "lpoa_signed": ["lpoa"],
                "fee_authorization": ["fee_authorization"], "tax_form": ["w9", "w8ben", "w8bene"],
                "entity_formation_documents": ["formation_documents"],
                "beneficial_ownership_certification": ["bo_certification"]},
    "firm": {"formation_documents": ["formation_documents"], "ownership_chart": ["ownership_chart"],
             "beneficial_ownership_certification": ["bo_certification"],
             "custodial_agreement_signed": ["custodial_agreement"], "reliance_contract": ["reliance_contract"],
             "verify_people": ["identity_documents"]},
    "change": {"client_signature": ["change_form"], "medallion_guarantee": ["medallion"]},
}


# Application fields whose items are non-regulatory (account_rules.NON_REGULATORY): reopening the document
# behind one keeps an open account open, restricted from trading and fee deduction. Anything else, the client
# signature and LPOA included, needs a rescission. test_api checks this matches the engine.
NON_REGULATORY_FIELDS = {"account": {"fee_authorization", "tax_form"}}
OPENED = ("OPEN", "OPEN_RESTRICTED", "ACTIVE")
CORRECTABLE = ("PENDING_ITEMS", "PENDING_ACCEPTANCE", "PENDING_REWORK", "OPEN_RESTRICTED")


def field_docs(kind: str, field: str) -> list[str]:
    return FIELD_DOCS.get(kind, {}).get(field, [])


def field_for_doc(kind: str, doc_type: str) -> str | None:
    return next((f for f, types in FIELD_DOCS.get(kind, {}).items() if doc_type in types), None)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise HTTPException(415, "Upload a PDF, PNG, or JPEG")


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def subject_names(db: Session, case: Case) -> tuple[list[str], list[str]]:
    """Names and tax ids a document for this case should show."""
    app = case.application or {}
    if case.kind == "firm":
        people = [p for p in app.get("people", []) if p.get("role") == "control_person"]
        return [app.get("legal_name", "")] + [p["name"] for p in people], [app.get("ein") or ""]
    if case.kind == "change":
        account = db.get(Account, case.account_id)
        app = account.application if account else {}
    holders = app.get("holders", [])
    names = ([app["entity_name"]] if app.get("entity_name") else []) + [h["name"] for h in holders]
    tax_ids = ([app["entity_ein"]] if app.get("entity_ein") else []) + [h.get("tax_id") or "" for h in holders]
    return [n for n in names if n], [t for t in tax_ids if t]


def right_tax_form(db: Session, case: Case) -> str | None:
    """W-9 for a US person; W-8BEN for a foreign individual; W-8BEN-E for a foreign entity."""
    if case.kind == "firm":
        return None
    app = case.application or {}
    if case.kind == "change":
        account = db.get(Account, case.account_id)
        app = account.application if account else {}
    holders = app.get("holders", [])
    primary = holders[0] if holders else {}
    if app.get("entity_name"):
        return None     # the application doesn't record an entity's country yet, so there's no expectation
    us = "United States" in (primary.get("nationality"), primary.get("country_of_residence"))
    return "w9" if us else "w8ben"


def pre_checks(data: bytes, content_type: str, doc_type: str, names: list[str], tax_ids: list[str],
               expected_tax_form: str | None = None) -> list[dict]:
    """Deterministic checks on the file. 'manual' means a person has to look."""
    if content_type == "text/plain":
        text = data.decode("utf-8", errors="replace")
    elif content_type == "application/pdf":
        text = pdf_text(data)
    else:
        return [{"check": "Text layer", "result": "manual", "detail": "Image file: check it by eye"}]
    if not text.strip():
        return [{"check": "Text layer", "result": "manual", "detail": "No text layer (a scan): check it by eye"}]
    t = _norm(text)
    out = [{"check": "Text layer", "result": "pass", "detail": f"{len(text.split())} words read"}]
    if doc_type in ("application_package", "other"):
        return out
    found = [n for n in names if _norm(n) and _norm(n) in t]
    out.append({"check": "Names the client", "result": "pass" if found else "fail",
                "detail": f"Found {', '.join(found)}" if found else f"None of {', '.join(names) or 'the names'} found"})
    if doc_type in TAX_DOCS and expected_tax_form:
        ok = doc_type == expected_tax_form
        out.append({"check": "Right form for the client", "result": "pass" if ok else "fail",
                    "detail": "Matches the client's tax status" if ok else
                    f"Expected {DOC_TYPES[expected_tax_form]['label']} for this client"})
    if DOC_TYPES[doc_type].get("tax_id"):
        digits = [re.sub(r"\D", "", x) for x in tax_ids]
        ok = any(d and d in re.sub(r"\D", "", text) for d in digits)
        out.append({"check": "Tax ID matches the application", "result": "pass" if ok else "fail",
                    "detail": "Matches" if ok else "The tax ID on the form doesn't match the application"})
    if DOC_TYPES[doc_type].get("signed"):
        signed = bool(re.search(r"(/s/|signature|signed)", text, re.I))
        out.append({"check": "Signature line present", "result": "pass" if signed else "fail",
                    "detail": "A person confirms the signature" if signed else "No signature line found"})
    return out


def checks_pass(checks: list[dict]) -> bool:
    return bool(checks) and all(c["result"] == "pass" for c in checks)


# ---------------------------------------------------------------------------
# Synthetic sample PDFs (demo only)
# ---------------------------------------------------------------------------

def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF with a text layer. Small and dependency-free."""
    body = ["BT", "/F1 16 Tf", "72 740 Td", f"({_pdf_escape(title)}) Tj", "/F1 11 Tf"]
    for line in lines:
        body += ["0 -20 Td", f"({_pdf_escape(line)}) Tj"]
    body.append("ET")
    stream = "\n".join(body).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def sample_document(db: Session, case: Case, doc_type: str, on: date) -> tuple[str, bytes]:
    """A synthetic document for this case that passes the pre-checks."""
    app = case.application or {}
    names, tax_ids = subject_names(db, case)
    who = names[0] if names else "Client"
    firm = db.get(Firm, case.firm_id) if case.firm_id else None
    adviser = firm.legal_name if firm else "the adviser"
    label = DOC_TYPES[doc_type]["label"]
    lines = ["SYNTHETIC SAMPLE: NOT A REAL DOCUMENT", ""]
    if doc_type in TAX_DOCS:
        lines += [f"Name: {who}", f"Taxpayer identification number: {tax_ids[0] if tax_ids else 'n/a'}",
                  "Certification under penalties of perjury."]
    elif doc_type == "lpoa":
        lines += [f"Client: {who}", f"grants {adviser} limited power of attorney to trade the account",
                  "and to instruct the custodian on its investments. No authority to withdraw assets."]
    elif doc_type == "fee_authorization":
        lines += [f"Client: {who}", f"authorizes the custodian to deduct {adviser}'s advisory fees from the account."]
    elif doc_type == "account_application":
        lines += [f"Account holder: {who}", f"Account: {case.account_id or ''}", f"Adviser: {adviser}"]
    elif doc_type == "change_form":
        lines += [f"Account owner: {who}", f"Account: {case.account_id}",
                  f"Change requested: {app.get('change_type', '').replace('_', ' ')}"]
    elif doc_type == "medallion":
        lines += [f"Signer: {who}", "MEDALLION SIGNATURE GUARANTEED", "Stamp SYN-0000000 (synthetic)"]
    elif doc_type == "bo_certification":
        lines += [f"Legal entity customer: {who}", "Certification of beneficial owners and a control person."]
    elif doc_type == "identity_documents":
        lines += [f"Identity documents for: {', '.join(names) or who}"]
    else:
        lines += [f"For: {who}"]
    if DOC_TYPES[doc_type].get("signed"):
        lines += ["", f"Signature: /s/ {names[-1] if case.kind == 'firm' and len(names) > 1 else who}",
                  f"Date: {on.isoformat()}"]
    filename = f"{doc_type}-{(case.account_id or case.target_id).lower()}-sample.pdf"
    return filename, make_pdf(label, lines)


# ---------------------------------------------------------------------------
# Storing and reviewing
# ---------------------------------------------------------------------------

def store(db: Session, case: Case, user: User | None, doc_type: str, filename: str, data: bytes, channel: str,
          received_on: date | None = None, synthetic: bool = False, actor_name: str | None = None,
          content_type: str | None = None) -> Document:
    if doc_type not in DOC_TYPES:
        raise HTTPException(422, "Unknown document type")
    if channel not in CHANNELS:
        raise HTTPException(422, "Unknown channel")
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Keep documents under 5 MB")
    content_type = content_type or sniff(data)
    sha = hashlib.sha256(data).hexdigest()
    same = db.scalar(select(Document).where(Document.case_id == case.id, Document.sha256 == sha,
                                            Document.status != "not_in_good_order"))
    if same is not None:
        raise HTTPException(409, f"This exact file is already on the case: {same.filename}, added by "
                                 f"{same.uploaded_by_name} on {same.received_on.isoformat()}")
    names, tax_ids = subject_names(db, case)
    doc = Document(case_id=case.id, target_id=case.target_id, doc_type=doc_type,
                   filename=(filename or f"{doc_type}.pdf")[:200], content_type=content_type, size=len(data),
                   sha256=sha, data=data, channel=channel,
                   received_on=received_on or settings.today(), uploaded_by=user.id if user else None,
                   uploaded_by_name=actor_name or (user.display_name if user else "system"),
                   checks=pre_checks(data, content_type, doc_type, names, tax_ids, right_tax_form(db, case)),
                   synthetic=synthetic)
    db.add(doc)
    db.flush()
    audit.record(db, user, "document_uploaded", "document", doc.id, case_id=case.id, doc_type=doc_type,
                 filename=doc.filename, sha256=doc.sha256, size=doc.size, channel=channel,
                 synthetic=synthetic or None, checks_passed=checks_pass(doc.checks))
    return doc


def mark(db: Session, doc: Document, status: str, reviewer: str, user: User | None, note: str | None = None) -> None:
    doc.status, doc.reviewed_by_name, doc.reviewed_at, doc.review_note = status, reviewer, utcnow(), note
    action = {"in_good_order": "document_in_good_order", "superseded": "document_superseded"}.get(
        status, "document_not_in_good_order")
    audit.record(db, user, action,
                 "document", doc.id, case_id=doc.case_id, doc_type=doc.doc_type, sha256=doc.sha256,
                 reviewer=reviewer, note=note)


def supersede_others(db: Session, accepted: Document, types: list[str], reviewer: str, user: User | None) -> None:
    """Once one copy satisfies an item, other copies still waiting on the same item are closed out."""
    others = db.scalars(select(Document).where(Document.case_id == accepted.case_id, Document.id != accepted.id,
                                               Document.doc_type.in_(types), Document.status == "received")).all()
    for doc in others:
        mark(db, doc, "superseded", reviewer, user, f"{accepted.filename} was accepted for this item")


def decode_upload(item: dict) -> tuple[str, bytes]:
    try:
        return item.get("filename") or "document.pdf", base64.b64decode(item["content_b64"], validate=True)
    except (KeyError, ValueError):
        raise HTTPException(422, "Document content isn't valid base64")


def attach_at_intake(db: Session, case: Case, user: User | None, items: list[dict], channel: str) -> dict:
    """
    Store documents sent with an application and set the fields they back. A document that passes every
    pre-check is in good order by rule; others wait for operations, and their item stays requested.
    Returns the field values to apply to the application.
    """
    fields: dict = {}
    for item in items:
        doc_type = item.get("doc_type", "")
        if item.get("sample"):
            if not settings.demo_mode:
                raise HTTPException(403, "Sample documents are for the demo only")
            filename, data = sample_document(db, case, doc_type, settings.today())
        else:
            filename, data = decode_upload(item)
        doc = store(db, case, user, doc_type, filename, data, channel, synthetic=bool(item.get("sample")))
        field = field_for_doc(case.kind, doc_type)
        # Identity verification is a person's call, never the pre-checks'
        if field and field != "verify_people" and checks_pass(doc.checks):
            mark(db, doc, "in_good_order", AUTOMATED_REVIEWER, user)
            fields[field] = DOC_TYPES[doc_type].get("tax_form", True)
    return fields


def reopen_refusal(case: Case, doc: Document, user: User) -> str | None:
    """Why this person can't reopen (mark not in good order) a document that was accepted, or None."""
    if doc.status != "in_good_order":
        return "Only an accepted document can be reopened"
    if user.role not in ("ops_analyst", "ops_supervisor", "ria_service", "ria_service_lead"):
        return "Operations or a registered principal reopens a document"
    if case.status not in CORRECTABLE + OPENED:
        return "This case is closed"
    field = field_for_doc(case.kind, doc.doc_type)
    if case.status in OPENED and field and field not in NON_REGULATORY_FIELDS.get(case.kind, set()):
        return "This document backs a regulatory requirement on an open account: rescind the acceptance instead"
    return None


def reopen(db: Session, case: Case, doc: Document, user: User, reason: str) -> OpsRiskEvent | None:
    """
    Mark an accepted document not in good order and take back the item it satisfied. On an open account this
    is an operational risk event: the document should never have been accepted.
    """
    was_open = case.status in OPENED
    mark(db, doc, "not_in_good_order", user.display_name, user, reason)
    field = field_for_doc(case.kind, doc.doc_type)
    if field == "verify_people":
        case.application = {**case.application, "people": [{**p, "identity_verified": False}
                                                           for p in case.application.get("people", [])]}
    elif field:
        value = None if field == "tax_form" else False
        case.application = {**case.application, field: value}
        account = db.get(Account, case.account_id) if case.account_id and case.kind == "account" else None
        if account is not None:
            account.application = {**account.application, field: value}
    event = None
    if was_open:
        from .risk_register import log_event
        event = log_event(db, user, category="Document accepted in error", risk_type="operational", source="document",
                          summary=f"{DOC_TYPES[doc.doc_type]['label']} ({doc.filename}) was accepted by "
                                  f"{doc.reviewed_by_name or 'operations'} and later found not in good order: {reason}",
                          owner="ops_supervisor", detected_by=user.display_name, case=case)
    return event


def doc_dict(d: Document) -> dict:
    return {"id": d.id, "case_id": d.case_id, "doc_type": d.doc_type, "type_label": DOC_TYPES[d.doc_type]["label"],
            "filename": d.filename, "content_type": d.content_type, "size": d.size, "sha256": d.sha256,
            "channel": d.channel, "channel_label": CHANNELS.get(d.channel, d.channel),
            "received_on": d.received_on.isoformat(), "uploaded_by": d.uploaded_by_name,
            "uploaded_at": iso(d.uploaded_at), "status": d.status,
            "reviewed_by": d.reviewed_by_name, "reviewed_at": iso(d.reviewed_at),
            "review_note": d.review_note, "checks": d.checks or [], "checks_passed": checks_pass(d.checks or []),
            "synthetic": d.synthetic}


def case_documents(db: Session, case: Case) -> list[Document]:
    return db.scalars(select(Document).where(Document.case_id == case.id).order_by(Document.id)).all()
