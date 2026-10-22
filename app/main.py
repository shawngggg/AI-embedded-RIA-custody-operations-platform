"""
The MVP app: the API under /api and the built React screens at every other path.

Run locally:  uvicorn app.main:app --reload
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal
from .routers import (admin, audit_log, auth, cases, dashboard, documents, intake, portal, restrictions, reviews,
                      risk_events, work, messages, outbox, ai_eval, walkthrough)
from .seed import SEED_FINGERPRINT, ensure_seeded


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.cookie_secure and settings.secret_key.startswith("dev-only-"):
        raise RuntimeError("Set SECRET_KEY before deploying: sessions would be signed with the public dev key")
    if settings.seed_on_start:
        with SessionLocal() as db:
            ensure_seeded(db)
    yield


app = FastAPI(title="RIA Custody Operations Platform: MVP", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")

for r in (auth.router, cases.router, restrictions.router, reviews.router, dashboard.router, audit_log.router,
          admin.router, portal.router, intake.router, documents.router, risk_events.router, work.router, messages.router, outbox.router, ai_eval.router,
          walkthrough.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    # revision: the Cloud Run revision serving (K_REVISION), so a deploy can confirm it's live; seed: the demo data
    return {"ok": True, "demo_mode": settings.demo_mode, "as_of": settings.today().isoformat(),
            "revision": os.environ.get("K_REVISION"), "seed": SEED_FINGERPRINT}


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


_static = settings.static_dir
if _static.exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        candidate = _static / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")
