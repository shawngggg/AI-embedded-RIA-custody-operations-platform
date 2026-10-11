import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="mvp-test-")
os.environ.update({
    # TEST_DATABASE_URL runs the suite against Postgres (CI does); otherwise a throwaway SQLite file
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db",
    "AS_OF": "2026-10-10",
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


@pytest.fixture()
def client():
    reset_database()
    with SessionLocal() as db:
        seed(db)
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


def case_id(client, title_part: str) -> int:
    for c in client.get("/api/cases").json():
        if title_part in c["title"]:
            return c["id"]
    raise AssertionError(f"No case titled like {title_part}")
