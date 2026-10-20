"""
Intake: a firm application by form, or a package read by AI behind the controls gate.

Live AI calls need ANTHROPIC_API_KEY and stay under the daily cap. Without
them, the sample package runs through the gate with the illustrative model
response, labeled as such; any other document waits for live AI.
"""
import io
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from extraction import DEFAULT_MODEL, extract_firm_application, gate
from nigo_eval import PACKAGES, package_text
from sample_data import FIRM_PACKAGE_TEXT, ILLUSTRATIVE_EXTRACTION

from .. import ai_suggestions, audit
from ..config import settings
from ..db import get_db
from ..documents import attach_at_intake, store
from ..orm import AiUsage, User
from ..runner import run_case
from ..access import require_capability
from ..seed import create_firm_case
from ..serialize import firm_from
from ..views import case_detail

router = APIRouter(prefix="/api/intake", tags=["intake"])
ops = require_capability("intake")      # the onboarding team takes in new applications


def _usage(db: Session, day: date) -> AiUsage:
    row = db.get(AiUsage, day)
    if row is None:
        row = AiUsage(day=day, count=0)
        db.add(row)
        db.flush()
    return row


@router.get("/status")
def status(user: User = Depends(ops), db: Session = Depends(get_db)):
    used = _usage(db, settings.today()).count
    db.commit()
    # The synthetic NIGO test packages, for trying live AI on more than the sample. Names and text only: what each
    # one is missing is the answer key, and showing it would tell the reviewer what to look for.
    return {"live": settings.ai_live, "auth": settings.ai_auth, "model": DEFAULT_MODEL, "used_today": used,
            "cap": settings.ai_daily_cap, "sample_text": FIRM_PACKAGE_TEXT,
            "test_packages": [{"key": p.key, "name": p.name, "text": package_text(p)} for p in PACKAGES]}


def _read(file: UploadFile | None, text: str | None) -> tuple[str, bytes | None, str | None]:
    """The package's text, plus the original PDF bytes and file name when a PDF was uploaded."""
    if text and text.strip():
        return text.replace("\r\n", "\n"), None, None   # browsers send form text with CRLF line breaks
    if file is None:
        raise HTTPException(422, "Upload a PDF or text file, or paste the text")
    raw = file.file.read()
    if raw.startswith(b"%PDF"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages), raw, file.filename
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n"), None, file.filename


@router.post("/extract")
def extract(file: UploadFile | None = File(None), text: str | None = Form(None),
            user: User = Depends(ops), db: Session = Depends(get_db)):
    document, original, filename = _read(file, text)
    if len(document) > 60_000:
        raise HTTPException(413, "Keep packages under 60,000 characters for the demo")
    usage = _usage(db, settings.today())
    is_sample = " ".join(document.split()) == " ".join(FIRM_PACKAGE_TEXT.split())
    if settings.ai_live and usage.count < settings.ai_daily_cap:
        usage.count += 1
        result = extract_firm_application(document)
        mode = "live"
    elif is_sample:
        document = FIRM_PACKAGE_TEXT
        result = gate(ILLUSTRATIVE_EXTRACTION, document, "end_turn", "illustrative output")
        mode = "illustrative"
    else:
        reason = "the daily cap is reached" if settings.ai_live else "no API key or workload identity is configured"
        raise HTTPException(503, f"Live AI isn't available ({reason}). Try the sample package, or enter the "
                                 "application by form.")
    from sqlalchemy import select
    from ..orm import Case, Firm
    existing = db.scalar(select(Firm).where(Firm.crd == result.application.crd_number)) \
        if result.application.crd_number else None
    if existing is not None:
        prior = db.scalar(select(Case).where(Case.firm_id == existing.id, Case.kind == "firm"))
        db.commit()
        raise HTTPException(409, f"{existing.legal_name} (CRD {existing.crd}) is already on the platform"
                                 + (f" as case {prior.id}" if prior else ""))
    version = ai_suggestions.prompt_version() if mode == "live" else None
    extraction = {**result.to_dict(), "mode": mode, "document": document[:20_000], "prompt_version": version}
    case = create_firm_case(db, result.application, user, extraction=extraction)
    suggestions = ai_suggestions.create_for(db, case, extraction, mode, result.model, version)
    raw = original if original is not None else document.encode("utf-8")
    store(db, case, user, "application_package", filename or "application-package.txt", raw, "intake",
          content_type=None if original is not None else "text/plain")
    audit.record(db, user, "ai_extraction", "case", case.id, case_id=case.id, mode=mode, model=result.model,
                 accepted=len(result.accepted), rejected=len(result.rejected), prompt_version=version,
                 suggestions=len(suggestions))
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class FirmForm(BaseModel):
    application: dict
    documents: list[dict] = Field(default_factory=list)   # [{doc_type, sample} or {doc_type, filename, content_b64}]


@router.post("/firm")
def firm_by_form(body: FirmForm, user: User = Depends(ops), db: Session = Depends(get_db)):
    try:
        app = firm_from(body.application)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"Application not readable: {exc}")
    if not app.legal_name:
        raise HTTPException(422, "The firm's legal name is required")
    # Documents count only when attached, and identity is verified by a person after the documents arrive
    from dataclasses import replace
    app = replace(app, formation_documents=False, ownership_chart=False, beneficial_ownership_certification=False,
                  custodial_agreement_signed=False, reliance_contract=False,
                  people=[replace(p, identity_verified=False) for p in app.people])
    from sqlalchemy import select
    from ..orm import Firm
    if app.crd_number and db.scalar(select(Firm).where(Firm.crd == app.crd_number)):
        raise HTTPException(409, "A firm with that CRD number already exists")
    case = create_firm_case(db, app, user)
    fields = attach_at_intake(db, case, user, body.documents, "email")
    if fields:
        case.application = {**case.application, **fields}
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)
