"""Release 2 core: the three-layer ledger, money movement and transfers, cash settlement, and reconciliation."""
import pytest

from .conftest import case_id


def _transfer(c, title_part):
    return case_id(c, title_part, kind="transfer")


def _status(c, cid):
    return c.get(f"/api/cases/{cid}").json()["status"]


def _proof_ok(c):
    checks = c.get("/api/settlement").json()["checks"]
    assert all(x["ok"] for x in checks), checks


def _release(as_user, cid, ref, maker="ops.transfers", checker="ops.money"):
    """A maker proposes the release and a different checker approves it (the clients share one session, so each
    signs in just before acting)."""
    d = as_user(maker).post(f"/api/cases/{cid}/decisions", json={"decision_type": "release", "value": "released",
                                                                  "reference": ref}).json()
    assert d.get("status") == "pending", d
    return as_user(checker).post(f"/api/decisions/{d['id']}/approve")


# --- the ledger -------------------------------------------------------------------------------------------------

def test_the_seeded_books_balance_at_all_three_layers(as_user):
    s = as_user("ops.settlement")
    view = s.get("/api/settlement").json()
    assert all(x["ok"] for x in view["checks"]) and len(view["checks"]) == 3
    assert {m["master"] for m in view["masters"]} >= {"MASTER-310001", "MASTER-310003", "MASTER-310031"}
    assert view["last_run"]["steps"] and view["in_transit"]


def test_a_posting_that_breaks_the_rollup_is_rejected(client):
    from app import ledger
    from app.db import SessionLocal
    from custody.ledger import CASH, client_move, units
    with SessionLocal() as db:
        legs = client_move("ACC-2001", "310001", CASH, units(100), "settled", "1010")
        with pytest.raises(ledger.LedgerError):
            ledger.post(db, "BAD-1", "test", "No contra", legs[:-1], __import__("datetime").date.today())
        with pytest.raises(ledger.LedgerError):        # the master named isn't the account's RIA
            bad = [legs[0]._replace(units=1) if False else legs[0],
                   type(legs[1])("master", "MASTER-310003", CASH, "settled", units(100)), legs[2], legs[3]]
            ledger.post(db, "BAD-2", "test", "Wrong master", bad, __import__("datetime").date.today())
        assert ledger.post(db, "GOOD-1", "test", "Balanced", legs, __import__("datetime").date.today()) is not None
        assert ledger.post(db, "GOOD-1", "test", "Replay", legs, __import__("datetime").date.today()) is None


# --- money movement: authority, controls, maker-checker ---------------------------------------------------------

def test_an_ria_wire_needs_a_maker_and_a_checker_within_dollar_authority(as_user):
    ria = as_user("granite.advisor")
    r = ria.post("/api/money/transfers", json={"type": "wire_out", "account_id": "ACC-3006", "amount": 4_000,
                                               "counterparty": {"owner_name": "Samuel Adeyemi",
                                                                "institution": "Wasatch Community Bank",
                                                                "account_last4": "4471"}}).json()
    cid = r["case"]["id"]
    assert r["case"]["status"] == "IN_REVIEW"                    # the portal's words for PENDING_RELEASE
    d = as_user("ops.transfers").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "release", "value": "released", "reference": "REL-T-1"}).json()
    assert as_user("ops.transfers").post(f"/api/decisions/{d['id']}/approve").status_code == 403   # maker != checker
    assert as_user("ops.analyst").post(f"/api/decisions/{d['id']}/approve").status_code == 403     # wrong team
    detail = as_user("ops.money").post(f"/api/decisions/{d['id']}/approve").json()
    assert detail["status"] == "AWAITING_SETTLEMENT"
    assert {e["kind"] for e in detail["ledger_entries"]} == {"wire_out_pending"}
    _proof_ok(as_user("ops.settlement"))


