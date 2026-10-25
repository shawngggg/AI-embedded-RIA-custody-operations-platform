import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="mvp-test-")
os.environ.update({
    # TEST_DATABASE_URL runs the suite against Postgres (CI does); otherwise a throwaway SQLite file
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db",
    "AS_OF": os.environ.get("TEST_AS_OF", ""),     # empty: today, the same clock the seed backdates from
    "CLOCK": "10:00",                              # before the rail cutoffs, whatever the hour the suite runs
    "DEMO_MODE": "true",
    "SEED_ON_START": "true",
    "STATIC_DIR": f"{_tmp}/no-static",
    "ANTHROPIC_API_KEY": "",
    "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import reset_database, seed  # noqa: E402


_SQLITE = os.environ["DATABASE_URL"].startswith("sqlite:///")
_TEMPLATE = f"{_tmp}/seeded.db"


_PG_TEMPLATE: list = []


def _fresh_database() -> None:
    """A freshly seeded database for each test. On SQLite the seeded file is copied from a template made once, which
    keeps the suite fast now that the seed holds 100-odd cases, their documents, and the ledger. On Postgres the seed
    is built once in a local SQLite file and bulk-copied in for each test, the same path a deploy and a demo reset take
    (app/seed.py, seed_database)."""
    if not _SQLITE:
        from app.db import engine
        from app.seed import build_local_seed, copy_database
        if not _PG_TEMPLATE:
            _PG_TEMPLATE.append(build_local_seed(f"{_tmp}/pg-template/seed.db")[0])
        engine.dispose()
        copy_database(_PG_TEMPLATE[0], engine)
        return
    import shutil
    from app.db import engine
    db_file = os.environ["DATABASE_URL"].removeprefix("sqlite:///")
    engine.dispose()
    if not os.path.exists(_TEMPLATE):
        reset_database()
        with SessionLocal() as db:
            seed(db)
        engine.dispose()
        shutil.copyfile(db_file, _TEMPLATE)
    else:
        shutil.copyfile(_TEMPLATE, db_file)


@pytest.fixture()
def client():
    _fresh_database()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def as_user(client):
    """Sign in as a demo user: as_user('ops.analyst') returns a client with that session."""
    def sign_in(username: str) -> TestClient:
        client.cookies.clear()
        r = client.post("/api/auth/demo", json={"username": username})
        assert r.status_code == 200, r.text
        return client
    return sign_in


R2_KINDS = {"transfer", "entitlement", "product", "offboarding", "deceased", "advisor_move", "merger", "transition"}


def case_id(client, title_part: str, kind: str | None = None) -> int:
    """The first case titled like `title_part`: an onboarding or change case, unless `kind` names another (release 2
    transfers carry client names in their titles too)."""
    for c in client.get("/api/cases").json():
        if title_part in c["title"] and (c["kind"] == kind if kind else c["kind"] not in R2_KINDS):
            return c["id"]
    raise AssertionError(f"No case titled like {title_part}")
