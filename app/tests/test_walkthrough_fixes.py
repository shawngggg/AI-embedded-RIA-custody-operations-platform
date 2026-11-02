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


# --- Group B: identity and compliance decisions --------------------------------------------------------------------

def _with_holder(name: str, tax_id: str, phone: str, email: str) -> dict:
    return {**NEW_APP, "holders": [{**NEW_APP["holders"][0], "name": name, "tax_id": tax_id, "phone": phone,
                                    "email": email}]}


def test_5_a_tax_id_on_file_under_another_name_goes_to_cip_and_never_to_the_ria(as_user):
    from app.tests.test_api import WHY
    app = _with_holder("Gus Hale", "900-00-2007", "435-555-0177", "gus.hale@example.com")   # Jordan Pike's tax ID
    portal = as_user("granite.advisor").post("/api/portal/accounts", json={"application": app,
                                                                           "documents": ACCOUNT_DOCS}).json()
    assert portal["status"] == "IN_REVIEW" and "red flag" not in str(portal).lower()
    cip = as_user("ops.cip")
    detail = cip.get(f"/api/cases/{portal['id']}").json()
    assert (detail["status"], detail["queue"]) == ("PENDING_RED_FLAG_REVIEW", "verification")
    step = next(s for s in detail["result"]["steps"] if s["step"].startswith("Identity red flags"))
    assert "Tax ID ending 2007 is on file for Jordan Pike" in step["detail"][0]
    choice = next(a for a in detail["available_decisions"] if a["decision_type"] == "red_flag_review")
    assert "Jordan Pike" in choice["values"][0]["prefill"]          # the rationale starts from the evidence
    p = cip.post(f"/api/cases/{portal['id']}/decisions", json={
        "decision_type": "red_flag_review", "value": "cleared", "reference": "RFL-1",
        "note": "Same client: a legal name change on file, matched to the marriage certificate"}).json()
    done = as_user("ops.supervisor").post(f"/api/decisions/{p['id']}/approve").json()
    assert done["status"] == "PENDING_ACCEPTANCE"


def test_5_a_red_flag_cip_suspects_goes_to_aml_held(as_user):
    app = _with_holder("Ida Voss", "900-00-2007", "435-555-0178", "ida.voss@example.com")
    cid = as_user("granite.advisor").post("/api/portal/accounts", json={"application": app,
                                                                        "documents": ACCOUNT_DOCS}).json()["id"]
    state = as_user("ops.cip").get(f"/api/cases/{cid}").json()["identity_check"]
    assert state["can_record"] and state["people"] == ["Ida Voss"]      # Path A too
    d = as_user("ops.cip").post(f"/api/cases/{cid}/identity-check", json={
        "kind": "suspected_fraud", "person": "Ida Voss", "note": "The tax ID belongs to another client"}).json()
    assert d["status"] == "PENDING_FRAUD_REVIEW" and "IDFR" in [r["code"] for r in d["restrictions"] if r["active"]]


def test_5_a_phone_shared_with_another_family_is_flagged_and_a_household_isnt(as_user):
    from app.db import SessionLocal
    from app.red_flags import find
    as_user(ADMIN)
    with SessionLocal() as db:
        other = find(db, [{"name": "Pat Quinn", "tax_id": "900-00-9901", "phone": "801-555-0131"}], None)
        household = find(db, [{"name": "Sam Pike", "tax_id": "900-00-9902", "phone": "801-555-0131"}], None)
        same = find(db, [{"name": "Jordan A. Pike", "tax_id": "900-00-2007", "phone": "801-555-0131"}], None)
    assert other and "Phone ending 0131" in other[0]["flag"]
    assert household == [] and same == []                           # a household; the same person