def test_over_an_analysts_dollar_authority_the_supervisor_checks(as_user):
    ria = as_user("granite.advisor")
    cid = ria.post("/api/money/transfers", json={"type": "wire_out", "account_id": "ACC-3005", "amount": 75_000,
                                                 "counterparty": {"owner_name": "Gregory Holm",
                                                                  "institution": "Great Salt Lake Trust Company",
                                                                  "account_last4": "6620"}}).json()["case"]["id"]
    d = as_user("ops.transfers").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "release", "value": "released", "reference": "REL-T-2"}).json()
    r = as_user("ops.money").post(f"/api/decisions/{d['id']}/approve")
    assert r.status_code == 403 and "dollar authority" in r.json()["detail"]
    assert as_user("ops.supervisor").post(f"/api/decisions/{d['id']}/approve").json()["status"] == \
        "AWAITING_SETTLEMENT"


def test_a_large_wire_waits_for_a_callback_on_a_number_on_file(as_user):
    cid = _transfer(as_user("ops.transfers"), "Wire out: Gregory Holm")
    assert _status(as_user("ops.transfers"), cid) == "PENDING_CALLBACK"
    assert as_user("ops.service").post(f"/api/cases/{cid}/actions/callback",
                                       json={"data": {"confirmed": "yes", "phone": "801-555-0110"}}).status_code == 403
    action = next(a for a in as_user("ops.transfers").get(f"/api/cases/{cid}").json()["actions"] if a["key"] == "callback")
    on_file = [o[0] for o in next(f for f in action["fields"] if f["name"] == "phone")["options"]]
    assert on_file and all(n.startswith("801-555-01") for n in on_file)        # from the account record
    for number in ("", "801-555-0999"):           # none named, or one off the request: refused (MM-006)
        r = as_user("ops.transfers").post(f"/api/cases/{cid}/actions/callback",
                                          json={"data": {"confirmed": "yes", "phone": number}})
        assert r.status_code == 422, number
    r = as_user("ops.transfers").post(f"/api/cases/{cid}/actions/callback",
                                      json={"data": {"confirmed": "yes", "phone": on_file[0]}})
    assert r.json()["case"]["status"] == "PENDING_RELEASE"
    d = as_user("ops.transfers").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "release", "value": "released", "reference": "REL-T-3"}).json()
    assert as_user("ops.money").post(f"/api/decisions/{d['id']}/approve").status_code == 403   # $250,000: over authority
    assert as_user("ops.supervisor").post(f"/api/decisions/{d['id']}/approve").json()["status"] == "AWAITING_SETTLEMENT"


def test_who_may_ask_for_money_movement(as_user):
    ria = as_user("granite.advisor")
    sd = ria.post("/api/money/transfers", json={"type": "ach_withdrawal", "account_id": "ACC-2003", "amount": 100})
    assert sd.json()["case"]["status"] == "DECLINED"              # the RIA is view-only on the self-directed account
    other = ria.post("/api/money/transfers", json={"type": "ach_deposit", "account_id": "ACC-2002", "amount": 100})
    assert other.status_code == 404                               # Bonneville's client
    client = as_user("client.farid")
    out = client.post("/api/money/transfers", json={"type": "wire_out", "account_id": "ACC-2003", "amount": 500,
                                                    "counterparty": {"institution": "Wasatch Community Bank"}}).json()
    assert out["case"]["status"] == "DECLINED"                    # no grant from the RIA (release 2 entitlements)
    dep = client.post("/api/money/transfers", json={"type": "ach_deposit", "account_id": "ACC-2003", "amount": 500})
    assert dep.json()["case"]["status"] == "IN_TRANSIT"           # a deposit needs no grant
    ops = as_user("ops.transfers")
    assert ops.post("/api/money/transfers", json={"type": "ach_deposit", "account_id": "ACC-2001",
                                                  "amount": 100}).status_code == 422    # ops doesn't originate
    signed = ops.post("/api/money/transfers", json={"type": "wire_out", "account_id": "ACC-2001", "amount": 1_000,
                                                    "requester": "signed_form", "client_signed": True,
                                                    "counterparty": {"owner_name": "Maria Chen",
                                                                     "institution": "Wasatch Community Bank"}}).json()
    assert signed["case"]["status"] in ("PENDING_CALLBACK", "PENDING_RISK_REVIEW")      # signed requests get a callback
    r = as_user("ops.analyst").post("/api/money/transfers", json={"type": "wire_out", "account_id": "ACC-2001",
                                                                  "amount": 1, "requester": "signed_form",
                                                                  "client_signed": True})
    assert r.status_code == 403                                   # onboarding has no money movement at all
    r = as_user("ops.supervisor")
    refused = r.get("/api/audit?action=access_refused").json()
    assert any(e["actor"] == "Jamie Ortiz" and "/api/money" in str(e["target_id"]) for e in refused)


