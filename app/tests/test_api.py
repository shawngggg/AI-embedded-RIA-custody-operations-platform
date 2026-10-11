"""API tests for the MVP: sign-in, scoping, queues, maker-checker, portal, codes, reviews, admin, intake."""
from app.tests.conftest import case_id

# --- Sign-in and scoping ---------------------------------------------------


def test_demo_users_and_sign_in(client):
    users = client.get("/api/auth/demo-users").json()["users"]
    assert {"ops.analyst", "sanctions.checker", "granite.advisor", "client.farid"} <= {u["username"] for u in users}
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/session").json() == {"user": None}
    client.post("/api/auth/demo", json={"username": "granite.advisor"})
    me = client.get("/api/auth/me").json()
    assert me["role"] == "ria_user" and me["firm"]["crd"] == "310001" and me["workspace"] == "portal"
    assert client.get("/api/auth/session").json()["user"]["username"] == "granite.advisor"


def test_seeded_cases_are_aged(as_user):
    """Seeded cases arrive at different times, so SLA clocks and the dashboard have something to show."""
    c = as_user("ops.analyst")
    cases = {x["title"]: x for x in c.get("/api/cases").json()}
    rosa = next(v for k, v in cases.items() if "Rosa Delgado" in k)
    meridian = next(v for k, v in cases.items() if "Meridian Gate" in k)
    assert rosa["sla"]["breached"] and not meridian["sla"]["breached"]
    hours = as_user("admin.platform").get("/api/dashboard").json()["hours_in_state"]
    assert hours["PENDING_EDD"]["average"] > 24


def test_queues_have_seeded_work(as_user):
    c = as_user("ops.analyst")
    counts = {q["key"]: q["count"] for q in c.get("/api/queues").json()["queues"]}
    assert counts["sanctions_review"] == 2 and counts["edd_review"] == 2
    assert counts["items_requested"] == 2 and counts["confirmation"] == 1 and counts["reviews"] >= 2


def test_portal_users_cannot_reach_the_workbench(as_user):
    c = as_user("granite.advisor")
    assert c.get("/api/queues").status_code == 403
    assert c.get("/api/dashboard").status_code == 403


def test_ria_user_sees_only_their_firms_accounts_and_changes(as_user):
    c = as_user("granite.advisor")
    titles = [x["title"] for x in c.get("/api/cases").json()]
    assert any("Maria Chen" in t for t in titles)
    assert not any("Kenji" in t for t in titles)            # Bonneville's client
    assert not any("RIA firm onboarding" in t for t in titles)
    firm_case = case_id(as_user("ops.analyst"), "Meridian Gate")
    assert as_user("granite.advisor").get(f"/api/cases/{firm_case}").status_code == 404


def test_client_sees_only_changes_on_their_account(as_user):
    c = as_user("client.farid")
    assert c.get("/api/cases").json() == []
    accounts = c.get("/api/portal/accounts").json()
    assert [a["id"] for a in accounts] == ["ACC-2003"]


# --- Maker-checker decisions ------------------------------------------------


def test_sanctions_false_positive_needs_two_people(as_user):
    cid = case_id(as_user("ops.analyst"), "Ilya Sorvetkin")
    ops = as_user("ops.analyst")
    r = ops.post(f"/api/cases/{cid}/decisions", json={"decision_type": "sanctions", "value": "false_positive",
                                                     "reference": "DET-1"})
    assert r.status_code == 403
    maker = as_user("sanctions.maker")
    d = maker.post(f"/api/cases/{cid}/decisions", json={"decision_type": "sanctions", "value": "false_positive",
                                                       "reference": "DET-2026-0412"}).json()
    assert d["status"] == "pending"
    assert maker.post(f"/api/decisions/{d['id']}/approve").status_code == 403    # maker can't be checker
    detail = as_user("sanctions.checker").post(f"/api/decisions/{d['id']}/approve").json()
    assert detail["status"] == "OPEN"
    sanc = [r for r in detail["restrictions"] if r["code"] == "SANC"][0]
    assert not sanc["active"] and sanc["authorization"] == "DET-2026-0412"


def test_wrong_role_attempts_are_refused_and_logged(as_user):
    cid = case_id(as_user("ops.analyst"), "Ilya Sorvetkin")
    as_user("aml.maker").post(f"/api/cases/{cid}/decisions", json={"decision_type": "sanctions",
                                                                  "value": "true_match", "reference": "X-1"})
    log = as_user("ops.analyst").get("/api/audit?action=decision_refused").json()
    assert log and log[0]["detail"]["reason"] == "wrong role"


