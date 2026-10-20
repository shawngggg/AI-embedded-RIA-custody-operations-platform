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
    r = client.post(f"/api/cases/{cid}/acceptance", json={"value": "accepted", "reviewed": True})
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
    hours = as_user("ops.supervisor").get("/api/dashboard").json()["hours_in_state"]
    assert hours["PENDING_EDD"]["average"] > 24


def test_queues_have_seeded_work(as_user):
    def counts(username):
        return {q["key"]: q["count"] for q in as_user(username).get("/api/queues").json()["queues"]}
    assert counts("sanctions.maker") == {"sanctions_review": 2}
    assert counts("aml.maker")["edd_review"] == 2 and counts("aml.maker")["reviews"] >= 2
    ops = counts("ops.supervisor")                         # the supervisor oversees every operations queue
    assert ops["items_requested"] == 3 and ops["confirmation"] == 1
    assert counts("rs.principal") == {"principal_acceptance": 2}           # Juniper Ridge and Lena Park


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
    log = as_user("ops.supervisor").get("/api/audit?action=decision_refused").json()
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
    log = as_user("ops.supervisor").get("/api/audit?action=acceptance_refused").json()
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
    assert lpoa["channel"] == "portal" and lpoa["status"] == "received" and lpoa["checks"] == []   # checks stay inside
    seen_by_ops = {d["id"]: d for d in as_user("ops.analyst").get(f"/api/cases/{cid}/documents").json()}
    assert seen_by_ops[lpoa["id"]]["checks_passed"]
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
    assert detail["status"] == "PENDING_ACCEPTANCE" and not detail["result"]["requested_items"]
    docs = {d["doc_type"]: d for d in detail["documents"]}
    assert docs["lpoa"]["status"] == "in_good_order" and docs["lpoa"]["reviewed_by"] == "Jamie Ortiz"
    event = as_user("ops.supervisor").get("/api/audit?action=items_received").json()[0]
    assert {d["sha256"] for d in event["detail"]["documents"]} == {lpoa["sha256"], w9["sha256"]}
    detail = accept(as_user("rs.principal"), cid)
    assert detail["status"] == "OPEN" and not [r for r in detail["restrictions"] if r["active"]]


def test_rosa_waits_for_her_lpoa_because_it_is_regulatory(as_user):
    detail = as_user("ops.analyst").get(f"/api/cases/{case_id(as_user('ops.analyst'), 'Rosa Delgado')}").json()
    assert detail["status"] == "PENDING_ITEMS" and detail["account"]["status"] == "PENDING"
    assert detail["acceptance"] is None