def test_codes_and_the_flagged_list_stop_money(as_user):
    ops = as_user("ops.supervisor")
    blocked = _transfer(ops, "ACH withdrawal: Hazel Montoya")
    assert _status(ops, blocked) == "BLOCKED"                     # CIPV: identity verification pending
    flagged = _transfer(ops, "Wire out: Whitfield")
    assert _status(ops, flagged) == "PENDING_SANCTIONS"
    assert flagged in [c["id"] for c in ops.get("/api/cases?queue=sanctions_review").json()]
    portal = as_user("bonneville.advisor").get(f"/api/cases/{flagged}").json()
    assert portal["status"] == "IN_REVIEW" and "anction" not in str(portal)


def test_a_journal_between_the_same_household_books_at_once(as_user):
    ria = as_user("granite.advisor")
    r = ria.post("/api/money/transfers", json={"type": "journal", "account_id": "ACC-3001", "destination": "ACC-3009",
                                               "amount": 1_000, "idempotency_key": "JNL-TEST-1"}).json()
    assert r["created"] and r["case"]["status"] == "COMPLETED"
    again = ria.post("/api/money/transfers", json={"type": "journal", "account_id": "ACC-3001",
                                                   "destination": "ACC-3009", "amount": 1_000,
                                                   "idempotency_key": "JNL-TEST-1"}).json()
    assert not again["created"] and again["case"]["id"] == r["case"]["id"]       # API-2: a retry is the same request


def test_the_portal_sees_its_balances_but_not_the_general_ledger(as_user):
    ria = as_user("granite.advisor")
    acct = ria.get("/api/money/accounts/ACC-3001").json()
    assert acct["positions"] and acct["entries"]
    assert all("layer" not in leg for e in acct["entries"] for leg in e["legs"])
    assert ria.get("/api/money/accounts/ACC-2002").status_code == 404
    for path in ("/api/settlement", "/api/recon", "/api/ledger/entries", "/api/money/overview"):
        assert ria.get(path).status_code == 403, path
    rows = ria.get("/api/money/transfers").json()
    assert rows and all(r["account"].startswith("ACC-") for r in rows) and "queue" not in rows[0]


# --- the rail and the settlement cycle --------------------------------------------------------------------------

def test_the_rail_settles_an_acats_and_the_cost_basis_follows(as_user):
    ops = as_user("ops.transfers")
    cid = next(c["id"] for c in ops.get("/api/money/transfers?open_only=true").json()
               if c["type"] == "acats_in" and c["status"] == "AWAITING_SETTLEMENT")
    r = ops.post(f"/api/cases/{cid}/actions/rail", json={"data": {"outcome": "confirm"}}).json()
    assert r["case"]["status"] == "COMPLETED"
    kinds = [e["kind"] for e in r["case"]["ledger_entries"]]
    assert "acats_in_pending" in kinds and "acats_in_settled" in kinds
    assert "basis" in [a["key"] for a in r["case"]["actions"]]
    _proof_ok(as_user("ops.settlement"))


def test_a_rejected_acats_is_corrected_and_resubmitted(as_user):
    ops = as_user("ops.transfers")
    cid = next(c["id"] for c in ops.get("/api/money/transfers").json() if c["status"] == "PENDING_CORRECTION")
    detail = ops.get(f"/api/cases/{cid}").json()
    assert "title" in detail["summary"]["Exception"].lower() and detail["summary"]["Drafted correction"]
    r = ops.post(f"/api/cases/{cid}/actions/correction", json={"data": {"note": "Resubmitted with the prior title"}})
    assert r.json()["case"]["status"] == "AWAITING_SETTLEMENT"
    _proof_ok(as_user("ops.settlement"))