def test_edd_approval_activates_the_firm(as_user):
    cid = case_id(as_user("ops.analyst"), "Lakeshore")
    d = as_user("aml.maker").post(f"/api/cases/{cid}/decisions", json={"decision_type": "edd", "value": "approved",
                                                                      "reference": "EDD-2026-0102"}).json()
    detail = as_user("aml.checker").post(f"/api/decisions/{d['id']}/approve").json()
    assert detail["status"] == "ACTIVE"
    assert not [r for r in detail["restrictions"] if r["active"]]


def test_true_match_declines_and_blocks(as_user):
    cid = case_id(as_user("ops.analyst"), "Meridian Gate")
    d = as_user("sanctions.maker").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "sanctions", "value": "true_match", "reference": "DET-2026-0101"}).json()
    detail = as_user("sanctions.checker").post(f"/api/decisions/{d['id']}/approve").json()
    assert detail["status"] == "DECLINED"
    assert [r["code"] for r in detail["restrictions"] if r["active"]] == ["OFAC"]


def test_rejected_proposal_changes_nothing(as_user):
    cid = case_id(as_user("ops.analyst"), "Elena Marsh-Duval")
    d = as_user("aml.maker").post(f"/api/cases/{cid}/decisions", json={"decision_type": "edd", "value": "approved",
                                                                      "reference": "EDD-9"}).json()
    detail = as_user("aml.checker").post(f"/api/decisions/{d['id']}/reject").json()
    assert detail["status"] == "PENDING_EDD"


# --- Missing items ----------------------------------------------------------


def test_items_received_reruns_the_case(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Alta Vista")
    detail = ops.get(f"/api/cases/{cid}").json()
    assert {c["field"] for c in detail["corrections"]} == {"ownership_chart", "beneficial_ownership_certification",
                                                          "principal_address"}
    detail = ops.post(f"/api/cases/{cid}/corrections", json={"fields": {
        "ownership_chart": True, "beneficial_ownership_certification": True,
        "principal_address": "1400 Kearns Blvd, Park City, UT 84060"}}).json()
    assert detail["status"] == "ACTIVE" and detail["manual_steps"] == 1 and detail["nigo_count"] == 1


def _upload_sample(ops):
    text = ops.get("/api/intake/status").json()["sample_text"]
    return ops.post("/api/intake/extract", data={"text": text}).json()


def test_ai_read_firm_completes_after_identity_verification(as_user):
    ops = as_user("ops.analyst")
    cid = _upload_sample(ops)["id"]
    detail = ops.post(f"/api/cases/{cid}/corrections", json={
        "fields": {"beneficial_ownership_certification": True}, "verify_people": True}).json()
    assert detail["status"] == "ACTIVE"
    assert detail["result"]["reliance"]["eligible"] is False


def test_rias_cannot_record_identity_verification(as_user):
    cid = _upload_sample(as_user("ops.analyst"))["id"]
    r = as_user("granite.advisor").post(f"/api/cases/{cid}/corrections", json={"verify_people": True})
    assert r.status_code == 404      # not visible to the RIA at all


# --- Account changes ---------------------------------------------------------


def test_fraud_pattern_confirmation_applies_the_bank_change(as_user):
    cid = case_id(as_user("ops.analyst"), "bank instruction on Maria Chen")
    d = as_user("ops.analyst").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "confirmation", "value": "confirmed", "reference": "CALLBACK-0007"}).json()
    detail = as_user("ops.supervisor").post(f"/api/decisions/{d['id']}/approve").json()
    assert detail["status"] == "APPLIED"
    account = [a for a in as_user("granite.advisor").get("/api/portal/accounts").json() if a["id"] == "ACC-2001"][0]
    assert account["bank_instructions"][-1]["status"] == "inactive_until_verified"


def test_client_changes_their_self_directed_account(as_user):
    c = as_user("client.farid")
    detail = c.post("/api/portal/changes", json={"account_id": "ACC-2003", "change_type": "address",
                                                 "details": {"new_address": "12 Pine St, Ogden, UT 84401"}}).json()
    assert detail["status"] == "APPLIED"
    assert c.get("/api/portal/accounts").json()[0]["address"] == "12 Pine St, Ogden, UT 84401"