def test_9_confirmed_fraud_is_referred_to_the_bsa_officer_who_decides(as_user):
    from app.tests.test_api import WHY
    app = _with_holder("Ida Voss", "900-00-2007", "435-555-0178", "ida.voss@example.com")
    cid = as_user("granite.advisor").post("/api/portal/accounts", json={"application": app,
                                                                        "documents": ACCOUNT_DOCS}).json()["id"]
    as_user("ops.cip").post(f"/api/cases/{cid}/identity-check", json={
        "kind": "suspected_fraud", "person": "Ida Voss", "note": "The tax ID belongs to another client"})
    p = as_user("aml.maker").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "identity_fraud", "value": "confirmed", "reference": "IDF-9"}).json()
    as_user("aml.checker").post(f"/api/decisions/{p['id']}/approve")
    refs = as_user("aml.officer").get("/api/sar-referrals").json()
    mine = next(r for r in refs if r["case_id"] == cid)
    assert mine["status"] == "open" and mine["days_left"] == 30 and mine["can_decide"]
    assert as_user("aml.maker").get("/api/work").json()["sar_referrals"]
    assert as_user("ops.cip").get("/api/sar-referrals").status_code == 403
    assert as_user("ops.cip").get(f"/api/cases/{cid}").json()["sar_referral"] is None
    assert "sar_referral" not in as_user("granite.advisor").get(f"/api/cases/{cid}").json()
    assert as_user("aml.maker").post(f"/api/cases/{cid}/sar-referral", json={
        "decision": "file", "note": WHY}).status_code == 403                    # the officer decides
    assert as_user("aml.officer").post(f"/api/cases/{cid}/sar-referral", json={
        "decision": "file", "note": "short"}).status_code == 422
    done = as_user("aml.officer").post(f"/api/cases/{cid}/sar-referral", json={
        "decision": "file", "note": "Synthetic identity using another client's tax ID: reportable"}).json()
    assert done["status"] == "decided" and done["decided_by"] == "Ines Carvalho"


def test_10_no_notice_needs_a_second_person_and_compliance_sees_the_risk_event(as_user):
    i = as_user("aml.maker").post("/api/privacy", json={
        "summary": "A misdirected email sent one client's statement to the wrong adviser.", "aware_on": "2026-10-20",
        "accounts": ["ACC-3001"], "information": "Name, address, balances",
        "service_provider": "Statement print vendor", "provider_aware_at": "2026-10-15T09:00:00",
        "provider_notified_at": "2026-10-19T09:00:00"}).json()
    assert i["provider_late"] is True                                # 96 hours: over the 72
    aml = as_user("aml.maker")
    aml.post(f"/api/privacy/{i['id']}/contain", json={"note": "The recipient confirmed deletion in writing"})
    d = aml.post(f"/api/privacy/{i['id']}/determine", json={
        "decision": "no_notice", "reason": "Deleted unread, confirmed in writing"}).json()
    assert d["status"] == "no_notice_proposed" and d["days_left"] is not None     # the clock still runs
    assert as_user("aml.maker").post(f"/api/privacy/{i['id']}/approve", json={
        "approve": True, "note": "Looks right to me"}).status_code == 403
    done = as_user("aml.checker").post(f"/api/privacy/{i['id']}/approve", json={
        "approve": True, "note": "Read the recipient's written confirmation"}).json()
    assert done["status"] == "closed" and done["approved_by"] == "Morgan Lee"
    events = as_user("aml.checker").get("/api/risk-events").json()["events"]
    assert any(e["source"] == "privacy" for e in events)


def test_11_a_change_callback_dials_a_number_from_before_the_change(as_user):
    from app.tests.test_api import case_id
    cid = case_id(as_user("ops.analyst"), "bank instruction on Maria Chen")
    svc = as_user("ops.service")                     # the test client is shared: sign in after looking the case up
    action = next(a for a in svc.get(f"/api/cases/{cid}").json()["actions"] if a["key"] == "change_callback")
    offered = [o[0] for o in next(f for f in action["fields"] if f["name"] == "phone")["options"]]
    assert offered and all(offered)
    r = svc.post(f"/api/cases/{cid}/actions/change_callback", json={"data": {"confirmed": "yes", "phone": "801-555-9999"}})
    assert r.status_code == 422 and "before any recent change" in r.json()["detail"]
    r = svc.post(f"/api/cases/{cid}/decisions", json={"decision_type": "confirmation", "value": "confirmed",
                                                     "reference": "CALLBACK-11"})
    assert r.status_code == 422                                     # no confirmation without the callback
    r = svc.post(f"/api/cases/{cid}/actions/change_callback", json={"data": {"confirmed": "yes", "phone": offered[0]}})
    assert r.status_code == 200


def test_12_the_title_fix_uses_the_delivering_firms_title(as_user):
    xfer = as_user("ops.transfers")
    pending = [c for c in xfer.get("/api/cases").json() if c["status"] == "PENDING_FIX"]
    assert pending, "the seed has a transfer the pre-screen caught"
    detail = xfer.get(f"/api/cases/{pending[0]['id']}").json()
    assert "statement from the delivering firm" in str(detail)
    action = next(a for a in detail["actions"] if a["key"] == "fix")
    assert "delivering firm's statement" in str(action["fields"])
    r = xfer.post(f"/api/cases/{pending[0]['id']}/actions/fix", json={"data": {"choice": "apply",
                                                                              "note": "Matches the March statement"}})
    assert r.status_code == 200, r.text
    from app.db import SessionLocal
    from app.orm import Case
    with SessionLocal() as db:
        assert db.get(Case, pending[0]["id"]).application["title_confirmed_from_statement"] is True