def test_unapplied_cash_is_applied_by_the_settlement_team_only(as_user):
    view = as_user("ops.settlement").get("/api/settlement").json()
    waiting = next(r for r in view["receipts"] if r["status"] == "unapplied" and r["candidates"])
    assert waiting["candidates"][0]["score"] < 0.9                # a typo in the memo: a person decides
    assert as_user("ops.transfers").post(f"/api/settlement/receipts/{waiting['id']}/apply",
                                         json={"account_id": waiting["candidates"][0]["account"]}).status_code == 403
    s = as_user("ops.settlement")
    after = s.post(f"/api/settlement/receipts/{waiting['id']}/apply",
                   json={"account_id": waiting["candidates"][0]["account"],
                         "note": "Sender confirmed the account by phone"}).json()
    assert next(r for r in after["receipts"] if r["id"] == waiting["id"])["status"] == "applied"
    assert all(c["ok"] for c in after["checks"])
    stale = next(r for r in after["receipts"] if r["return_due"])
    assert s.post(f"/api/settlement/receipts/{stale['id']}/return", json={"note": "short"}).status_code == 422
    back = s.post(f"/api/settlement/receipts/{stale['id']}/return",
                  json={"note": "No account matches after 10 business days"}).json()
    assert next(r for r in back["receipts"] if r["id"] == stale["id"])["status"] == "returned"
    run = s.post("/api/settlement/run").json()["last_run"]
    assert [x["step"] for x in run["steps"]][3].startswith("Check the invariant") and run["checks"]


def test_breaks_resolve_and_then_the_books_certify(as_user):
    s = as_user("ops.settlement")
    view = s.get("/api/recon").json()
    open_ = [b for b in view["breaks"] if b["status"] != "resolved"]
    assert {b["pattern"] for b in open_} == {"unexplained", "ach_return_unbooked", "late_delivery"}
    assert all(b["status"] == "resolved" for b in view["breaks"] if b["pattern"] in ("dividend_reinvestment",
                                                                                       "bank_fee"))
    assert next(b for b in open_ if b["pattern"] == "unexplained" and "SFLX" in b["note"])["escalated"]
    control = s.get("/api/settlement").json()["protection"]["possession"]
    assert [d["name"] for d in control["deficits"]] and control["deficits"][0]["deficit"] == 40   # DTC's 40 CDVG short
    assert as_user("ops.trade").post("/api/recon/run").status_code == 403          # trade reads, settlement acts
    s = as_user("ops.settlement")
    for b in open_:
        body = {"resolution": "Approved the drafted resolution after checking the evidence"}
        if b["pattern"] == "unexplained":
            body = {"resolution": "DTC confirmed its own error and corrected the position"}
        r = s.post(f"/api/recon/breaks/{b['id']}/resolve", json=body)
        assert r.status_code == 200, r.text
    ret = next(b for b in open_ if b["pattern"] == "ach_return_unbooked")
    assert _status(as_user("ops.supervisor"), ret["case_id"]) == "RETURNED"
    s = as_user("ops.settlement")
    run = s.post("/api/recon/run").json()["last_run"]
    assert run["certifiable"] and run["certified_by"] == "Omar Haddad"
    assert all(loc["differences"] == 0 for loc in s.get("/api/recon").json()["locations"])
    events = as_user("ops.supervisor").get("/api/risk-events").json()["events"]
    assert any(e["source"] == "recon" and e["risk_type"] == "financial" for e in events)


# --- standing letters of authorization, fraud monitoring, and cutoffs (industry-practice review, Oct. 25, 2026) ------

BUILDER = {"owner_name": "Canyon Rim Builders (synthetic)", "owner_kind": "entity",
           "institution": "Sawtooth National Bank", "account_last4": "7710"}


