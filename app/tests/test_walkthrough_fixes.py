"""The fixes from the role-by-role walkthrough (Nov. 1, 2026): what the client hears, what the record says, and the
controls each specialist expected. Numbers in the test names are the walkthrough's notes."""
from app.tests.test_api import ACCOUNT_DOCS, NEW_APP, accept

ADMIN = "admin.platform"


def outbox(as_user) -> list[dict]:
    """Every email in the outbox, newest first, as the outbox page shows them (the page itself lists the latest
    300). Signs the shared test client in as the administrator."""
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.orm import OutboundEmail
    from app.routers.outbox import email_dict
    as_user(ADMIN)
    with SessionLocal() as db:
        return [email_dict(db, e, full=True) for e in db.scalars(select(OutboundEmail).order_by(OutboundEmail.id.desc()))]


# --- Group A: what the client and the RIA hear --------------------------------------------------------------------

def test_1_statements_go_to_the_client_attached(as_user):
    emails = [e for e in outbox(as_user) if e["template"] == "statement_ready"]
    assert emails, "the seed generates last quarter's statements"
    for e in emails:
        assert "is attached" in e["body"] and "on request" not in e["body"]
        assert [a["filename"] for a in e["attachments"]] and e["attachments"][0]["filename"].endswith(".pdf")
        assert e["to"].endswith("@example.com")                       # the client, not an RIA user


def test_2_the_client_hears_the_account_is_open_with_the_trusted_contact_disclosure(as_user):
    app = {**NEW_APP, "holders": [{**NEW_APP["holders"][0], "name": "Nell Ortega", "tax_id": "900-00-3801",
                                   "phone": "801-555-0381", "email": "nell.ortega@example.com"}]}
    cid = as_user("granite.advisor").post("/api/portal/accounts", json={"application": app,
                                                                         "documents": ACCOUNT_DOCS}).json()["id"]
    assert accept(as_user("rs.principal"), cid)["status"] == "OPEN"
    mine = [e for e in outbox(as_user) if e["to"] == "nell.ortega@example.com"]
    assert [e["template"] for e in mine] == ["account_opened_client"]
    assert "Trusted contact on file: Leo Park" in mine[0]["body"] and "FINRA Rule 4512" in mine[0]["body"]
    assert "800-555-0142" in mine[0]["body"]                         # how to reach the custodian


def test_2_a_change_is_confirmed_to_the_client_and_an_email_change_to_both_emails(as_user):
    before = len(outbox(as_user))
    r = as_user("granite.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-3007", "change_type": "address", "details": {"new_address": "9 Elm St, Ogden, UT 84401"}})
    assert r.json()["status"] == "APPLIED", r.text
    new = outbox(as_user)[: len(outbox(as_user)) - before]
    client = [e for e in new if e["template"] == "change_confirmed_client"]
    assert client and "Old address:" in client[0]["body"] and "9 Elm St, Ogden" in client[0]["body"]
    assert "didn't ask for this change" in client[0]["body"]

    # An email change on another account (two changes together would be a fraud pattern, held for confirmation)
    r = as_user("granite.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-3003", "change_type": "contact_info", "details": {"email": "tomas.new@example.com"}})
    assert r.json()["status"] == "APPLIED", r.text
    to = {e["to"] for e in outbox(as_user) if e["template"] == "change_confirmed_client" and e["case_id"] == r.json()["id"]}
    assert "tomas.new@example.com" in to and len(to) == 2           # the old email hears about the change too


def test_3_every_step_that_says_sent_has_its_email(as_user):
    """A step marked Sent has the email behind it; planned work says so."""
    emails = outbox(as_user)
    ops = as_user("ops.supervisor")              # one test client: sign in after reading the outbox
    by_case: dict[int, set[str]] = {}
    for e in emails:
        if e["case_id"]:
            by_case.setdefault(e["case_id"], set()).add(e["template"].removesuffix("_by_mail"))
    checked = 0
    for c in ops.get("/api/cases").json():
        if c["kind"] not in ("account", "change"):
            continue
        steps = ops.get(f"/api/cases/{c['id']}").json()["result"].get("steps", [])
        for s in steps:
            assert not (s["step"].startswith("Path B: account-level AML monitoring") and s["outcome"] == "Enrolled")
            if s["step"] == "Send confirmation notices" and s["outcome"] == "Sent":
                assert by_case.get(c["id"], set()) & {"change_confirmed_client", "sloa_initial"}, c["title"]
                checked += 1
            if s["step"] in ("Request missing items from the RIA", "Return for correction",
                             "Request corrections from the RIA") and s["outcome"] == "Sent":
                assert by_case.get(c["id"], set()) & {"items_requested", "returned", "items_reminder", "withdrawn"}, c["title"]
                checked += 1
    assert checked >= 10