def test_15_slas_count_business_hours_on_the_queues_calendar():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app import sla
    et = ZoneInfo("America/New_York")
    friday_5pm = datetime(2026, 10, 30, 17, 0, tzinfo=et)
    assert sla.due(friday_5pm, "money_release") == datetime(2026, 11, 2, 11, 0, tzinfo=et)     # 1h Friday, 3h Monday
    assert sla.due(datetime(2026, 10, 30, 10, 0, tzinfo=et), "sanctions_review") == datetime(2026, 11, 2, 10, 0, tzinfo=et)
    tuesday = datetime(2026, 11, 10, 10, 0, tzinfo=et)                     # Veterans Day: the Fed closes, NSCC doesn't
    assert sla.due(tuesday, "sanctions_review").day == 12 and sla.due(tuesday, "transfer_exceptions").day == 11
    assert sla.due(friday_5pm, "items_requested") is None                 # waits on the RIA: no custodian SLA


def test_22_settlement_sees_its_work_and_the_qa_sample_is_a_keyed_draw(as_user):
    work = as_user("ops.settlement").get("/api/work").json()
    s = work["settlement"]
    assert {"breaks", "unapplied", "reserve_deficit", "control_deficits", "last_cycle"} <= set(s)
    assert s["breaks"] + s["unapplied"] > 0, "the seed has breaks and receipts to work"
    assert "settlement" not in as_user("ops.analyst").get("/api/work").json()
    from app import ai_suggestions
    draws = [ai_suggestions.qa_draw(i) for i in range(1, 2001)]
    assert draws == [ai_suggestions.qa_draw(i) for i in range(1, 2001)]          # reproducible under the key
    picked = [i for i, d in enumerate(draws, 1) if d < 1 / ai_suggestions.QA_EVERY]
    assert 300 < len(picked) < 500                                                  # about 1 in 5
    assert {i % 5 for i in picked} == {0, 1, 2, 3, 4}                               # not every fifth id
    assert len(ai_suggestions.qa_key_fingerprint()) == 10


# --- Group D: settlement, reviews, billing ------------------------------------------------------------------------

def test_13_the_cycle_runs_as_of_the_last_business_day_on_a_closed_day(as_user, monkeypatch):
    from datetime import date, timedelta
    from app import settlement
    from app.config import settings
    today = date.fromisoformat(settings.as_of) if settings.as_of else date.today()
    sunday = today + timedelta(days=(6 - today.weekday()))
    monkeypatch.setattr(settings, "as_of", sunday.isoformat())
    view = as_user("ops.settlement").post("/api/settlement/run").json()
    run = view["last_run"]
    assert view["closed_today"] and run["business_date"] == settlement.business_date(sunday).isoformat() < sunday.isoformat()
    assert "isn't a business day" in run["steps"][0]["detail"][0]
    assert view["protection"]["reserve"]["as_of"] == run["business_date"]       # the reserve as of that day too


def _funding(run: dict) -> dict:
    return next(s for s in run["steps"] if s["step"].startswith("Send net funding"))


def test_14_a_rerun_sends_only_new_activity_and_the_reserve_stays_apart(as_user, monkeypatch):
    from app import settlement
    from app.config import settings
    monkeypatch.setattr(settings, "as_of", settlement.business_date().isoformat())   # weekends too
    s = as_user("ops.settlement")
    s.post("/api/settlement/run")
    again = s.post("/api/settlement/run").json()
    assert again["last_run"]["instruction"] is None
    assert _funding(again["last_run"])["outcome"].startswith(("Nothing new since instruction", "No cash moved"))
    receipt = s.post("/api/settlement/receipts", json={"channel": "Fedwire", "amount": 50_000,
                                                       "sender": "Bear River Savings Bank",
                                                       "memo": "FBO ACC-3006 Samuel Adeyemi"}).json()
    rid = next(x["id"] for x in receipt["receipts"] if x["amount"] == 50_000)
    s.post(f"/api/settlement/receipts/{rid}/apply", json={"account_id": "ACC-3006", "note": "Memo names the account"})
    third = s.post("/api/settlement/run").json()
    ins = third["last_run"]["instruction"]
    assert ins["status"] == "sent" and ins["net"] == 50_000 and ins["ref"].startswith("SI-")
    assert ins["ref"] in [i["ref"] for i in third["instructions"]]
    s.post("/api/settlement/reserve/deposit", json={"amount": 100_000, "note": "Cushion ahead of month end"})
    fourth = s.post("/api/settlement/run").json()
    assert fourth["last_run"]["instruction"] is None                               # not customer settlement funding
    reserve = next(x for x in fourth["last_run"]["steps"] if x["step"].startswith("Special reserve bank account"))
    assert "100,000.00" in reserve["outcome"] and fourth["reserve_moves"]
    assert all(f["channel"] != "Other" or f["net"] != -100_000 for f in fourth["today_flows"])