def test_ria_change_on_a_self_directed_account_is_redirected(as_user):
    detail = as_user("granite.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-2003", "change_type": "address", "details": {"new_address": "x"}}).json()
    assert detail["status"] == "REDIRECTED"


def test_owner_level_change_returns_for_signature_then_applies(as_user):
    c = as_user("granite.advisor")
    detail = c.post("/api/portal/changes", json={"account_id": "ACC-2001", "change_type": "beneficiary",
                                                 "details": {"beneficiaries": "Lena Chen, 100%"}}).json()
    assert detail["status"] == "RETURNED" and detail["corrections"][0]["field"] == "client_signature"
    detail = c.post(f"/api/cases/{detail['id']}/corrections", json={"fields": {"client_signature": True}}).json()
    assert detail["status"] == "APPLIED"


def test_other_firms_cannot_change_an_account(as_user):
    r = as_user("bonneville.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-2001", "change_type": "address", "details": {}})
    assert r.status_code == 404


# --- Portal account applications -------------------------------------------

NEW_APP = {"group": "pure_ria", "registration_type": "individual",
           "holders": [{"name": "Nina Park", "role": "account_holder", "date_of_birth": "1987-04-02",
                        "address": "300 S State St, Salt Lake City, UT 84111", "tax_id": "900-00-3001"}],
           "lpoa_signed": True, "fee_authorization": True, "client_signature": True, "tax_form": "W-9",
           "account_purpose": "Long-term investing", "source_of_funds": "Salary", "source_of_wealth": "Salary"}


def test_ria_opens_an_account_on_path_a(as_user):
    detail = as_user("granite.advisor").post("/api/portal/accounts", json={"application": NEW_APP}).json()
    assert detail["status"] == "OPEN" and detail["path"] == "A"


def test_incomplete_application_comes_back_for_items(as_user):
    app = {**NEW_APP, "lpoa_signed": False, "tax_form": None}
    detail = as_user("bonneville.advisor").post("/api/portal/accounts", json={"application": app}).json()
    assert detail["status"] == "PENDING_ITEMS"
    assert {c["field"] for c in detail["corrections"]} == {"lpoa_signed", "tax_form"}


# --- Restriction codes --------------------------------------------------------


def test_only_the_owner_removes_a_manual_code(as_user):
    ops = as_user("ops.analyst")
    r = ops.post("/api/restrictions", json={"target_id": "ACC-2001", "code": "CIPV",
                                            "reason": "Re-verify identity after a returned mail"}).json()
    refused = as_user("sanctions.maker").post(f"/api/restrictions/{r['id']}/remove", json={"reason": "not needed"})
    assert refused.status_code == 403
    done = as_user("ops.analyst").post(f"/api/restrictions/{r['id']}/remove", json={"reason": "Verified"}).json()
    assert done["active"] is False


def test_case_codes_are_released_through_the_case(as_user):
    codes = as_user("sanctions.checker").get("/api/restrictions").json()
    sanc = [c for c in codes if c["code"] == "SANC"][0]
    r = as_user("sanctions.checker").post(f"/api/restrictions/{sanc['id']}/remove",
                                          json={"reason": "Released", "authorization": "DET-1"})
    assert r.status_code == 409


# --- Rolling review -----------------------------------------------------------


def test_missed_refresh_restricts_until_the_review_completes(as_user):
    rv = as_user("review.analyst")
    assert rv.post("/api/reviews/enforce-deadlines").json()["restricted"] == ["ACC-2004"]
    codes = [c["code"] for c in rv.get("/api/restrictions?target=ACC-2004").json()]
    assert codes == ["KYCR"]
    review = [r for r in rv.get("/api/reviews").json() if r["subject_id"] == "ACC-2004"][0]
    done = rv.post(f"/api/reviews/{review['id']}/complete", json={"outcome": "no_change", "new_tier": "LOW"}).json()
    assert done["status"] == "complete" and done["next_review"] == "2027-10-10"
    assert rv.get("/api/restrictions?target=ACC-2004").json() == []


# --- Administration -------------------------------------------------------------


