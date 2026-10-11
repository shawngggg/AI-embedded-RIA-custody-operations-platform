"""Documents on a case: list, upload, open, and mark not in good order."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..documents import (CHANNELS, DOC_TYPES, FIELD_DOCS, MAX_BYTES, case_documents, doc_dict, mark, reopen,
                         reopen_refusal, sample_document, store)
from ..runner import run_case
from ..orm import Document, User
from ..queues import is_closed
from ..security import current_user
from ..views import get_case_for

router = APIRouter(prefix="/api", tags=["documents"])
OPS = ("ops_analyst", "ops_supervisor")
REQUESTERS = ("ria_admin", "ria_user", "client")


@router.get("/documents/types")
def types(user: User = Depends(current_user)):
    return {"types": [{"value": k, "label": v["label"]} for k, v in DOC_TYPES.items()],
            "channels": [{"value": k, "label": v} for k, v in CHANNELS.items() if k != "intake"],
            "field_docs": FIELD_DOCS, "max_bytes": MAX_BYTES, "samples": settings.demo_mode}


@router.get("/cases/{case_id}/documents")
def list_documents(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    case = get_case_for(db, user, case_id)
    return [doc_dict(d) for d in case_documents(db, case)]


@router.post("/cases/{case_id}/documents")
def upload(case_id: int, doc_type: str = Form(...), channel: str = Form("portal"), received_on: str | None = Form(None),
           sample: bool = Form(False), file: UploadFile | None = File(None), user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    case = get_case_for(db, user, case_id)
    if user.role not in OPS + REQUESTERS:
        raise HTTPException(403, "Operations or the requester adds documents")
    if is_closed(case.kind, case.status):
        raise HTTPException(409, "This case is closed")
    if user.role in REQUESTERS:
        channel = "portal"
    when = date.fromisoformat(received_on) if received_on else settings.today()
    if when > settings.today():
        raise HTTPException(422, "The received date can't be in the future")
    if sample:
        if not settings.demo_mode:
            raise HTTPException(403, "Sample documents are for the demo only")
        filename, data = sample_document(db, case, doc_type, when)
    else:
        if file is None:
            raise HTTPException(422, "Choose a file")
        data = file.file.read(MAX_BYTES + 1)
        filename = file.filename or "document"
    doc = store(db, case, user, doc_type, filename, data, channel, when, synthetic=sample)
    db.commit()
    return doc_dict(doc)


@router.get("/documents/{doc_id}/file")
def open_file(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    get_case_for(db, user, doc.case_id)          # same visibility as the case
    return Response(doc.data, media_type=doc.content_type,
                    headers={"Content-Disposition": f'inline; filename="{doc.filename}"',
                             "X-Content-SHA256": doc.sha256, "Cache-Control": "private, no-store"})


class NotInGoodOrder(BaseModel):
    reason: str = Field(min_length=5)


@router.post("/documents/{doc_id}/reject")
def reject(doc_id: int, body: NotInGoodOrder, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Mark a document not in good order: one waiting to be checked, or one already accepted (reopen)."""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    case = get_case_for(db, user, doc.case_id)
    if doc.status == "in_good_order":
        why = reopen_refusal(case, doc, user)
        if why:
            raise HTTPException(403 if "reopens" in why else 409, why)
        reopen(db, case, doc, user, body.reason)
        run_case(db, case, user)
        db.commit()
        return doc_dict(doc)
    if user.role not in OPS:
        raise HTTPException(403, "Operations checks documents")
    if doc.status != "received":
        raise HTTPException(409, "Only a document waiting to be checked can be marked not in good order")
    mark(db, doc, "not_in_good_order", user.display_name, user, body.reason)
    db.commit()
    return doc_dict(doc)