def test_16_the_nightly_batch_restricts_a_missed_refresh(as_user):
    from datetime import timedelta
    from app.config import settings
    from app.db import SessionLocal
    from app.orm import Review
    reviews = {r["subject_id"]: r for r in as_user("review.analyst").get("/api/reviews").json()}
    assert reviews["ACC-2004"]["status"] == "restricted"         # Whitfield: the seed's catch-up nightly run did it
    with SessionLocal() as db:
        r = db.query(Review).filter(Review.subject_id == "ACC-2002").one()
        r.status, r.requested_on = "requested", settings.today() - timedelta(days=40)
        r.response_due = settings.today() - timedelta(days=1)
        db.commit()
    out = as_user("ops.supervisor").post("/api/nightly/run").json()
    assert out["reviews"]["restricted"] == ["ACC-2002"]
    assert [c["code"] for c in as_user("review.analyst").get("/api/restrictions?target=ACC-2002").json()] == ["KYCR"]


def test_17_a_review_leaves_its_basis_and_a_higher_risk_one_gets_a_checker(as_user):
    rv = as_user("review.analyst")
    kenji = next(r for r in rv.get("/api/reviews").json() if r["subject_id"] == "ACC-2002")
    url = f"/api/reviews/{kenji['id']}/complete"
    note = "Checked the account profile, activity, and screening: nothing new since opening"
    assert rv.post(url, json={"outcome": "no_change", "new_tier": "LOW"}).status_code == 422          # no note
    r = rv.post(url, json={"outcome": "no_change", "new_tier": "LOW", "note": note, "basis": "refresh_received"})
    assert r.status_code == 422 and "No refresh was requested" in r.json()["detail"]
    assert rv.post(url, json={"outcome": "no_change", "new_tier": "LOW", "note": note,
                              "basis": "skipped", "skip_reason": "short"}).status_code == 422
    proposed = rv.post(url, json={"outcome": "risk_increased", "new_tier": "MEDIUM", "note": note + "; but wires "
                                  "to a new country", "basis": "skipped",
                                  "skip_reason": "Profile confirmed with the RIA by phone on Nov. 2"}).json()
    assert proposed["status"] == "awaiting_approval" and proposed["proposal"]["by"] == "Casey Nguyen"
    approve = f"/api/reviews/{kenji['id']}/approve"
    assert as_user("review.analyst").post(approve, json={"approve": True, "note": "Looks right"}).status_code == 403
    work = as_user("aml.checker").get("/api/work").json()
    assert work["reviews_to_approve"] == 1
    done = as_user("aml.checker").post(approve, json={"approve": True,
                                                      "note": "Read the note; the wires match the stated purpose"}).json()
    assert done["status"] == "complete" and done["risk_tier"] == "MEDIUM"
    last = done["history"][-1]
    assert last["approved_by"] == "Morgan Lee" and last["note"].startswith("Checked") and last["skip_reason"]


def test_20_the_fee_basis_ties_to_the_statement_value(as_user):
    from app import book
    from app.db import SessionLocal
    from app.orm import Statement
    as_user("ops.supervisor")
    with SessionLocal() as db:
        b = book.current(db)
        closing = {s.account_id: (s.period_end, s.summary["closing_value"]) for s in db.query(Statement).all()}
    granite = [f for f in b["fees"] if f["firm_crd"] == "310001" and not f["run"].endswith("-NEW")]
    assert granite
    for f in granite:
        on, value = closing[f["account"]]
        assert f["statement_value"] == round(value, 2) and f["billable_value"] == round(value, 2), f
        assert f["basis_source"] == f"Statement value, {on:%b} {on.day}"
    new = [f for f in b["fees"] if f["run"].endswith("-NEW")]
    assert all(f["statement_value"] is None and "no statement yet" in f["basis_source"] for f in new)