def test_items_received_on_an_open_restricted_account_lift_the_docs_code(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Owen Gallagher")
    detail = ops.get(f"/api/cases/{cid}").json()
    assert detail["status"] == "OPEN_RESTRICTED" and detail["account"]["status"] == "OPEN"
    assert [r["code"] for r in detail["restrictions"] if r["active"]] == ["DOCS"]
    assert {c["field"] for c in detail["corrections"]} == {"fee_authorization", "tax_form"}
    fee, w9 = upload_sample(ops, cid, "fee_authorization"), upload_sample(ops, cid, "w9")
    detail = ops.post(f"/api/cases/{cid}/corrections", json={
        "fields": {"fee_authorization": True, "tax_form": "W-9"},
        "documents": {"fee_authorization": fee["id"], "tax_form": w9["id"]}}).json()
    assert detail["status"] == "OPEN"
    assert not [r for r in detail["restrictions"] if r["active"] and r["code"] == "DOCS"]


def test_the_app_and_the_engine_agree_on_what_can_wait():
    from account_rules import NON_REGULATORY
    from app.documents import NON_REGULATORY_FIELDS
    from app.views import CORRECTIONS
    assert NON_REGULATORY_FIELDS["account"] == {f for rid in NON_REGULATORY for f, *_ in CORRECTIONS[rid]}
    assert set(NON_REGULATORY) == {"ACC-003", "ACC-004"}       # fee authorization and tax form (Shawn, Oct. 11)


def test_the_same_file_twice_is_refused_and_extra_copies_are_closed_out(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Rosa Delgado")
    first = upload_sample(ops, cid, "lpoa")
    again = ops.post(f"/api/cases/{cid}/documents", data={"doc_type": "lpoa", "sample": "true"})
    assert again.status_code == 409 and first["filename"] in again.json()["detail"]
    # A different copy of the same document (re-sent with another date) is allowed
    other = ops.post(f"/api/cases/{cid}/documents", data={"doc_type": "lpoa", "sample": "true",
                                                         "received_on": "2026-10-09"}).json()
    assert other["sha256"] != first["sha256"]
    w9 = upload_sample(ops, cid, "w9")
    detail = ops.post(f"/api/cases/{cid}/corrections", json={
        "fields": {"lpoa_signed": True, "tax_form": "W-9"},
        "documents": {"lpoa_signed": other["id"], "tax_form": w9["id"]}}).json()
    status = {d["id"]: d["status"] for d in detail["documents"]}
    assert status[other["id"]] == "in_good_order" and status[first["id"]] == "superseded"


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
    scan = {d["id"]: d for d in as_user("ops.analyst").get(f"/api/cases/{cid}/documents").json()}[scan["id"]]
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
    detail = as_user("bonneville.advisor").post("/api/portal/accounts", json={
        "application": NEW_APP, "documents": ACCOUNT_DOCS[:1] + ACCOUNT_DOCS[2:3]}).json()
    cid = detail["id"]
    ops = as_user("ops.analyst")
    lpoa, w9 = upload_sample(ops, cid, "lpoa"), upload_sample(ops, cid, "w9")
    detail = ops.post(f"/api/cases/{cid}/corrections", json={"fields": {"lpoa_signed": True, "tax_form": "W-9"},
                                                            "documents": {"lpoa_signed": lpoa["id"], "tax_form": w9["id"]}}).json()
    assert detail["status"] == "PENDING_ACCEPTANCE" and not detail["result"]["requested_items"]
    # Jamie later joins the RIA service team as a principal
    admin = as_user("admin.platform")
    jamie = [u for u in admin.get("/api/admin/users").json() if u["username"] == "ops.analyst"][0]
    role = admin.post(f"/api/admin/users/{jamie['id']}/role", json={"role": "ria_service"}).json()["grant"]
    as_user("admin.second").post(f"/api/admin/grants/{role['id']}/approve")
    regs = as_user("admin.platform").post(f"/api/admin/users/{jamie['id']}/registrations", json={
        "registrations": ["S7", "S24"], "individual_crd": "7700199"}).json()["grant"]
    as_user("admin.second").post(f"/api/admin/grants/{regs['id']}/approve")
    r = as_user("ops.analyst").post(f"/api/cases/{cid}/acceptance", json={"value": "accepted", "reviewed": True})
    assert r.status_code == 403 and "prepared" in r.json()["detail"]
    assert accept(as_user("rs.principal"), cid)["status"] == "OPEN"


# --- Undoing an acceptance ----------------------------------------------------------


def test_accepting_needs_the_review_confirmation(as_user):
    cid = case_id(as_user("ops.analyst"), "Lena Park")
    r = as_user("rs.principal").post(f"/api/cases/{cid}/acceptance", json={"value": "accepted"})
    assert r.status_code == 422 and "reviewed" in r.json()["detail"]


def test_rescission_restricts_at_once_and_needs_the_team_lead(as_user):
    cid = case_id(as_user("ops.analyst"), "Lena Park")
    accept(as_user("rs.principal"), cid)
    body = {"kind": "regulatory_issue", "return_to": "operations",
            "reason": "The ID on file belongs to a different Lena Park; CIP has to be redone"}
    assert as_user("rs.associate").post(f"/api/cases/{cid}/rescission", json=body).status_code == 403
    detail = as_user("rs.principal").post(f"/api/cases/{cid}/rescission", json=body).json()
    assert detail["status"] == "OPEN"
    assert [r["code"] for r in detail["restrictions"] if r["active"]] == ["RSCN"]      # restricted at once
    pending = [d for d in detail["decision_log"] if d["decision_type"] == "rescission"][0]
    assert as_user("rs.principal").post(f"/api/decisions/{pending['id']}/approve").status_code == 403   # requester
    assert as_user("ops.supervisor").post(f"/api/decisions/{pending['id']}/approve").status_code == 403  # wrong team
    assert as_user("rs.principal").post("/api/restrictions/" + str(detail["restrictions"][0]["id"]) + "/remove",
                                        json={"reason": "done", "authorization": "x"}).status_code == 409
    detail = as_user("rs.lead").post(f"/api/decisions/{pending['id']}/approve").json()
    assert detail["status"] == "PENDING_REWORK" and detail["account"]["status"] == "PENDING"
    assert not [r for r in detail["restrictions"] if r["active"]]
    events = [e for e in as_user("rs.lead").get("/api/risk-events").json()["events"] if e["source"] == "rescission"]
    assert events[0]["category"] == "Principal acceptance rescinded" and events[0]["approved_by"] == "Renee Alvarez"
    assert events[0]["risk_type"] == "regulatory"
    # Operations reworks and sends it back; the principal accepts again
    ops = as_user("ops.analyst")
    detail = ops.post(f"/api/cases/{cid}/rework-complete", json={"note": "Replaced the ID; CIP re-verified"}).json()
    assert detail["status"] == "PENDING_ACCEPTANCE"
    assert accept(as_user("rs.principal"), cid)["status"] == "OPEN"


def test_rejected_rescission_lifts_the_hold_and_the_acceptance_stands(as_user):
    cid = case_id(as_user("ops.analyst"), "Account opening: Maria Chen")
    detail = as_user("rs.principal").post(f"/api/cases/{cid}/rescission", json={
        "kind": "accepted_before_review", "return_to": "review", "reason": "Clicked accept before opening the documents"}).json()
    pending = [d for d in detail["decision_log"] if d["decision_type"] == "rescission"][0]
    detail = as_user("rs.lead").post(f"/api/decisions/{pending['id']}/reject").json()
    assert detail["status"] == "OPEN" and not [r for r in detail["restrictions"] if r["active"] and r["code"] == "RSCN"]
    events = as_user("rs.lead").get("/api/risk-events").json()["events"]
    rejected = [e for e in events if e["source"] == "rescission"]
    assert len(rejected) == 1 and rejected[0]["category"] == "Rescission rejected"   # every request is logged


def test_a_firm_with_open_accounts_cant_be_unopened(as_user):
    cid = case_id(as_user("ops.analyst"), "Granite Peak")
    r = as_user("rs.principal").post(f"/api/cases/{cid}/rescission", json={
        "kind": "regulatory_issue", "return_to": "operations", "reason": "Ownership chart is missing a 30% owner"})
    assert r.status_code == 409 and "restrict" in r.json()["detail"]


def test_reopening_a_non_regulatory_document_keeps_the_account_open_but_restricted(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Account opening: Maria Chen")
    docs = {d["doc_type"]: d for d in ops.get(f"/api/cases/{cid}").json()["documents"]}
    out = ops.post(f"/api/documents/{docs['fee_authorization']['id']}/reject",
                   json={"reason": "Fee authorization names a different adviser"})
    assert out.status_code == 200 and out.json()["status"] == "not_in_good_order"
    detail = ops.get(f"/api/cases/{cid}").json()
    assert detail["status"] == "OPEN_RESTRICTED" and detail["account"]["status"] == "OPEN"
    assert [r["code"] for r in detail["restrictions"] if r["active"]] == ["DOCS"]
    event = [e for e in as_user("ops.supervisor").get("/api/risk-events").json()["events"] if e["source"] == "document"][0]
    assert event["category"] == "Document accepted in error" and event["owner"] == "ops_supervisor"
    # A team lead records the remediation path and closes it; an analyst can't
    assert as_user("ops.analyst").post(f"/api/risk-events/{event['id']}", json={
        "remediation": "training", "note": "Refresher on fee authorization checks"}).status_code == 403
    closed = as_user("ops.supervisor").post(f"/api/risk-events/{event['id']}", json={
        "remediation": "procedure", "note": "Add an adviser-name check to the fee authorization review step",
        "close": True}).json()
    assert closed["status"] == "closed" and closed["remediation_label"] == "Procedure update"


def test_a_regulatory_document_on_an_open_account_needs_a_rescission(as_user):
    ops = as_user("ops.analyst")
    cid = case_id(ops, "Whitfield")
    docs = {d["doc_type"]: d for d in ops.get(f"/api/cases/{cid}").json()["documents"]}
    r = ops.post(f"/api/documents/{docs['formation_documents']['id']}/reject", json={"reason": "Wrong entity"})
    assert r.status_code == 409 and "rescind" in r.json()["detail"]
    # The client's signature and the LPOA are regulatory too
    cid = case_id(ops, "Account opening: Maria Chen")
    docs = {d["doc_type"]: d for d in ops.get(f"/api/cases/{cid}").json()["documents"]}
    for doc_type in ("lpoa", "account_application"):
        r = ops.post(f"/api/documents/{docs[doc_type]['id']}/reject", json={"reason": "Signature page missing"})
        assert r.status_code == 409 and "rescind" in r.json()["detail"]
        assert docs[doc_type]["reopen_refusal"]


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
    submitted = as_user("granite.advisor").post("/api/portal/accounts", json={
        "application": NEW_APP, "documents": ACCOUNT_DOCS}).json()
    assert submitted["status"] == "IN_REVIEW"                # the portal sees "In review", nothing internal
    detail = as_user("ops.analyst").get(f"/api/cases/{submitted['id']}").json()
    assert detail["status"] == "PENDING_ACCEPTANCE" and detail["path"] == "A" and detail["manual_steps"] == 0
    assert {d["status"] for d in detail["documents"]} == {"in_good_order"}
    assert accept(as_user("rs.principal"), detail["id"])["status"] == "OPEN"


def test_checkboxes_without_documents_dont_count_and_the_account_opens_restricted(as_user):
    claimed = {**NEW_APP, "lpoa_signed": True, "fee_authorization": True, "client_signature": True, "tax_form": "W-9"}
    submitted = as_user("bonneville.advisor").post("/api/portal/accounts", json={
        "application": claimed, "documents": ACCOUNT_DOCS[:2]}).json()
    assert {c["field"] for c in submitted["corrections"]} == {"fee_authorization", "tax_form"}
    detail = as_user("ops.analyst").get(f"/api/cases/{submitted['id']}").json()
    assert detail["status"] == "PENDING_ACCEPTANCE"          # the fee authorization and W-9 aren't regulatory
    detail = accept(as_user("rs.principal"), detail["id"])
    assert detail["status"] == "OPEN_RESTRICTED"
    assert "DOCS" in [r["code"] for r in detail["restrictions"] if r["active"]]
    portal = as_user("bonneville.advisor").get(f"/api/cases/{detail['id']}").json()
    assert portal["status"] == "OPEN_RESTRICTED"
    assert {"reason": "Waiting for documents from you", "limits": ["trading", "fee deduction"]} in portal["restrictions_portal"]
    assert not {"code", "owner", "placed_by"} & {k for r in portal["restrictions_portal"] for k in r}


def test_a_missing_lpoa_or_client_signature_holds_the_account(as_user):
    for missing in ("lpoa", "account_application"):
        docs = [d for d in ACCOUNT_DOCS if d["doc_type"] != missing]
        detail = as_user("granite.advisor").post("/api/portal/accounts", json={
            "application": {**NEW_APP, "holders": [{**NEW_APP["holders"][0], "name": f"Nina Park {missing}"}]},
            "documents": docs}).json()
        assert detail["status"] == "PENDING_ITEMS" and detail["requested_items"], missing


def test_a_regulatory_gap_waits_for_items(as_user):
    app = {**NEW_APP, "account_purpose": None}        # Bonneville's accounts take Path B, where CDD needs the purpose
    submitted = as_user("bonneville.advisor").post("/api/portal/accounts", json={"application": app,
                                                                                  "documents": ACCOUNT_DOCS}).json()
    assert submitted["status"] == "PENDING_ITEMS" and "result" not in submitted
    detail = as_user("ops.analyst").get(f"/api/cases/{submitted['id']}").json()
    assert "CDD-001" in [d["rule_id"] for d in detail["result"]["findings"]["decisions"]]



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
                                                 "role": "ops_analyst", "team": "onboarding", "password": "long-password-1"})
    assert refused.status_code == 403
    assert {u["firm"]["crd"] for u in ria.get("/api/admin/users").json()} == {"310001"}


def test_licensed_team_registrations_are_checked_and_need_a_second_admin(as_user):
    admin = as_user("admin.platform")
    base = {"username": "p.reyes", "display_name": "Pat Reyes", "role": "ria_service", "password": "long-password-1"}
    assert admin.post("/api/admin/users", json=base).status_code == 422                       # no registrations
    assert admin.post("/api/admin/users", json={**base, "registrations": ["S24"],
                                                "individual_crd": "7700150"}).status_code == 422  # no Series 7
    assert admin.post("/api/admin/users", json={**base, "role": "ops_analyst", "team": "onboarding", "username": "p.ops",
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
    assert ops.get("/api/audit/export").status_code == 403         # the whole log is for oversight roles
    export = as_user("ops.supervisor").get("/api/audit/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]


def test_demo_reset(as_user):
    admin = as_user("admin.platform")
    admin.post("/api/admin/users", json={"username": "temp.user", "display_name": "Temp", "role": "ops_analyst", "team": "onboarding",
                                         "password": "long-password-1"})
    assert admin.post("/api/admin/reset").json() == {"ok": True}
    admin = as_user("admin.platform")
    assert "temp.user" not in {u["username"] for u in admin.get("/api/admin/users").json()}


# --- Gate metrics: traceability and logged refusals -------------------------------------


def test_every_decision_event_carries_its_basis(as_user):
    cid = case_id(as_user("ops.analyst"), "Ilya Sorvetkin")
    d = as_user("sanctions.maker").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "sanctions", "value": "false_positive", "reference": "DET-T-1"}).json()
    as_user("sanctions.checker").post(f"/api/decisions/{d['id']}/approve", json={})
    event = as_user("ops.supervisor").get("/api/audit?action=decision_approved").json()[0]["detail"]
    assert event["policy_version"] == "2026.10"
    sanctions = [b for b in event["basis"] if b["rule_id"] == "SCR-001"][0]
    assert "31 CFR Chapter V" in sanctions["source"] and sanctions["flagged_entries"]
    lena = case_id(as_user("ops.analyst"), "Lena Park")
    accepted = accept(as_user("rs.principal"), lena)
    assert accepted["status"] == "OPEN"
    event = as_user("ops.supervisor").get("/api/audit?action=principal_accepted").json()[0]["detail"]
    assert event["policy_version"] and any(b["rule_id"] == "FINRA-4512" for b in event["basis"])
    assert all(b["source"] and b["rule_version"] for b in event["basis"])


def test_refusals_are_logged_not_just_refused(as_user):
    def refused(action):
        return as_user("admin.platform").get(f"/api/audit?action={action}").json()
    assert as_user("granite.advisor").get("/api/queues").status_code == 403
    assert refused("access_refused")[0]["detail"]["reason"] == "This part of the workbench isn't available to your role"
    rosa = case_id(as_user("ops.analyst"), "Rosa Delgado")
    assert as_user("granite.advisor").get(f"/api/cases/{rosa}").status_code == 404     # another firm's case
    assert refused("access_refused")[0]["case_id"] == rosa
    doc = upload_sample(as_user("bonneville.advisor"), rosa, "lpoa")
    assert as_user("bonneville.advisor").post(f"/api/documents/{doc['id']}/reject",
                                              json={"reason": "Unsigned"}).status_code == 403
    assert refused("document_check_refused")
    event = as_user("rs.lead").get("/api/risk-events").json()["events"]
    assert event                                     # Juniper Ridge's acceptance is past its SLA
    assert as_user("ops.analyst").post(f"/api/risk-events/{event[0]['id']}", json={
        "remediation": "training", "note": "Refresher training for the team"}).status_code == 404   # not their area
    assert refused("ops_risk_event_update_refused")


def test_intake_reports_how_live_ai_signs_in(as_user, monkeypatch):
    from app.config import settings
    ops = as_user("ops.analyst")
    assert ops.get("/api/intake/status").json()["auth"] == "none"
    monkeypatch.setattr(settings, "federation_rule_id", "fdrl_test")
    monkeypatch.setattr(settings, "anthropic_org_id", "org_test")
    status = ops.get("/api/intake/status").json()
    assert status["live"] and status["auth"] == "workload identity federation"


# --- Walkthrough feedback (Oct. 13): teams, menus by role, the Work page ------------------


def test_menus_follow_role_and_team(as_user):
    def caps(u):
        return as_user(u).get("/api/auth/me").json()["capabilities"]
    jamie, taylor, priya = caps("ops.analyst"), caps("ops.cip"), caps("ops.supervisor")
    assert jamie["intake"] and not jamie["approvals"] and not jamie["audit"] and not jamie["dashboard"]
    assert not taylor["intake"]                                   # CIP, not onboarding
    assert priya["approvals"] and priya["dashboard"]
    assert as_user("ops.analyst").get("/api/decisions").status_code == 403
    assert as_user("ops.cip").get("/api/intake/status").status_code == 403
    refused = as_user("ops.supervisor").get("/api/audit?action=access_refused").json()
    assert {e["actor"] for e in refused} >= {"Jamie Ortiz", "Taylor Brooks"}


def test_queues_are_marked_by_team(as_user):
    def mine(u):
        return {q["key"] for q in as_user(u).get("/api/queues").json()["queues"] if q["mine"]}
    assert mine("ops.analyst") == {"items_requested", "rework"}
    assert mine("ops.cip") == {"verification"}
    assert mine("ops.service") == {"confirmation", "maintenance_items"}     # account maintenance
    assert {"items_requested", "confirmation", "verification", "rework", "maintenance_items"} <= mine("ops.supervisor")


def test_work_page_shows_what_needs_each_person(as_user):
    jamie = as_user("ops.analyst").get("/api/work").json()
    assert any("Rosa Delgado" in c["title"] for c in jamie["needs_you"]) and jamie["to_approve"] == []
    avery = as_user("rs.principal").get("/api/work").json()
    assert {c["status"] for c in avery["needs_you"]} == {"PENDING_ACCEPTANCE"}
    cid = case_id(as_user("ops.analyst"), "Ilya Sorvetkin")
    as_user("sanctions.maker").post(f"/api/cases/{cid}/decisions", json={
        "decision_type": "sanctions", "value": "false_positive", "reference": "DET-W-1"})
    sam = as_user("sanctions.checker").get("/api/work").json()
    assert [d["case_id"] for d in sam["to_approve"]] == [cid]
    alex = as_user("sanctions.maker").get("/api/work").json()
    assert alex["to_approve"] == [] and [d["case_id"] for d in alex["my_proposals"]] == [cid]


def test_a_submitted_package_lands_in_a_queue(as_user):
    detail = _upload_sample(as_user("ops.analyst"))
    assert detail["queue"] == "items_requested"
    assert any(c["id"] == detail["id"] for c in as_user("ops.analyst").get("/api/work").json()["needs_you"])


def test_restricted_accounts_lists_each_account_once(as_user):
    ops = as_user("ops.analyst")
    ops.post("/api/restrictions", json={"target_id": "ACC-2001", "code": "CIPV", "reason": "Returned mail"})
    rows = {r["target_id"]: r for r in ops.get("/api/restrictions/accounts").json()}
    maria = rows["ACC-2001"]
    assert maria["name"] == "Maria Chen" and maria["kind"] == "account" and "CIPV" in [c["code"] for c in maria["codes"]]
    assert maria["blocks"] and maria["firm"] == "Granite Peak Advisors LLC"
    assert any(r["kind"] == "firm" for r in rows.values())                  # Meridian Gate's sanctions hold
    assert as_user("admin.platform").get("/api/restrictions/accounts").status_code == 403


def test_risk_register_covers_operational_regulatory_and_financial(as_user):
    events = as_user("ops.analyst").get("/api/risk-events").json()["events"]
    sla = [e for e in events if e["source"] == "sla"]
    assert any("Rosa Delgado" in (e["case_title"] or "") for e in sla) and all(e["risk_type"] == "operational" for e in sla)
    meridian = case_id(as_user("ops.analyst"), "Meridian Gate")
    d = as_user("sanctions.maker").post(f"/api/cases/{meridian}/decisions", json={
        "decision_type": "sanctions", "value": "true_match", "reference": "DET-R-1"}).json()
    as_user("sanctions.checker").post(f"/api/decisions/{d['id']}/approve", json={})
    true_match = [e for e in as_user("sanctions.maker").get("/api/risk-events?risk_type=regulatory").json()["events"]
                  if e["source"] == "sanctions"][0]
    assert true_match["owner"] == "sanctions" and true_match["area"] == "sanctions"
    assert "31 CFR 501.603" in true_match["summary"]
    lena = case_id(as_user("ops.analyst"), "Lena Park")
    accept(as_user("rs.principal"), lena)
    as_user("rs.principal").post(f"/api/cases/{lena}/rescission", json={
        "kind": "accepted_before_review", "return_to": "review", "reason": "Accepted before opening the documents"})
    requested = [e for e in as_user("rs.lead").get("/api/risk-events").json()["events"] if e["source"] == "rescission"]
    assert requested[0]["category"] == "Rescission requested" and requested[0]["status"] == "open"
    fin = as_user("ops.analyst").post("/api/risk-events", json={
        "category": "Fee debited twice", "risk_type": "financial", "area": "billing",
        "summary": "A client reported two identical advisory fee debits this quarter.", "target_id": "ACC-2001"}).json()
    assert fin["risk_type_label"] == "Financial" and fin["source"] == "manual" and not fin["can_update"]
    assert fin["owner"] == "ops_supervisor" and fin["reported_by_me"]          # the reporter keeps seeing it
    assert as_user("sanctions.checker").post(f"/api/risk-events/{true_match['id']}", json={
        "remediation": "report", "note": "Rejected-transaction report filed with OFAC", "close": True}).json()["status"] == "closed"
    assert as_user("ops.analyst").post(f"/api/risk-events/{fin['id']}", json={
        "remediation": "procedure", "note": "Add a duplicate-debit check"}).status_code == 403


def test_team_channels_link_cases_accounts_and_people(as_user):
    jamie = as_user("ops.analyst")
    chans = jamie.get("/api/channels").json()
    keys = [c["key"] for c in chans["channels"]]
    assert "onboarding" in keys and "sanctions" not in keys                 # sanctions data stays with that team
    assert chans["channels"][keys.index("onboarding")]["unread"] >= 1       # Priya mentioned Jamie
    seeded = jamie.get("/api/channels/onboarding/messages").json()
    assert any(m["mentions_me"] for m in seeded)
    assert jamie.get("/api/channels/unread").json()["by_channel"]["onboarding"] == 0   # reading marks them read
    rosa = case_id(jamie, "Rosa Delgado")
    msg = jamie.post("/api/channels/onboarding/messages", json={
        "body": f"Case #{rosa}: LPOA received; @ops.supervisor please review. Also see ACC-2001."}).json()
    assert {(r["type"], r["id"]) for r in msg["refs"]} == {("case", rosa), ("account", "ACC-2001")}
    assert msg["mentions"] == [{"username": "ops.supervisor", "name": "Priya Raman"}]
    about = jamie.get(f"/api/messages/about?case_id={rosa}").json()
    assert {m["id"] for m in about} >= {msg["id"]}
    assert jamie.get("/api/channels/sanctions/messages").status_code == 403
    assert as_user("sanctions.maker").get("/api/channels/sanctions/messages").json()
    assert as_user("granite.advisor").get("/api/channels").status_code == 403


def test_portal_home_welcomes_the_firm(as_user):
    home = as_user("bonneville.advisor").get("/api/portal/home").json()
    assert home["firm"]["name"] == "Bonneville Planning Group" or home["firm"]["crd"] == "310003"
    assert any("Rosa Delgado" in a["title"] for a in home["attention"])          # the LPOA Bonneville owes
    assert home["contact"]["name"] == "Renee Alvarez" and home["accounts"]["total"] >= 1
    farid = as_user("client.farid").get("/api/portal/home").json()
    assert farid["accounts"]["total"] == 1 and farid["name"] == "Farid"
    assert as_user("ops.analyst").get("/api/portal/home").status_code == 404


def test_notices_go_through_the_outbox(as_user):
    box = as_user("ops.analyst").get("/api/outbox").json()
    assert box["connection"]["provider"] == "outbox" and box["connection"]["demo_hold"]
    rosa = [e for e in box["emails"] if e["template"] == "items_requested" and "Rosa Delgado" in e["subject"]]
    assert rosa and rosa[0]["to"] == "sam.okafor@bonneville.example" and rosa[0]["status"] == "held"
    assert "Client-signed LPOA" in rosa[0]["body"]
    cid = case_id(as_user("ops.analyst"), "Lena Park")
    accept(as_user("rs.principal"), cid)
    opened = [e for e in as_user("ops.analyst").get("/api/outbox").json()["emails"] if e["case_id"] == cid]
    assert {e["template"] for e in opened} == {"account_opened"}
    assert {e["to"] for e in opened} == {"dana.whitfield@granitepeak.example", "marcus.lee@granitepeak.example"}
    assert as_user("aml.maker").get("/api/outbox").status_code == 403


def test_microsoft_365_sends_through_graph(monkeypatch):
    from app import mail
    from app.orm import OutboundEmail
    monkeypatch.setenv("MS_TENANT_ID", "tenant-1"); monkeypatch.setenv("MS_CLIENT_ID", "client-1")
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret-1"); monkeypatch.setenv("MAIL_SENDER", "notices@custodian.example")
    calls = []

    class Resp:
        def __init__(self, code, body=None): self.status_code, self._body, self.text = code, body or {}, ""
        def json(self): return self._body
        def raise_for_status(self): pass

    class Http:
        def post(self, url, **kw):
            calls.append((url, kw))
            return Resp(200, {"access_token": "graph-token"}) if "login.microsoftonline.com" in url else Resp(202)
    e = OutboundEmail(to_address="ops@ria.example", to_name="RIA Ops", subject="Hi", body="Body", template="t")
    mail.send_m365(e, http=Http())
    token_url, token_kw = calls[0]
    send_url, send_kw = calls[1]
    assert token_url == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    assert token_kw["data"]["scope"] == "https://graph.microsoft.com/.default" and token_kw["data"]["grant_type"] == "client_credentials"
    assert send_url == "https://graph.microsoft.com/v1.0/users/notices@custodian.example/sendMail"
    assert send_kw["headers"]["Authorization"] == "Bearer graph-token"
    assert send_kw["json"]["message"]["toRecipients"][0]["emailAddress"]["address"] == "ops@ria.example"


# --- Who sees what (the per-role sanity check of Oct. 13) ------------------------------------

PLANNED_ALL = {"planned_transfers", "planned_trading", "planned_billing"}
EXPECTED_CAPABILITIES = {      # the planned workbenches show only to the teams that will run them
    "ops.analyst": {"work", "cases", "queues", "messages", "risk_events", "intake", "restricted_accounts", "outbox"},
    "ops.cip": {"work", "cases", "queues", "messages", "risk_events", "restricted_accounts", "outbox"},
    "ops.service": {"work", "cases", "queues", "messages", "risk_events", "restricted_accounts", "outbox"},
    # Teams whose workbench arrives in a later release: no queue or notices yet, their own planned page
    "ops.transfers": {"work", "cases", "messages", "risk_events", "restricted_accounts", "planned_transfers"},
    "ops.trade": {"work", "cases", "messages", "risk_events", "restricted_accounts", "planned_trading"},
    "ops.billing": {"work", "cases", "messages", "risk_events", "restricted_accounts", "planned_billing"},
    "ops.supervisor": {"work", "cases", "queues", "messages", "risk_events", "approvals", "reviews", "intake",
                       "restricted_accounts", "audit", "dashboard", "outbox"} | PLANNED_ALL,
    "aml.maker": {"work", "cases", "queues", "messages", "risk_events", "approvals", "reviews", "restricted_accounts",
                  "audit"},
    "sanctions.maker": {"work", "cases", "queues", "messages", "risk_events", "approvals", "restricted_accounts",
                        "audit"},
    "review.analyst": {"work", "cases", "messages", "risk_events", "reviews", "restricted_accounts"},
    "rs.lead": {"work", "cases", "queues", "messages", "risk_events", "approvals", "restricted_accounts", "audit",
                "dashboard", "outbox", "planned_transfers", "planned_trading"},
    "rs.principal": {"work", "cases", "queues", "messages", "risk_events", "restricted_accounts", "planned_transfers",
                     "planned_trading"},
    "admin.platform": {"work", "messages", "risk_events", "audit", "outbox", "admin"},
    "granite.admin": {"portal"}, "granite.advisor": {"portal"}, "client.farid": {"portal"},
}


def test_each_role_gets_only_its_menu(as_user):
    for username, expected in EXPECTED_CAPABILITIES.items():
        me = as_user(username).get("/api/auth/me").json()
        me = me.get("user", me)
        assert {k for k, v in me["capabilities"].items() if v} == expected, username


def test_queues_show_only_the_queues_a_person_works(as_user):
    keys = {u: {q["key"] for q in as_user(u).get("/api/queues").json()["queues"]}
            for u in ("ops.analyst", "ops.cip", "ops.service", "sanctions.maker", "review.analyst")}
    assert keys["ops.analyst"] == {"items_requested", "rework"}
    assert keys["ops.cip"] == {"verification"} and keys["ops.service"] == {"confirmation", "maintenance_items"}
    assert keys["sanctions.maker"] == {"sanctions_review"} and keys["review.analyst"] == {"reviews"}


def test_the_platform_admin_has_no_case_files(as_user):
    c = as_user("admin.platform")
    for path in ("/api/cases", "/api/cases/1", "/api/cases/1/audit", "/api/queues", "/api/dashboard",
                 "/api/restrictions/accounts"):
        assert c.get(path).status_code == 403, path
    reasons = {e["detail"]["reason"] for e in c.get("/api/audit?action=access_refused").json()
               if e["actor"] == "Jordan Blake"}
    assert "Case files aren't part of your role" in reasons


INTERNAL_CASE_KEYS = {"result", "decisions", "decision_log", "restrictions", "queue", "queue_label", "sla", "path",
                      "extra", "application", "acceptance", "manual_steps", "nigo_count", "rescission", "rework",
                      "available_decisions", "queue_entered_at"}
PORTAL_STATUSES = {"PENDING_ITEMS", "RETURNED", "OPEN", "OPEN_RESTRICTED", "APPLIED", "REDIRECTED", "DECLINED",
                   "CONFIRMING", "IN_REVIEW", "SUBMITTED"}


def test_rias_and_clients_see_nothing_of_custody_operations(as_user):
    staff = {u["display_name"] for u in as_user("admin.platform").get("/api/admin/users").json()
             if u["workspace"] == "workbench"}
    as_user("client.farid").post("/api/portal/changes", json={
        "account_id": "ACC-2003", "change_type": "trusted_contact", "client_signature": True,
        "details": {"trusted_contact_name": "Leila Hosseini", "trusted_contact_phone": "801-555-0142"}})
    for username in ("granite.admin", "granite.advisor", "bonneville.advisor", "client.farid"):
        c = as_user(username)
        for path in ("/api/work", "/api/queues", "/api/decisions", "/api/reviews", "/api/channels", "/api/risk-events",
                     "/api/restrictions", "/api/restrictions/accounts", "/api/restrictions/catalog", "/api/audit",
                     "/api/dashboard", "/api/outbox", "/api/admin/rules", "/api/admin/teams", "/api/admin/registrations",
                     "/api/intake/status", "/api/messages/about?case_id=1"):
            assert c.get(path).status_code in (403, 404), (username, path)
        cases = c.get("/api/cases").json()
        assert cases, username
        for summary in cases:
            assert not INTERNAL_CASE_KEYS & set(summary) and summary["status"] in PORTAL_STATUSES, (username, summary)
            detail = c.get(f"/api/cases/{summary['id']}").json()
            assert not INTERNAL_CASE_KEYS & set(detail), (username, INTERNAL_CASE_KEYS & set(detail))
            assert detail["status"] in PORTAL_STATUSES and {h["status"] for h in detail["history"]} <= PORTAL_STATUSES
            blob = str(detail)
            assert not [n for n in staff if n in blob], (username, summary["title"])
            assert not any(w in blob for w in ("SANCTIONS", "Sanctions", "EDD", "is_pep", "adverse_media", "risk_tier"))
            assert all(x["rule_id"] is None for x in detail["corrections"])
            assert c.get(f"/api/cases/{summary['id']}/audit").status_code == 403
        for account in c.get("/api/portal/accounts").json():
            assert "risk_tier" not in account and "path" not in account and account["status"] != "BLOCKED"


def test_a_sanctions_escalation_reads_as_in_review_in_the_portal(as_user):
    ilya = case_id(as_user("ops.analyst"), "Ilya Sorvetkin")
    assert as_user("ops.analyst").get(f"/api/cases/{ilya}").json()["status"] == "PENDING_SANCTIONS"
    portal = as_user("granite.advisor").get(f"/api/cases/{ilya}").json()
    assert portal["status"] == "IN_REVIEW" and portal["status_note"].startswith("We're reviewing")
    home = as_user("granite.advisor").get("/api/portal/home").json()
    assert ilya not in [a["case_id"] for a in home["attention"]]


def test_each_person_sees_only_their_areas_risk_events(as_user):
    def areas(username):
        return {e["area"] for e in as_user(username).get("/api/risk-events?status=").json()["events"]}
    meridian = case_id(as_user("ops.analyst"), "Meridian Gate")
    d = as_user("sanctions.maker").post(f"/api/cases/{meridian}/decisions", json={
        "decision_type": "sanctions", "value": "true_match", "reference": "DET-A-1"}).json()
    as_user("sanctions.checker").post(f"/api/decisions/{d['id']}/approve", json={})
    assert areas("ops.analyst") == {"onboarding"}            # Rosa's items are past their SLA
    assert areas("sanctions.maker") == {"sanctions"}
    assert areas("rs.lead") == {"ria_service"}               # Juniper Ridge's acceptance is past its SLA
    assert areas("admin.platform") == {"access"}             # access and authentication only
    assert {"onboarding"} <= areas("ops.supervisor") and not areas("ops.supervisor") & {"sanctions", "access"}
    assert areas("ops.cip") == set() and areas("aml.maker") == set()
    # A report in another area stays visible to its reporter, and to that area, not to everyone
    as_user("ops.cip").post("/api/risk-events", json={"category": "Wrong ID type accepted", "risk_type": "operational",
                                                      "area": "onboarding", "summary": "A library card was accepted as ID."})
    assert areas("ops.cip") == {"onboarding"} and "Wrong ID type accepted" in str(as_user("ops.analyst").get("/api/risk-events").json())
    assert "Wrong ID type accepted" not in str(as_user("ops.service").get("/api/risk-events").json())
    work = as_user("admin.platform").get("/api/work").json()
    assert work["risk_events_open"] == 2                       # the two seeded access events


def test_access_and_authentication_events_reach_the_admin(as_user):
    rosa = case_id(as_user("ops.analyst"), "Rosa Delgado")
    assert as_user("granite.advisor").get(f"/api/cases/{rosa}").status_code == 404     # another firm's case
    assert as_user("granite.advisor").get(f"/api/cases/{rosa}").status_code == 404     # once a day per person
    for _ in range(5):
        assert as_user("admin.platform").post("/api/auth/login", json={"username": "dev.mallory", "password": "guess-1234"}).status_code == 401
    users = as_user("admin.platform").get("/api/admin/users").json()
    target = next(u for u in users if u["username"] == "review.analyst")
    grant = as_user("admin.platform").post(f"/api/admin/users/{target['id']}/role", json={"role": "aml_compliance"}).json()
    assert as_user("admin.platform").post(f"/api/admin/grants/{grant['grant']['id']}/approve", json={}).status_code == 403
    events = as_user("admin.platform").get("/api/risk-events").json()["events"]
    cats = [e["category"] for e in events]
    assert cats.count("Out-of-scope access attempt") == 2      # the seeded one and Marcus's
    assert "Repeated failed sign-ins" in cats and "Grant self-approval attempt" in cats
    assert all(e["area"] == "access" and e["owner"] == "platform_admin" for e in events)
    assert any("Marcus Lee" in e["summary"] for e in events)
    assert not [e for e in as_user("ops.supervisor").get("/api/risk-events").json()["events"] if e["area"] == "access"]


def test_account_changes_are_account_maintenance_with_client_service(as_user):
    returned = as_user("granite.advisor").post("/api/portal/changes", json={
        "account_id": "ACC-1990", "change_type": "beneficiary", "details": {"beneficiaries": "Lin Chen 100%"}}).json()
    assert returned["status"] == "RETURNED"                   # no client signature on an owner-level change
    case = as_user("ops.service").get(f"/api/cases/{returned['id']}").json()
    assert case["queue"] == "maintenance_items" and case["queue_label"] == "Account changes: items requested"
    assert returned["id"] in [c["id"] for c in as_user("ops.service").get("/api/work").json()["needs_you"]]
    assert returned["id"] not in [c["id"] for c in as_user("ops.analyst").get("/api/work").json()["needs_you"]]
    change = {"account_id": "ACC-2001", "change_type": "address", "details": {"new_address": "9 Elm St, Ogden, UT 84401"},
              "documents": [{"doc_type": "change_form", "sample": True}]}
    assert as_user("ops.analyst").post("/api/portal/changes", json=change).status_code == 403    # onboarding
    assert as_user("ops.service").post("/api/portal/changes", json=change).status_code == 200    # client service


def test_the_ai_evaluation_runs_the_nigo_test_set_for_the_admin(as_user, monkeypatch):
    from app.config import settings
    from app.routers import ai_eval
    from extraction import gate
    from nigo_eval import PACKAGES, answer_key, package_text
    admin = as_user("admin.platform")
    ev = admin.get("/api/ai-evaluation").json()
    assert len(ev["packages"]) == 20 and not ev["can_run"] and "isn't configured" in ev["why_not"] and ev["latest"] is None
    assert admin.post("/api/ai-evaluation", json={}).status_code == 503              # no live AI in the tests
    for u in ("ops.supervisor", "aml.maker", "granite.admin"):
        assert as_user(u).get("/api/ai-evaluation").status_code == 403
    # With a stand-in model that reads like the answer key, the run is scored, logged, and counted against the cap
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    by_text = {package_text(p): p for p in PACKAGES}
    monkeypatch.setattr(ai_eval, "extract_firm_application",
                        lambda text: gate(answer_key(by_text[text]), text, "end_turn", "stand-in"))
    admin = as_user("admin.platform")
    run = admin.post("/api/ai-evaluation", json={}).json()
    assert run["recall"] == 1.0 and run["false_nigo"] == 0 and run["met"] and run["by"] == "Jordan Blake"
    assert run["prompt_version"] and len(run["rows"]) == 20
    assert admin.get("/api/ai-evaluation").json()["latest"]["recall"] == 1.0
    assert admin.post("/api/ai-evaluation", json={}).status_code == 200              # 40 of 50 calls used
    assert admin.post("/api/ai-evaluation", json={}).status_code == 409              # a third run would pass the cap
    assert admin.get("/api/audit?action=ai_evaluation_run").json()[0]["detail"]["model"]


def test_a_sanctions_determination_is_seen_by_every_team_on_the_account_but_not_the_admin_or_the_portal(as_user):
    ilya = case_id(as_user("ops.analyst"), "Ilya Sorvetkin")
    as_user("sanctions.maker").post(f"/api/cases/{ilya}/decisions", json={
        "decision_type": "sanctions", "value": "false_positive", "reference": "DET-V-1"})
    for u in ("ops.analyst", "ops.cip", "ops.service", "ops.trade", "aml.maker", "rs.principal", "review.analyst"):
        log = as_user(u).get(f"/api/cases/{ilya}").json()["decision_log"]
        assert [(d["label"], d["value"], d["reference"]) for d in log] == [("Sanctions determination", "false_positive", "DET-V-1")], u
    assert as_user("admin.platform").get(f"/api/cases/{ilya}").status_code == 403       # access, not cases
    portal = as_user("granite.advisor").get(f"/api/cases/{ilya}").json()
    assert "decision_log" not in portal and "DET-V-1" not in str(portal) and portal["status"] == "IN_REVIEW"


def test_the_new_teams_see_their_planned_workbench_and_channel(as_user):
    marisol = as_user("ops.transfers")
    assert marisol.get("/api/queues").json()["queues"] == []                 # no release 1 queue
    assert "asset-transfer" in [c["key"] for c in marisol.get("/api/channels").json()["channels"]]
    assert marisol.get("/api/outbox").status_code == 403
    assert as_user("ops.trade").get("/api/restrictions/accounts").status_code == 200   # restricted accounts can't trade


def test_a_demo_reset_keeps_the_ai_evaluation_runs(as_user, monkeypatch):
    from app.config import settings
    from app.routers import ai_eval
    from extraction import gate
    from nigo_eval import PACKAGES, answer_key, package_text
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    by_text = {package_text(p): p for p in PACKAGES}
    monkeypatch.setattr(ai_eval, "extract_firm_application",
                        lambda text: gate(answer_key(by_text[text]), text, "end_turn", "stand-in"))
    admin = as_user("admin.platform")
    assert admin.post("/api/ai-evaluation", json={}).status_code == 200
    admin.post("/api/admin/users", json={"username": "temp.user", "display_name": "Temp User", "role": "ops_analyst",
                                         "password": "a-long-temp-password", "team": "onboarding"})
    assert admin.post("/api/admin/reset", json={}).status_code == 200
    admin = as_user("admin.platform")
    assert "temp.user" not in [u["username"] for u in admin.get("/api/admin/users").json()]     # demo data reset
    assert admin.get("/api/ai-evaluation").json()["latest"]["recall"] == 1.0                    # evaluation kept


def test_startup_reseeds_the_demo_when_the_seed_data_changed(client):
    from app.db import SessionLocal
    from app.orm import AuditEvent, User
    from app.seed import SEED_FINGERPRINT, ensure_seeded
    with SessionLocal() as db:
        assert not ensure_seeded(db)                       # same seed data: nothing to do
    with SessionLocal() as db:
        db.add(User(username="stale.user", display_name="Stale", role="ops_analyst", is_demo=False))
        db.query(AuditEvent).filter(AuditEvent.action == "demo_seeded").update({"target_id": "older-seed"})
        db.commit()
    with SessionLocal() as db:
        assert ensure_seeded(db)                           # a deploy brought different seed data
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "stale.user").first() is None
        assert db.query(AuditEvent).filter(AuditEvent.action == "demo_seeded").one().target_id == SEED_FINGERPRINT
    assert client.get("/api/health").json()["seed"] == SEED_FINGERPRINT
