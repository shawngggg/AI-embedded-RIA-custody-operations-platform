"""API tests for the MVP: sign-in, scoping, queues, maker-checker, principal acceptance, documents, portal,
codes, reviews, admin, intake."""
import hashlib

from app.tests.conftest import case_id

ACCOUNT_DOCS = [{"doc_type": t, "sample": True} for t in ("account_application", "lpoa", "fee_authorization", "w9")]


def upload_sample(client, cid: int, doc_type: str) -> dict:
    r = client.post(f"/api/cases/{cid}/documents", data={"doc_type": doc_type, "sample": "true", "channel": "email"})
    assert r.status_code == 200, r.text
    return r.json()


def accept(client, cid: int) -> dict:
    r = client.post(f"/api/cases/{cid}/acceptance", json={"value": "accepted"})
    assert r.status_code == 200, r.text
    return r.json()

# --- Sign-in and scoping ---------------------------------------------------


def test_demo_users_and_sign_in(client):
    users = client.get("/api/auth/demo-users").json()["users"]
    assert {"ops.analyst", "sanctions.checker", "granite.advisor", "client.farid", "rs.principal"} <= {
        u["username"] for u in users}
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
    assert counts["principal_acceptance"] == 2           # Juniper Ridge and Lena Park


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
    assert detail["status"] == "PENDING_ACCEPTANCE"
    sanc = [r for r in detail["restrictions"] if r["code"] == "SANC"][0]
    assert not sanc["active"] and sanc["authorization"] == "DET-2026-0412"
    assert accept(as_user("rs.principal"), cid)["status"] == "OPEN"


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
    assert detail["status"] == "PENDING_ACCEPTANCE"
    assert not [r for r in detail["restrictions"] if r["active"]]
    assert accept(as_user("rs.principal"), cid)["status"] == "ACTIVE"


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


# --- Principal acceptance (FINRA Rule 4512) -----------------------------------


def test_only_a_registered_principal_accepts_an_account(as_user):
    cid = case_id(as_user("ops.analyst"), "Lena Park")
    assert as_user("ops.supervisor").post(f"/api/cases/{cid}/acceptance", json={"value": "accepted"}).status_code == 403
    associate = as_user("rs.associate")
    assert associate.get(f"/api/cases/{cid}").json()["acceptance"]["can_accept"] is False
    r = associate.post(f"/api/cases/{cid}/acceptance", json={"value": "accepted"})
    assert r.status_code == 403 and "Series 24" in r.json()["detail"]
    principal = as_user("rs.principal")
    assert principal.get(f"/api/cases/{cid}").json()["acceptance"]["can_accept"] is True
    detail = accept(principal, cid)
    assert detail["status"] == "OPEN"
    entry = [d for d in detail["decision_log"] if d["decision_type"] == "acceptance"][0]
    assert entry["reference"] == "Avery Collins (Series 24, CRD 7700101)"
    log = as_user("ops.analyst").get("/api/audit?action=acceptance_refused").json()
    assert log and log[0]["actor"] == "Noah Brennan"


def test_principal_declines_a_firm(as_user):
    cid = case_id(as_user("ops.analyst"), "Juniper Ridge")
    detail = as_user("rs.principal").post(f"/api/cases/{cid}/acceptance", json={"value": "declined",
                                                                               "note": "Outside our appetite"}).json()
    assert detail["status"] == "DECLINED"


def test_acceptance_isnt_a_maker_checker_proposal(as_user):
    cid = case_id(as_user("ops.analyst"), "Lena Park")
    r = as_user("rs.principal").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "acceptance", "value": "accepted", "reference": "X-1"})
    assert r.status_code == 422


# --- Documents and missing items ------------------------------------------------