def test_an_ria_pays_a_third_party_only_under_the_clients_sloa(as_user):
    acct = "ACC-3010"
    refused = as_user("granite.advisor").post("/api/money/transfers", json={
        "type": "wire_out", "account_id": acct, "amount": 5_000, "counterparty": BUILDER}).json()["case"]
    assert refused["status"] == "DECLINED"
    inside = as_user("ops.transfers").get(f"/api/cases/{refused['id']}").json()
    assert "standing letter of authorization" in inside["result"]["requested_items"][0]

    # The client signs an SLOA; the RIA sends it in; client service verifies it under maker-checker
    change = as_user("granite.advisor").post("/api/portal/changes", json={
        "account_id": acct, "change_type": "standing_loa",
        "details": {"payee": BUILDER["owner_name"], "payee_kind": "entity", "payee_address": "18 Quarry Rd, Draper, UT",
                    "institution": BUILDER["institution"], "account_last4": BUILDER["account_last4"],
                    "schedule": "Each construction draw"},
        "documents": [{"doc_type": "sloa", "sample": True}]}).json()
    assert change["status"] == "CONFIRMING"                     # the portal's words for PENDING_CONFIRMATION
    cid = change["id"]
    assert as_user("ops.service").get(f"/api/cases/{cid}").json()["status"] == "PENDING_CONFIRMATION"
    d = as_user("ops.service").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "confirmation", "value": "confirmed", "reference": "SLOA-T-1",
        "note": "Signature matches; client confirmed on a callback"}).json()
    assert as_user("ops.supervisor").post(f"/api/decisions/{d['id']}/approve").json()["status"] == "APPLIED"
    sloa = next(a for a in as_user("granite.advisor").get("/api/money/accounts").json() if a["id"] == acct)["sloas"][0]
    assert sloa["id"] == f"SLOA-{cid}" and sloa["payee"] == BUILDER["owner_name"]
    box = as_user("ops.supervisor").get("/api/outbox").json()["emails"]
    assert any(e["template"] == "sloa_initial" and e["case_id"] == cid for e in box)

    # Under the SLOA, the payee comes from the client's instruction, not from what the RIA typed
    wire = as_user("granite.advisor").post("/api/money/transfers", json={
        "type": "wire_out", "account_id": acct, "amount": 5_000, "sloa_id": sloa["id"],
        "counterparty": {**BUILDER, "account_last4": "0001"}}).json()["case"]
    detail = as_user("ops.transfers").get(f"/api/cases/{wire['id']}").json()
    assert detail["status"] == "PENDING_RELEASE" and detail["application"]["counterparty"]["account_last4"] == "7710"
    assert [x["doc_type"] for x in detail["related_documents"]["documents"]] == ["sloa"]    # the release links it
    assert _release(as_user, wire["id"], "REL-SLOA-1").status_code == 200
    done = as_user("ops.transfers").post(f"/api/cases/{wire['id']}/actions/rail",
                                         json={"data": {"outcome": "confirm"}}).json()
    assert done["case"]["status"] == "COMPLETED"
    box = as_user("ops.supervisor").get("/api/outbox").json()["emails"]
    assert any(e["template"] == "sloa_transfer" and e["case_id"] == wire["id"] for e in box)


def test_each_sloa_is_reconfirmed_every_year(as_user):
    from datetime import date, timedelta
    from app.db import SessionLocal
    from app.orm import Account
    with SessionLocal() as db:
        a = next(x for x in db.query(Account).all() if (x.application or {}).get("sloas"))
        sloas = [{**s, "annual_due": (date.today() - timedelta(days=1)).isoformat()} for s in a.application["sloas"]]
        a.application = {**a.application, "sloas": sloas}
        db.commit()
        account_id = a.id
    run = as_user("ops.settlement").post("/api/settlement/run").json()["last_run"]
    step = next(x for x in run["steps"] if x["step"].startswith("Reconfirm standing letters"))
    assert step["outcome"] != "None due" and account_id in step["detail"][0]
    again = as_user("ops.settlement").post("/api/settlement/run").json()["last_run"]
    assert next(x for x in again["steps"] if x["step"].startswith("Reconfirm"))["outcome"] == "None due"