def test_decision_role_grants_need_a_second_administrator(as_user):
    admin = as_user("admin.platform")
    out = admin.post("/api/admin/users", json={"username": "n.sato", "display_name": "Naomi Sato",
                                               "role": "sanctions", "password": "correct-horse-1"}).json()
    assert out["user"]["active"] is False and out["grant"]["status"] == "pending"
    assert admin.post(f"/api/admin/grants/{out['grant']['id']}/approve").status_code == 403
    as_user("admin.second").post(f"/api/admin/grants/{out['grant']['id']}/approve")
    login = as_user("admin.second").post("/api/auth/login", json={"username": "n.sato", "password": "correct-horse-1"})
    assert login.status_code == 200 and login.json()["role"] == "sanctions"


def test_ria_admin_manages_only_their_firms_users(as_user):
    ria = as_user("granite.admin")
    ok = ria.post("/api/admin/users", json={"username": "g.analyst", "display_name": "Gwen Hart",
                                            "role": "ria_user", "password": "long-password-1"})
    assert ok.status_code == 200 and ok.json()["user"]["firm"]["crd"] == "310001"
    refused = ria.post("/api/admin/users", json={"username": "g.ops", "display_name": "Gil Ops",
                                                 "role": "ops_analyst", "password": "long-password-1"})
    assert refused.status_code == 403
    assert {u["firm"]["crd"] for u in ria.get("/api/admin/users").json()} == {"310001"}


def test_rule_library_lists_every_rule_set(as_user):
    lib = as_user("admin.platform").get("/api/admin/rules").json()
    assert {"Firm rules", "Account rules", "Account change rules", "FATF lists"} <= set(lib)
    assert all(r["source"] for r in lib["Firm rules"])


# --- AI intake ------------------------------------------------------------------


def test_sample_package_runs_through_the_gate_without_live_ai(as_user):
    ops = as_user("ops.analyst")
    status = ops.get("/api/intake/status").json()
    assert status["live"] is False and status["cap"] == 50
    # Browsers send textarea text with CRLF line breaks; the sample still matches
    crlf = status["sample_text"].replace("\n", "\r\n")
    detail = ops.post("/api/intake/extract", data={"text": crlf}).json()
    assert detail["extra"]["extraction"]["mode"] == "illustrative" and detail["status"] == "PENDING_ITEMS"
    other = ops.post("/api/intake/extract", data={"text": "Some other firm's package"})
    assert other.status_code == 503
    again = ops.post("/api/intake/extract", data={"text": status["sample_text"]})
    assert again.status_code == 409


def test_firm_by_form(as_user):
    app = {"legal_name": "Cache Valley Advisors LLC", "registration": "SEC", "sec_file_number": "801-120020",
           "crd_number": "310020", "raum": 300_000_000, "adv_last_annual_amendment": "2026-03-10",
           "principal_address": "90 N Main St, Logan, UT 84321", "ein": "87-1234520", "formation_documents": True,
           "ownership_chart": True, "beneficial_ownership_certification": True, "custodial_agreement_signed": True,
           "people": [{"name": "Lee Carter", "role": "control_person", "ownership_pct": 100,
                       "identity_verified": True}]}
    detail = as_user("ops.analyst").post("/api/intake/firm", json={"application": app}).json()
    assert detail["status"] == "ACTIVE"


# --- Dashboard, audit, reset --------------------------------------------------------


def test_dashboard_reports_rates_and_queues(as_user):
    d = as_user("ops.supervisor").get("/api/dashboard").json()
    assert d["straight_through"]["opened"] >= 8 and 0 <= d["straight_through"]["rate"] <= 1
    assert d["nigo"]["with_nigo"] >= 2 and {q["key"] for q in d["queues"]} >= {"sanctions_review", "edd_review"}


def test_audit_log_records_runs_and_exports(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Ilya Sorvetkin")
    events = ops.get(f"/api/cases/{cid}/audit").json()
    assert [e["action"] for e in events][:3] == ["case_created", "case_run", "restriction_placed"]
    export = ops.get("/api/audit/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]


def test_demo_reset(as_user):
    admin = as_user("admin.platform")
    admin.post("/api/admin/users", json={"username": "temp.user", "display_name": "Temp", "role": "ops_analyst",
                                         "password": "long-password-1"})
    assert admin.post("/api/admin/reset").json() == {"ok": True}
    admin = as_user("admin.platform")
    assert "temp.user" not in {u["username"] for u in admin.get("/api/admin/users").json()}