def test_items_received_need_the_document_and_operations(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Rosa Delgado")
    corrections = {c["field"]: c for c in ops.get(f"/api/cases/{cid}").json()["corrections"]}
    assert corrections["lpoa_signed"]["type"] == "document" and corrections["tax_form"]["type"] == "document"
    r = ops.post(f"/api/cases/{cid}/corrections", json={"fields": {"lpoa_signed": True}})
    assert r.status_code == 422 and "Attach the Limited power of attorney" in r.json()["detail"]
    lpoa = upload_sample(as_user("bonneville.advisor"), cid, "lpoa")     # the RIA sends it through the portal
    assert lpoa["channel"] == "portal" and lpoa["status"] == "received" and lpoa["checks_passed"]
    refused = as_user("bonneville.advisor").post(f"/api/cases/{cid}/corrections", json={
        "fields": {"lpoa_signed": True}, "documents": {"lpoa_signed": lpoa["id"]}})
    assert refused.status_code == 403                                   # operations checks documents
    w9 = upload_sample(as_user("ops.analyst"), cid, "w9")
    wrong = as_user("ops.analyst").post(f"/api/cases/{cid}/corrections", json={
        "fields": {"lpoa_signed": True}, "documents": {"lpoa_signed": w9["id"]}})
    assert wrong.status_code == 422
    detail = as_user("ops.analyst").post(f"/api/cases/{cid}/corrections", json={
        "fields": {"lpoa_signed": True, "tax_form": "W-9"},
        "documents": {"lpoa_signed": lpoa["id"], "tax_form": w9["id"]}}).json()
    assert detail["status"] == "PENDING_ACCEPTANCE" and detail["manual_steps"] == 1
    docs = {d["doc_type"]: d for d in detail["documents"]}
    assert docs["lpoa"]["status"] == "in_good_order" and docs["lpoa"]["reviewed_by"] == "Jamie Ortiz"
    event = as_user("ops.analyst").get("/api/audit?action=items_received").json()[0]
    assert {d["sha256"] for d in event["detail"]["documents"]} == {lpoa["sha256"], w9["sha256"]}


def test_a_document_marked_not_in_good_order_cant_back_an_item(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Rosa Delgado")
    lpoa = upload_sample(ops, cid, "lpoa")
    assert as_user("bonneville.advisor").post(f"/api/documents/{lpoa['id']}/reject",
                                              json={"reason": "Unsigned page 2"}).status_code == 403
    ops = as_user("ops.analyst")
    assert ops.post(f"/api/documents/{lpoa['id']}/reject", json={"reason": "Unsigned page 2"}).json()[
        "status"] == "not_in_good_order"
    r = ops.post(f"/api/cases/{cid}/corrections", json={"fields": {"lpoa_signed": True},
                                                      "documents": {"lpoa_signed": lpoa["id"]}})
    assert r.status_code == 422


def test_a_failed_check_needs_an_explanation_to_accept(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Rosa Delgado")
    w8 = upload_sample(ops, cid, "w8ben")                 # Rosa is a US person: she files a W-9
    assert [c for c in w8["checks"] if c["check"] == "Right form for the client"][0]["result"] == "fail"
    r = ops.post(f"/api/cases/{cid}/corrections", json={"fields": {"tax_form": "W-8BEN"}, "documents": {"tax_form": w8["id"]}})
    assert r.status_code == 422 and "Right form for the client" in r.json()["detail"]
    ok = ops.post(f"/api/cases/{cid}/corrections", json={
        "fields": {"tax_form": "W-8BEN"}, "documents": {"tax_form": w8["id"]},
        "overrides": {"tax_form": "Client confirmed by phone she moved abroad; nationality update to follow"}})
    assert ok.status_code == 200
    doc = [d for d in ok.json()["documents"] if d["id"] == w8["id"]][0]
    assert doc["status"] == "in_good_order" and doc["review_note"].startswith("Client confirmed")


def test_documents_are_fingerprinted_scoped_and_checked(as_user):
    adv = as_user("bonneville.advisor")
    cid = case_id(as_user("ops.analyst"), "Rosa Delgado")
    adv = as_user("bonneville.advisor")
    doc = upload_sample(adv, cid, "w9")
    body = adv.get(f"/api/documents/{doc['id']}/file")
    assert body.headers["content-type"] == "application/pdf"
    assert hashlib.sha256(body.content).hexdigest() == doc["sha256"] == body.headers["x-content-sha256"]
    assert as_user("granite.advisor").get(f"/api/documents/{doc['id']}/file").status_code == 404
    adv = as_user("bonneville.advisor")
    big = adv.post(f"/api/cases/{cid}/documents", data={"doc_type": "w9"},
                   files={"file": ("big.pdf", b"%PDF" + b"0" * (5 * 1024 * 1024 + 1), "application/pdf")})
    assert big.status_code == 413
    text = adv.post(f"/api/cases/{cid}/documents", data={"doc_type": "w9"},
                    files={"file": ("w9.txt", b"not a pdf", "text/plain")})
    assert text.status_code == 415
    scan = adv.post(f"/api/cases/{cid}/documents", data={"doc_type": "w9"},
                    files={"file": ("w9.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")}).json()
    assert scan["checks"][0]["result"] == "manual" and not scan["checks_passed"]


def _upload_sample(ops):
    text = ops.get("/api/intake/status").json()["sample_text"]
    return ops.post("/api/intake/extract", data={"text": text}).json()


def test_ai_read_firm_completes_after_identity_verification(as_user):
    ops = as_user("ops.analyst")
    detail = _upload_sample(ops)
    cid = detail["id"]
    assert detail["documents"][0]["doc_type"] == "application_package"
    bo = upload_sample(ops, cid, "bo_certification")
    ids = upload_sample(ops, cid, "identity_documents")
    detail = ops.post(f"/api/cases/{cid}/corrections", json={
        "fields": {"beneficial_ownership_certification": True}, "verify_people": True,
        "documents": {"beneficial_ownership_certification": bo["id"], "verify_people": ids["id"]}}).json()
    assert detail["status"] == "PENDING_ACCEPTANCE"
    detail = accept(as_user("rs.principal"), cid)
    assert detail["status"] == "ACTIVE" and detail["result"]["reliance"]["eligible"] is False


def test_rias_cannot_record_identity_verification(as_user):
    cid = _upload_sample(as_user("ops.analyst"))["id"]
    r = as_user("granite.advisor").post(f"/api/cases/{cid}/corrections", json={"verify_people": True})
    assert r.status_code == 404      # not visible to the RIA at all


def test_the_principal_cant_accept_a_case_they_prepared(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Rosa Delgado")
    lpoa, w9 = upload_sample(ops, cid, "lpoa"), upload_sample(ops, cid, "w9")
    ops.post(f"/api/cases/{cid}/corrections", json={"fields": {"lpoa_signed": True, "tax_form": "W-9"},
                                                   "documents": {"lpoa_signed": lpoa["id"], "tax_form": w9["id"]}})
    # Jamie later joins the RIA service team as a principal
    admin = as_user("admin.platform")
    jamie = [u for u in admin.get("/api/admin/users").json() if u["username"] == "ops.analyst"][0]
    role = admin.post(f"/api/admin/users/{jamie['id']}/role", json={"role": "ria_service"}).json()["grant"]
    as_user("admin.second").post(f"/api/admin/grants/{role['id']}/approve")
    regs = as_user("admin.platform").post(f"/api/admin/users/{jamie['id']}/registrations", json={
        "registrations": ["S7", "S24"], "individual_crd": "7700199"}).json()["grant"]
    as_user("admin.second").post(f"/api/admin/grants/{regs['id']}/approve")
    r = as_user("ops.analyst").post(f"/api/cases/{cid}/acceptance", json={"value": "accepted"})
    assert r.status_code == 403 and "prepared" in r.json()["detail"]
    assert accept(as_user("rs.principal"), cid)["status"] == "OPEN"


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
                                                 "client_signature": True,     # a checkbox alone doesn't count
                                                 "details": {"beneficiaries": "Lena Chen, 100%"}}).json()
    assert detail["status"] == "RETURNED" and detail["corrections"][0]["field"] == "client_signature"
    form = upload_sample(c, detail["id"], "change_form")
    ops = as_user("ops.analyst")
    detail = ops.post(f"/api/cases/{detail['id']}/corrections", json={
        "fields": {"client_signature": True}, "documents": {"client_signature": form["id"]}}).json()
    assert detail["status"] == "APPLIED"


def test_ria_change_with_the_signed_form_attached_applies(as_user):
    detail = as_user("granite.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-2001", "change_type": "beneficiary", "details": {"beneficiaries": "Lena Chen, 100%"},
        "documents": [{"doc_type": "change_form", "sample": True}]}).json()
    assert detail["status"] == "APPLIED"


def test_other_firms_cannot_change_an_account(as_user):
    r = as_user("bonneville.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-2001", "change_type": "address", "details": {}})
    assert r.status_code == 404