def test_a_first_large_wire_to_a_destination_not_on_file_goes_to_a_risk_review(as_user):
    r = as_user("granite.advisor").post("/api/money/transfers", json={
        "type": "wire_out", "account_id": "ACC-3002", "amount": 15_000,
        "counterparty": {"owner_name": "Priscilla Ng", "institution": "Red Butte Credit Union",
                         "account_last4": "5150"}}).json()["case"]
    detail = as_user("ops.transfers").get(f"/api/cases/{r['id']}").json()
    assert detail["status"] == "PENDING_RISK_REVIEW"
    assert detail["application"]["screen_basis"]["known_destination"] is False
    prescreen = next(s for s in detail["result"]["steps"] if s["step"].startswith("Pre-screen"))
    assert any("First payment" in d for d in prescreen["detail"])
    portal = as_user("granite.advisor").get(f"/api/cases/{r['id']}").json()
    assert portal["status"] == "IN_REVIEW" and "First payment" not in str(portal)


def test_a_wire_released_after_the_cutoff_goes_out_the_next_business_day(as_user, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "clock", "17:30")
    r = as_user("granite.advisor").post("/api/money/transfers", json={
        "type": "wire_out", "account_id": "ACC-3022", "amount": 1_500,
        "counterparty": {"owner_name": "Fiona Gallardo", "institution": "Wasatch Community Bank",
                         "account_last4": "3030"}}).json()["case"]
    inside = as_user("ops.transfers").get(f"/api/cases/{r['id']}").json()
    assert inside["status"] == "PENDING_RELEASE", [(x["step"], x["outcome"], x["detail"]) for x in inside["result"]["steps"]]
    _release(as_user, r["id"], "REL-LATE-1")
    detail = as_user("ops.transfers").get(f"/api/cases/{r['id']}").json()
    assert detail["status"] == "AWAITING_SETTLEMENT", detail["status"]
    goes = detail["result"]["summary"]["Goes out"]
    assert goes > detail["result"]["summary"]["Submitted"]
    assert any(s["outcome"] == f"Goes out {goes}" for s in detail["result"]["steps"])


def test_a_temporary_hold_under_rule_2165_stops_a_disbursement(as_user):
    from app.db import SessionLocal
    from app.orm import Account
    with SessionLocal() as db:                      # make the account holder a specified adult (65 or older)
        a = db.get(Account, "ACC-3006")
        a.application = {**a.application, "holders": [{**h, "date_of_birth": "1950-04-02"} for h in a.application["holders"]],
                         "trusted_contact": {"name": "Ada Adeyemi", "relationship": "daughter",
                                             "email": "ada.adeyemi@example.com"}}
        db.commit()
    wire = as_user("granite.advisor").post("/api/money/transfers", json={
        "type": "wire_out", "account_id": "ACC-3006", "amount": 3_000,
        "counterparty": {"owner_name": "Samuel Adeyemi", "institution": "Wasatch Community Bank",
                         "account_last4": "4471"}}).json()["case"]
    cid = wire["id"]
    short = as_user("ops.transfers").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "exploitation_hold", "value": "placed", "reference": "HOLD-T-1", "note": "new"})
    assert short.status_code == 422                                  # the reason starts the internal review
    d = as_user("ops.transfers").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "exploitation_hold", "value": "placed", "reference": "HOLD-T-1",
        "note": "Caller pressing the client to wire savings to a new 'advisor'"}).json()
    assert as_user("ops.money").post(f"/api/decisions/{d['id']}/approve").status_code == 403     # compliance approves
    held = as_user("aml.checker").post(f"/api/decisions/{d['id']}/approve").json()
    assert held["status"] == "ON_HOLD" and held["queue"] == "exploitation_holds"
    assert held["result"]["summary"]["On hold until"]
    box = {e["template"] for e in as_user("ops.supervisor").get("/api/outbox").json()["emails"] if e["case_id"] == cid}
    assert {"transfer_on_hold", "hold_client", "hold_trusted_contact"} <= box
    portal = as_user("granite.advisor").get(f"/api/cases/{cid}").json()
    assert portal["status"] == "ON_HOLD" and "exploit" not in str(portal).lower()
    r = as_user("aml.maker").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "hold_review", "value": "lifted", "reference": "HOLD-T-2",
        "note": "Spoke with the client and the trusted contact; the payee is the client's own bank"}).json()
    lifted = as_user("aml.checker").post(f"/api/decisions/{r['id']}/approve").json()
    assert lifted["status"] == "PENDING_RELEASE"


