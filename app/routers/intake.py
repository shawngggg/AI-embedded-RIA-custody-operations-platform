"""
Intake: a firm application by form, or a package read by AI behind the controls gate.

Live AI calls need ANTHROPIC_API_KEY and stay under the daily cap. Without
them, the sample package runs through the gate with the illustrative model
response, labeled as such; any other document waits for live AI.
"""
import io
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from extraction import DEFAULT_MODEL, extract_firm_application, gate
from sample_data import FIRM_PACKAGE_TEXT, ILLUSTRATIVE_EXTRACTION

from .. import audit
from ..config import settings
from ..db import get_db
from ..orm import AiUsage, User
from ..runner import run_case
from ..security import require_roles
from ..seed import create_firm_case
from ..serialize import firm_from
from ..views import case_detail

router = APIRouter(prefix="/api/intake", tags=["intake"])
ops = require_roles("ops_analyst", "ops_supervisor")


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
    return {"live": bool(settings.anthropic_api_key), "model": DEFAULT_MODEL, "used_today": used,
            "cap": settings.ai_daily_cap, "sample_text": FIRM_PACKAGE_TEXT}


def _read(file: UploadFile | None, text: str | None) -> str:
    if text and text.strip():
        return text.replace("\r\n", "\n")   # browsers send form text with CRLF line breaks
    if file is None:
        raise HTTPException(422, "Upload a PDF or text file, or paste the text")
    raw = file.file.read()
    if file.filename and file.filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")


@router.post("/extract")
def extract(file: UploadFile | None = File(None), text: str | None = Form(None),
            user: User = Depends(ops), db: Session = Depends(get_db)):
    document = _read(file, text)
    if len(document) > 60_000:
        raise HTTPException(413, "Keep packages under 60,000 characters for the demo")
    usage = _usage(db, settings.today())
    is_sample = " ".join(document.split()) == " ".join(FIRM_PACKAGE_TEXT.split())
    if settings.anthropic_api_key and usage.count < settings.ai_daily_cap:
        usage.count += 1
        result = extract_firm_application(document)
        mode = "live"
    elif is_sample:
        document = FIRM_PACKAGE_TEXT
        result = gate(ILLUSTRATIVE_EXTRACTION, document, "end_turn", "illustrative output")
        mode = "illustrative"
    else:
        reason = "the daily cap is reached" if settings.anthropic_api_key else "no API key is configured"
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
    extraction = {**result.to_dict(), "mode": mode, "document": document[:20_000]}
    case = create_firm_case(db, result.application, user, extraction=extraction)
    audit.record(db, user, "ai_extraction", "case", case.id, case_id=case.id, mode=mode, model=result.model,
                 accepted=len(result.accepted), rejected=len(result.rejected))
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class FirmForm(BaseModel):
    application: dict


@router.post("/firm")
def firm_by_form(body: FirmForm, user: User = Depends(ops), db: Session = Depends(get_db)):
    try:
        app = firm_from(body.application)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"Application not readable: {exc}")
    if not app.legal_name:
        raise HTTPException(422, "The firm's legal name is required")
    from sqlalchemy import select
    from ..orm import Firm
    if app.crd_number and db.scalar(select(Firm).where(Firm.crd == app.crd_number)):
        raise HTTPException(409, "A firm with that CRD number already exists")
    case = create_firm_case(db, app, user)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)