# --- Portal account applications -------------------------------------------

NEW_APP = {"group": "pure_ria", "registration_type": "individual",
           "holders": [{"name": "Nina Park", "role": "account_holder", "date_of_birth": "1987-04-02",
                        "address": "300 S State St, Salt Lake City, UT 84111", "tax_id": "900-00-3001"}],
           "account_purpose": "Long-term investing", "source_of_funds": "Salary", "source_of_wealth": "Salary"}


def test_ria_application_with_documents_waits_only_for_the_principal(as_user):
    detail = as_user("granite.advisor").post("/api/portal/accounts", json={
        "application": NEW_APP, "documents": ACCOUNT_DOCS}).json()
    assert detail["status"] == "PENDING_ACCEPTANCE" and detail["path"] == "A" and detail["manual_steps"] == 0
    assert {d["status"] for d in detail["documents"]} == {"in_good_order"}
    assert accept(as_user("rs.principal"), detail["id"])["status"] == "OPEN"


def test_checkboxes_without_documents_dont_count(as_user):
    claimed = {**NEW_APP, "lpoa_signed": True, "fee_authorization": True, "client_signature": True, "tax_form": "W-9"}
    detail = as_user("bonneville.advisor").post("/api/portal/accounts", json={
        "application": claimed, "documents": ACCOUNT_DOCS[:1] + ACCOUNT_DOCS[2:3]}).json()
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