# --- customer protection and the nightly batch (industry-practice review, Oct. 25, 2026) ---------------------------

def test_the_reserve_formula_flags_a_deficit_and_a_deposit_covers_it(as_user):
    view = as_user("ops.settlement").get("/api/settlement").json()
    r = view["protection"]["reserve"]
    assert r["required"] > 0 and r["excess"] > 0 and r["deficit"] == 0           # the seeded opening deposit
    s = as_user("ops.settlement")                                     # a large wire arrives and is applied
    receipt = s.post("/api/settlement/receipts", json={"channel": "Fedwire", "amount": 2_000_000,
                                                       "sender": "Bear River Savings Bank",
                                                       "memo": "FBO ACC-3006 Samuel Adeyemi: inheritance"}).json()
    rid = next(x["id"] for x in receipt["receipts"] if x["amount"] == 2_000_000)
    as_user("ops.settlement").post(f"/api/settlement/receipts/{rid}/apply",
                                   json={"account_id": "ACC-3006", "note": "Memo names the account and the client"})
    run = as_user("ops.settlement").post("/api/settlement/run").json()
    short = run["protection"]["reserve"]
    assert short["deficit"] > 0 and short["deposit_due"].endswith("10:00 ET")
    step = next(x for x in run["last_run"]["steps"] if "15c3-3" in x["step"])
    assert step["outcome"].startswith("Reserve deficit") and "CP-001" in step["rule_ids"]
    events = as_user("ops.supervisor").get("/api/risk-events").json()["events"]
    assert any(e["source"] == "protection" and e["risk_type"] == "financial" and "Reserve" in e["category"] for e in events)
    assert as_user("ops.transfers").post("/api/settlement/reserve/deposit",
                                         json={"amount": 1, "note": "not my team's job"}).status_code == 403
    covered = as_user("ops.settlement").post("/api/settlement/reserve/deposit", json={
        "amount": round(short["deficit"] + 10_000, -3), "note": "Covers today's reserve deficit"}).json()
    assert covered["protection"]["reserve"]["deficit"] == 0
    assert all(c["ok"] for c in covered["checks"])                     # moving cash between banks keeps the layers


def test_the_nightly_batch_runs_by_token_or_by_the_supervisor(as_user, monkeypatch):
    from app.config import settings
    c = as_user("ops.analyst")
    assert c.post("/api/nightly/run").status_code == 403                # not the supervisor (refused and logged)
    c.cookies.clear()
    assert c.post("/api/nightly/run").status_code == 401                # nobody signed in, no token
    assert c.post("/api/nightly/run", headers={"X-Nightly-Token": "guess"}).status_code == 403   # none configured
    monkeypatch.setattr(settings, "nightly_token", "test-token-0123456789")
    assert c.post("/api/nightly/run", headers={"X-Nightly-Token": "wrong"}).status_code == 403
    out = c.post("/api/nightly/run", headers={"X-Nightly-Token": "test-token-0123456789"}).json()
    assert out["by"] == "Scheduler" and out["screening"]["parties"] > 40 and "reserve_deficit" in out["settlement"]
    by_hand = as_user("ops.supervisor").post("/api/nightly/run").json()
    assert by_hand["by"] == "Priya Raman"
    recent = as_user("ops.supervisor").get("/api/nightly").json()
    assert recent["scheduled"] and len(recent["runs"]) == 2 and recent["can_run"]
