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


def _release(maker, checker, cid, ref):
    d = maker.post(f"/api/cases/{cid}/decisions", json={"decision_type": "release", "value": "released",
                                                         "reference": ref}).json()
    assert d["status"] == "pending", d
    return checker.post(f"/api/decisions/{d['id']}/approve")


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
    r = as_user("ops.transfers").post(f"/api/cases/{cid}/actions/callback",
                                      json={"data": {"confirmed": "yes", "phone": ""}})
    assert r.status_code == 422                                   # say which number on file was called
    r = as_user("ops.transfers").post(f"/api/cases/{cid}/actions/callback",
                                      json={"data": {"confirmed": "yes", "phone": "801-555-0110"}})
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
    assert next(b for b in open_ if b["pattern"] == "unexplained")["escalated"]
    assert as_user("ops.trade").post("/api/recon/run").status_code == 403          # trade reads, settlement acts
    s = as_user("ops.settlement")
    for b in open_:
        body = {"resolution": "Approved the drafted resolution after checking the evidence"}
        if b["pattern"] == "unexplained":
            body = {"resolution": "DTC confirmed its own error and reversed the 12 shares"}
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