def test_licensed_team_registrations_are_checked_and_need_a_second_admin(as_user):
    admin = as_user("admin.platform")
    base = {"username": "p.reyes", "display_name": "Pat Reyes", "role": "ria_service", "password": "long-password-1"}
    assert admin.post("/api/admin/users", json=base).status_code == 422                       # no registrations
    assert admin.post("/api/admin/users", json={**base, "registrations": ["S24"],
                                                "individual_crd": "7700150"}).status_code == 422  # no Series 7
    assert admin.post("/api/admin/users", json={**base, "role": "ops_analyst", "username": "p.ops",
                                                "registrations": ["S7"], "individual_crd": "7700151"}).status_code == 422
    out = admin.post("/api/admin/users", json={**base, "registrations": ["S7", "S63", "S24"],
                                               "individual_crd": "7700150"}).json()
    assert out["grant"]["registrations"] == ["Series 7", "Series 63", "Series 24"]
    as_user("admin.second").post(f"/api/admin/grants/{out['grant']['id']}/approve")
    pat = [u for u in as_user("admin.second").get("/api/admin/users").json() if u["username"] == "p.reyes"][0]
    assert pat["can_accept_accounts"] and pat["individual_crd"] == "7700150"


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
           "principal_address": "90 N Main St, Logan, UT 84321", "ein": "87-1234520",
           "people": [{"name": "Lee Carter", "role": "control_person", "ownership_pct": 100,
                       "identity_verified": True}]}            # claimed, but identity is verified by a person
    docs = [{"doc_type": t, "sample": True} for t in ("formation_documents", "ownership_chart", "bo_certification",
                                                     "custodial_agreement")]
    ops = as_user("ops.analyst")
    detail = ops.post("/api/intake/firm", json={"application": app, "documents": docs}).json()
    assert detail["status"] == "PENDING_ITEMS" and [c["field"] for c in detail["corrections"]] == ["verify_people"]
    ids = upload_sample(ops, detail["id"], "identity_documents")
    detail = ops.post(f"/api/cases/{detail['id']}/corrections", json={
        "verify_people": True, "documents": {"verify_people": ids["id"]}}).json()
    assert detail["status"] == "PENDING_ACCEPTANCE"


# --- Dashboard, audit, reset --------------------------------------------------------


def test_dashboard_reports_rates_and_queues(as_user):
    d = as_user("ops.supervisor").get("/api/dashboard").json()
    assert d["straight_through"]["opened"] >= 7 and 0 <= d["straight_through"]["rate"] <= 1
    assert d["nigo"]["with_nigo"] >= 2 and {q["key"] for q in d["queues"]} >= {"sanctions_review", "edd_review"}


def test_audit_log_records_runs_and_exports(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Ilya Sorvetkin")
    events = ops.get(f"/api/cases/{cid}/audit").json()
    actions = [e["action"] for e in events]
    assert actions[0] == "case_created" and "document_uploaded" in actions
    assert actions.index("case_run") < actions.index("restriction_placed")
    export = ops.get("/api/audit/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]


def test_demo_reset(as_user):
    admin = as_user("admin.platform")
    admin.post("/api/admin/users", json={"username": "temp.user", "display_name": "Temp", "role": "ops_analyst",
                                         "password": "long-password-1"})
    assert admin.post("/api/admin/reset").json() == {"ok": True}
    admin = as_user("admin.platform")
    assert "temp.user" not in {u["username"] for u in admin.get("/api/admin/users").json()}