def test_4_a_refusal_the_ria_can_fix_says_why(as_user):
    ria = as_user("granite.advisor")
    r = ria.post("/api/money/transfers", json={"type": "wire_out", "account_id": "ACC-3001", "amount": 5000,
                                                "counterparty": {"owner_name": "Summit Roofing LLC", "owner_kind": "entity",
                                                                 "institution": "Zions Bank", "account_last4": "2222"}})
    case = r.json()["case"]
    assert case["status"] == "DECLINED"
    assert "standing letter of authorization" in case["status_note"]
    assert any(d["label"] == "Not accepted because" for d in case["details"])
    notice = [e for e in outbox(as_user) if e["case_id"] == case["id"]]
    assert notice and "standing letter of authorization" in notice[0]["body"]


def test_4_a_compliance_decline_stays_neutral(as_user):
    from app.portal_view import refusal_note
    from app.orm import Case
    c = Case(kind="transfer", status="REJECTED", result={"summary": {}, "requested_items": [
        "The risk review didn't approve this request"]})
    assert refusal_note(c) is None                                   # no "Not accepted because": nothing leaks


def test_19_statements_use_client_words_and_name_the_custodian(as_user):
    from app.db import SessionLocal
    from app.orm import Account
    from app.statements import build, previous_quarter
    as_user(ADMIN)
    with SessionLocal() as db:
        lines, _ = build(db, db.get(Account, "ACC-3005"), *previous_quarter())
    text = "\n".join(lines)
    assert "release 2 ledger" not in text and "(submission" not in text
    assert "Custody Operations (synthetic custodian)" in text and "800-555-0142" in text
    assert " par @ " in text                                          # the Treasury note, as a percent of par


def test_21_tenants_in_common_item_names_the_total(as_user):
    app = {**NEW_APP, "registration_type": "joint", "joint_tenancy": "tenants_in_common",
           "holders": [{**NEW_APP["holders"][0], "name": "Ana Ruiz", "tax_id": "900-00-3803", "ownership_pct": 60},
                       {**NEW_APP["holders"][0], "name": "Ben Ruiz", "tax_id": "900-00-3804", "ownership_pct": 30,
                        "phone": "801-555-0383"}]}
    r = as_user("granite.advisor").post("/api/portal/accounts", json={"application": app, "documents": ACCOUNT_DOCS})
    items = r.json()["requested_items"]
    assert any("they add up to 90%, not 100%" in i for i in items), items      # ACC-013 v2, from Nov. 1, 2026


def test_1_graph_sends_the_statement_pdf_as_an_attachment(as_user, monkeypatch):
    import base64
    from sqlalchemy import select
    from app import mail
    from app.db import SessionLocal
    from app.orm import OutboundEmail, Statement
    monkeypatch.setenv("MS_TENANT_ID", "t"); monkeypatch.setenv("MS_CLIENT_ID", "c")
    monkeypatch.setenv("MS_CLIENT_SECRET", "s"); monkeypatch.setenv("MAIL_SENDER", "notices@custodian.example")
    sent = []

    class Resp:
        def __init__(self, code, body=None): self.status_code, self._body, self.text = code, body or {}, ""
        def json(self): return self._body
        def raise_for_status(self): pass

    class Http:
        def post(self, url, **kw):
            sent.append(kw)
            return Resp(200, {"access_token": "x"}) if "login" in url else Resp(202)
    as_user(ADMIN)
    with SessionLocal() as db:
        e = db.scalars(select(OutboundEmail).where(OutboundEmail.template == "statement_ready")).first()
        mail.send_m365(e, http=Http())
        st = db.get(Statement, e.attachments[0]["id"])
        files = sent[-1]["json"]["message"]["attachments"]
        assert files[0]["name"] == st.filename and files[0]["contentType"] == "application/pdf"
        assert base64.b64decode(files[0]["contentBytes"]) == st.content
