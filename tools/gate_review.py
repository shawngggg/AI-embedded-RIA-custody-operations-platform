"""
MVP gate review: measures the release 1 success metrics in the PRD against a fresh synthetic database.

It seeds the demo data, works every kind of decision the MVP has (sanctions, EDD, confirmation, principal
acceptance, rescission), tries each decision as every role that shouldn't be able to make it, and runs a set of
clean synthetic applications through the portal. Then it reads the results back from the database and the audit
log. Nothing here talks to the live site.

    python tools/gate_review.py              # report to the screen
    python tools/gate_review.py --json out.json
    python tools/gate_review.py --check      # exit 1 if a measured target is missed (CI runs this)

Metrics that need a person (the timed first-time walkthrough) or live AI (NIGO recall, suggestion acceptance)
are reported as not measured, with what they need. Suggestion tracking itself is checked here on the illustrative
sample: every review is recorded with what the reviewer did, and only the onboarding team can review.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="gate-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/gate.db"
os.environ.setdefault("SECRET_KEY", "gate-review-only-key-0123456789-abcdefghijklmnop")
os.environ["DEMO_MODE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.orm import AiSuggestion, AuditEvent, Case, CaseTransition, PendingDecision, User  # noqa: E402
from app.queues import QUEUES  # noqa: E402

USERS = {  # one demo user per role, plus second people for maker-checker
    "ops_analyst": "ops.analyst", "ops_supervisor": "ops.supervisor", "aml_compliance": "aml.maker",
    "sanctions": "sanctions.maker", "periodic_review": "review.analyst", "ria_service": "rs.associate",
    "ria_service_lead": "rs.lead", "platform_admin": "admin.platform", "ria_admin": "granite.admin",
    "ria_user": "granite.advisor", "client": "client.farid",
}
DECISION_EVENTS = ("decision_approved", "decision_rejected", "principal_accepted", "principal_declined")
ACCOUNT_DOCS = [{"doc_type": t, "sample": True} for t in ("account_application", "lpoa", "fee_authorization", "w9")]

_clients: dict[str, TestClient] = {}


def as_user(username: str) -> TestClient:
    if username not in _clients:
        c = TestClient(app)
        r = c.post("/api/auth/demo", json={"username": username})
        assert r.status_code == 200, r.text
        _clients[username] = c
    return _clients[username]


def case_id(title_part: str) -> int:
    rows = as_user("ops.analyst").get("/api/cases").json()
    return next(c["id"] for c in rows if title_part in c["title"])


def refusals_for(username: str) -> int:
    with SessionLocal() as db:
        uid = db.query(User.id).filter(User.username == username).scalar()
        return db.query(AuditEvent).filter(AuditEvent.actor_id == uid, AuditEvent.action.like("%refused%")).count()


# --------------------------------------------------------------------------- role enforcement
attempts: list[dict] = []


def attempt(what: str, username: str, method: str, path: str, body: dict | None = None) -> None:
    """A decision attempt by someone who shouldn't be able to make it: it must be refused, and logged."""
    before = refusals_for(username)
    r = getattr(as_user(username), method)(path, json=body or {})
    with SessionLocal() as db:
        role = db.query(User.role).filter(User.username == username).scalar()
    attempts.append({"decision": what, "user": username, "role": role, "status": r.status_code,
                     "refused": r.status_code in (401, 403, 404), "logged": refusals_for(username) > before})


def wrong_roles(allowed: set[str], exclude: set[str] = frozenset()) -> list[str]:
    return [u for role, u in USERS.items() if role not in allowed and u not in exclude]


def maker_checker(cid: int, dtype: str, value: str, ref: str, maker: str, checker: str, allowed_p: set[str],
                  allowed_a: set[str], matrix: bool) -> None:
    if matrix:
        for u in wrong_roles(allowed_p):
            attempt(f"{dtype}: propose", u, "post", f"/api/cases/{cid}/decisions",
                    {"decision_type": dtype, "value": value, "reference": ref})
    r = as_user(maker).post(f"/api/cases/{cid}/decisions", json={"decision_type": dtype, "value": value, "reference": ref})
    assert r.status_code == 200, (dtype, r.text)
    did = r.json()["id"]
    if matrix:
        for u in wrong_roles(allowed_a):
            attempt(f"{dtype}: approve", u, "post", f"/api/decisions/{did}/approve")
        attempt(f"{dtype}: maker approves own proposal", maker, "post", f"/api/decisions/{did}/approve")
    r = as_user(checker).post(f"/api/decisions/{did}/approve", json={})
    assert r.status_code == 200, (dtype, r.text)


def accept(cid: int, username: str = "rs.principal", value: str = "accepted") -> dict:
    r = as_user(username).post(f"/api/cases/{cid}/acceptance", json={"value": value, "reviewed": True})
    assert r.status_code == 200, r.text
    return r.json()


def work_the_seeded_cases() -> None:
    ilya, meridian = case_id("Ilya Sorvetkin"), case_id("Meridian Gate")
    maker_checker(ilya, "sanctions", "false_positive", "DET-GATE-01", "sanctions.maker", "sanctions.checker",
                  {"sanctions"}, {"sanctions"}, matrix=True)
    maker_checker(meridian, "sanctions", "true_match", "DET-GATE-02", "sanctions.maker", "sanctions.checker",
                  {"sanctions"}, {"sanctions"}, matrix=False)
    elena, lakeshore = case_id("Elena Marsh"), case_id("Lakeshore")
    maker_checker(elena, "edd", "approved", "EDD-GATE-01", "aml.maker", "aml.checker", {"aml_compliance"},
                  {"aml_compliance"}, matrix=True)
    maker_checker(lakeshore, "edd", "approved", "EDD-GATE-02", "aml.maker", "aml.checker", {"aml_compliance"},
                  {"aml_compliance"}, matrix=False)
    chg = case_id("bank instruction on Maria Chen")
    maker_checker(chg, "confirmation", "confirmed", "CALLBACK-GATE", "ops.analyst", "ops.supervisor",
                  {"ops_analyst", "ops_supervisor"}, {"ops_supervisor"}, matrix=True)

    waiting = [c for c in as_user("ops.analyst").get("/api/cases").json() if c["status"] == "PENDING_ACCEPTANCE"]
    first = waiting[0]["id"]
    for u in wrong_roles({"ria_service", "ria_service_lead"}) + ["rs.associate"]:   # rs.associate holds S7 only
        attempt("principal acceptance", u, "post", f"/api/cases/{first}/acceptance", {"value": "accepted", "reviewed": True})
    for c in waiting:
        accept(c["id"])

    lena = case_id("Lena Park")
    rescind = {"kind": "accepted_before_review", "return_to": "review", "reason": "Accepted before opening the documents"}
    for u in wrong_roles({"ria_service", "ria_service_lead"}):
        attempt("rescission: request", u, "post", f"/api/cases/{lena}/rescission", rescind)
    detail = as_user("rs.principal").post(f"/api/cases/{lena}/rescission", json=rescind).json()
    pending = [d for d in detail["decision_log"] if d["decision_type"] == "rescission" and d["status"] == "pending"][0]
    for u in wrong_roles({"ria_service_lead"}) + ["rs.principal"]:
        attempt("rescission: approve", u, "post", f"/api/decisions/{pending['id']}/approve")
    assert as_user("rs.lead").post(f"/api/decisions/{pending['id']}/approve", json={}).status_code == 200
    accept(lena)                                                     # back to the principal, accepted again

    maria = case_id("Account opening: Maria Chen")
    detail = as_user("rs.principal").post(f"/api/cases/{maria}/rescission", json={
        "kind": "regulatory_issue", "return_to": "operations", "reason": "Checking whether the ID was expired"}).json()
    pending = [d for d in detail["decision_log"] if d["decision_type"] == "rescission" and d["status"] == "pending"][0]
    assert as_user("rs.lead").post(f"/api/decisions/{pending['id']}/reject", json={}).status_code == 200

    event = as_user("rs.lead").get("/api/risk-events").json()["events"][0]
    for u in wrong_roles({"ops_supervisor", "ria_service_lead"}):
        attempt("risk event remediation", u, "post", f"/api/risk-events/{event['id']}",
                {"remediation": "training", "note": "Refresher on reviewing before accepting"})
    as_user("rs.lead").post(f"/api/risk-events/{event['id']}", json={
        "remediation": "procedure", "note": "Acceptance screen now asks for review confirmation first", "close": True})

    hold = as_user("ops.analyst").post("/api/restrictions", json={
        "target_id": "ACC-2001", "code": "CIPV", "reason": "Re-verify identity after returned mail"}).json()
    for u in wrong_roles({"ops_analyst", "ops_supervisor"}):
        attempt("restriction removal (owner only)", u, "post", f"/api/restrictions/{hold['id']}/remove",
                {"reason": "Not needed", "authorization": "X-1"})

    rosa = case_id("Rosa Delgado")
    doc = as_user("bonneville.advisor").post(f"/api/cases/{rosa}/documents",
                                             data={"doc_type": "lpoa", "sample": "true"}).json()
    for u in wrong_roles({"ops_analyst", "ops_supervisor"}):
        attempt("document good-order check", u, "post", f"/api/documents/{doc['id']}/reject", {"reason": "Unsigned"})
    attempt("items received without operations", "bonneville.advisor", "post", f"/api/cases/{rosa}/corrections",
            {"fields": {"lpoa_signed": True}, "documents": {"lpoa_signed": doc["id"]}})

    users = as_user("admin.platform").get("/api/admin/users").json()
    target = next(u for u in users if u["username"] == "review.analyst")
    for u in wrong_roles({"platform_admin"}):
        attempt("role grant: request", u, "post", f"/api/admin/users/{target['id']}/role", {"role": "aml_compliance"})
    grant = as_user("admin.platform").post(f"/api/admin/users/{target['id']}/role", json={"role": "aml_compliance"}).json()
    attempt("role grant: requester approves own", "admin.platform", "post", f"/api/admin/grants/{grant['grant']['id']}/approve")

    # From the Oct. 13 walkthrough: only the onboarding team takes in applications, #sanctions is the sanctions
    # team's, and only the platform admin re-sends a held or failed email
    application = {"group": "pure_ria", "registration_type": "individual", "holders": [{"name": "Probe Person"}]}
    for u in ("ops.cip", "ops.service", "aml.maker", "rs.associate"):
        attempt("new application from outside onboarding", u, "post", "/api/portal/accounts",
                {"application": application, "documents": []})
    for u in wrong_roles({"sanctions"}):
        attempt("post in #sanctions", u, "post", "/api/channels/sanctions/messages", {"body": "Any update on #16?"})
    held = as_user("admin.platform").get("/api/outbox").json()["emails"][0]
    for u in wrong_roles({"platform_admin"}):
        attempt("email re-send", u, "post", f"/api/outbox/{held['id']}/retry")

    # Oct. 20: only the onboarding team reviews what the AI read from a package (accept, edit, or reject each value)
    ops = as_user("ops.analyst")
    detail = ops.post("/api/intake/extract", data={"text": ops.get("/api/intake/status").json()["sample_text"]}).json()
    first = detail["ai_suggestions"][0]["id"]
    for u in wrong_roles({"ops_analyst", "ops_supervisor"}) + ["ops.cip", "ops.service"]:
        attempt("AI suggestion review outside onboarding", u, "post",
                f"/api/cases/{detail['id']}/ai-suggestions/{first}", {"action": "accept"})
    for i, sug in enumerate(detail["ai_suggestions"]):
        body = {"action": "accept"}
        if sug["field"] == "ein":
            body = {"action": "edit", "value": "87-1234599", "reason": "Gate review: an edit is recorded with its reason"}
        r = ops.post(f"/api/cases/{detail['id']}/ai-suggestions/{sug['id']}", json=body)
        assert r.status_code == 200, r.text


def suggestion_tracking() -> dict:
    """Every suggestion the AI made is stored with what the reviewer did, by whom, and why (PRD guardrail 6)."""
    with SessionLocal() as db:
        rows = db.query(AiSuggestion).all()
        reviews = db.query(AuditEvent).filter(AuditEvent.action == "ai_suggestion_reviewed").count()
    reviewed = [r for r in rows if r.status != "pending"]
    return {"suggestions": len(rows), "reviewed": len(reviewed), "audited": reviews,
            "with_reviewer": sum(bool(r.reviewed_by_name and r.reviewed_at) for r in reviewed),
            "changes_with_reason": sum(bool(r.reason) for r in reviewed if r.status != "accepted"),
            "changes": sum(r.status != "accepted" for r in reviewed),
            "live": sum(r.mode == "live" for r in rows), "outcomes": dict(Counter(r.status for r in rows))}


# --------------------------------------------------------------------------- straight-through opening
def clean_applications(n_a: int = 12, n_b: int = 8) -> list[dict]:
    first = ["Avery", "Jordan", "Riley", "Quinn", "Harper", "Rowan", "Emerson", "Sage", "Parker", "Reese",
             "Skyler", "Dakota", "Logan", "Morgan", "Hayden", "Blair", "Cameron", "Drew", "Jamie", "Kendall"]
    out = []
    for i in range(n_a + n_b):
        advisor = "granite.advisor" if i < n_a else "bonneville.advisor"
        app_ = {"group": "pure_ria", "registration_type": "individual",
                "holders": [{"name": f"{first[i]} Gate{i:02d}", "role": "account_holder", "date_of_birth": f"19{60 + i}-0{1 + i % 9}-1{i % 9}",
                             "address": f"{100 + i} S State St, Salt Lake City, UT 84111", "tax_id": f"900-00-{3100 + i}",
                             "occupation": "Engineer"}],
                "account_purpose": "Long-term investing", "source_of_funds": "Salary", "source_of_wealth": "Salary and savings"}
        r = as_user(advisor).post("/api/portal/accounts", json={"application": app_, "documents": ACCOUNT_DOCS})
        assert r.status_code == 200, r.text
        d = as_user("ops.analyst").get(f"/api/cases/{r.json()['id']}").json()    # the portal sees none of this
        before = {"status": d["status"], "manual_steps": d["manual_steps"], "path": d["path"]}
        after = accept(d["id"]) if d["status"] == "PENDING_ACCEPTANCE" else d
        with SessionLocal() as db:
            case = db.get(Case, d["id"])
            nigo = case.nigo_count
        codes = [r["code"] for r in after["restrictions"] if r["active"]]
        out.append({"case": d["id"], "path": before["path"], "before_acceptance": before["status"],
                    "manual_steps": before["manual_steps"], "nigo": nigo, "final": after["status"], "codes": codes})
    return out


# --------------------------------------------------------------------------- metrics
def traceability() -> dict:
    with SessionLocal() as db:
        events = db.query(AuditEvent).filter(AuditEvent.action.in_(DECISION_EVENTS)).all()
        rows = []
        for e in events:
            d = e.detail or {}
            basis = d.get("basis") or []
            rows.append({"action": e.action, "approver": bool(e.actor_id and e.actor_role),
                         "rule_version": bool(d.get("policy_version")),
                         "citation": any(b.get("source") for b in basis)})
    n = len(rows)
    full = sum(r["approver"] and r["rule_version"] and r["citation"] for r in rows)
    return {"decisions": n, "with_approver": sum(r["approver"] for r in rows),
            "with_rule_version": sum(r["rule_version"] for r in rows),
            "with_citation": sum(r["citation"] for r in rows), "fully_traceable": full,
            "share": round(full / n, 3) if n else None, "by_action": dict(Counter(r["action"] for r in rows))}


def sla_adherence() -> dict:
    hours = QUEUES["sanctions_review"]["sla_hours"]
    out = []
    with SessionLocal() as db:
        for d in db.query(PendingDecision).filter(PendingDecision.decision_type == "sanctions",
                                                  PendingDecision.status == "approved"):
            entered = (db.query(CaseTransition).filter(CaseTransition.case_id == d.case_id,
                                                       CaseTransition.to_status == "PENDING_SANCTIONS")
                       .order_by(CaseTransition.at).first())
            if entered is None:
                continue
            taken = (d.decided_at - entered.at).total_seconds() / 3600
            out.append({"case": d.case_id, "hours": round(taken, 1), "within": taken <= hours})
    return {"sla_hours": hours, "escalations": len(out), "within": sum(o["within"] for o in out), "detail": out}


def build_health() -> dict:
    import shutil
    import subprocess
    if not shutil.which("gh"):
        return {"measured": False, "why": "GitHub CLI not available"}
    try:
        raw = subprocess.run(["gh", "api", "repos/shawngggg/AI-embedded-RIA-custody-operations-platform/actions/runs"
                              "?branch=main&event=push&per_page=50"], capture_output=True, text=True, timeout=60)
        runs = json.loads(raw.stdout).get("workflow_runs", [])
    except Exception as exc:  # noqa: BLE001
        return {"measured": False, "why": str(exc)}
    done = [r for r in runs if r.get("status") == "completed"]
    if not done:
        return {"measured": False, "why": raw.stderr.strip() or "no completed CI runs on main"}
    ok = [r for r in done if r.get("conclusion") == "success"]
    return {"measured": True, "runs": len(done), "passed": len(ok),
            "failed": [{"sha": r["head_sha"][:7], "title": r["display_title"][:70], "conclusion": r["conclusion"]}
                       for r in done if r.get("conclusion") != "success"]}


def nigo_test_set() -> dict:
    """The NIGO test set read by its answer key: proves the set and the rules agree before the model is measured."""
    from extraction import gate
    from nigo_eval import PACKAGES, answer_key, package_text, run
    by_text = {package_text(p): p for p in PACKAGES}
    out = run(lambda text: gate(answer_key(by_text[text]), text, "end_turn", "answer key"))
    return {k: out[k] for k in ("packages", "seeded", "caught", "false_nigo", "recall")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--check", action="store_true", help="exit 1 if a measured R1 target is missed")
    ap.add_argument("--no-github", action="store_true", help="skip the CI history lookup (build health)")
    args = ap.parse_args()
    with TestClient(app):                                # runs startup: migrations-free create + seed
        work_the_seeded_cases()
        stp = clean_applications()
    trace = traceability()
    refused = sum(a["refused"] for a in attempts)
    logged = sum(a["refused"] and a["logged"] for a in attempts)
    opened = [s for s in stp if s["final"] in ("OPEN", "OPEN_RESTRICTED")]
    stp_ok = [s for s in opened if s["manual_steps"] == 0 and s["nigo"] == 0]
    report = {
        "decision_traceability": trace,
        "straight_through": {"applications": len(stp), "opened": len(opened), "no_manual_step": len(stp_ok),
                             "share": round(len(stp_ok) / len(stp), 3),
                             "by_path": {p: {"apps": sum(s["path"] == p for s in stp),
                                             "stp": sum(s["path"] == p for s in stp_ok),
                                             "open_codes": dict(Counter(c for s in stp if s["path"] == p for c in s["codes"]))}
                                         for p in ("A", "B")}},
        "role_enforcement": {"attempts": len(attempts), "refused": refused, "refused_and_logged": logged,
                             "share": round(logged / len(attempts), 3) if attempts else None,
                             "not_refused": [a for a in attempts if not a["refused"]],
                             "not_logged": sorted({(a["decision"], a["role"], a["status"]) for a in attempts
                                                   if a["refused"] and not a["logged"]})},
        "escalation_sla": sla_adherence(),
        "build_health": {"measured": False, "why": "skipped"} if args.no_github else build_health(),
        "nigo_test_set": nigo_test_set(),
        "ai_suggestion_tracking": suggestion_tracking(),
        "not_measured": {
            "nigo_detection_recall": "Needs the live model: run Users and rules → AI evaluation in the deployed app",
            "ai_suggestion_acceptance": "Needs live values reviewed in the deployed app (New application, then review "
                                        "each value); the share shows on the dashboard and Users and rules → AI evaluation",
            "reviewer_walkthrough": "Needs a person: a first-time reviewer, timed from sign-in to open account"},
    }
    t, s, r = report["decision_traceability"], report["straight_through"], report["role_enforcement"]
    print("MVP gate review (synthetic data)\n")
    print(f"Decision traceability : {t['fully_traceable']}/{t['decisions']} decisions carry approver, rule version, and citation"
          f"  (approver {t['with_approver']}, rule version {t['with_rule_version']}, citation {t['with_citation']})")
    print(f"Straight-through      : {s['no_manual_step']}/{s['applications']} clean applications opened with no manual step "
          f"before acceptance  {s['by_path']}")
    print(f"Role enforcement      : {r['refused']}/{r['attempts']} wrong-role attempts refused; {r['refused_and_logged']} refused and logged")
    for item in r["not_logged"]:
        print(f"    not logged: {item}")
    e = report["escalation_sla"]
    print(f"Escalation SLA        : {e['within']}/{e['escalations']} sanctions escalations decided within {e['sla_hours']} hours")
    b = report["build_health"]
    print(f"Build health          : " + (f"{b['passed']}/{b['runs']} CI runs on main passed {b['failed']}" if b["measured"] else f"not measured ({b['why']})"))
    n = report["nigo_test_set"]
    print(f"NIGO test set         : {n['packages']} packages, {n['seeded']} seeded defects; the answer key flags "
          f"{n['caught']}/{n['seeded']} with {n['false_nigo']} false NIGO (checks the set, not the model)")
    a = report["ai_suggestion_tracking"]
    print(f"AI suggestion tracking: {a['reviewed']}/{a['suggestions']} sample values reviewed and recorded with the reviewer "
          f"({a['with_reviewer']}), {a['audited']} audit events, {a['changes_with_reason']}/{a['changes']} changes with a reason "
          f"(illustrative; {a['live']} live)")
    for k, v in report["not_measured"].items():
        print(f"Not measured          : {k}: {v}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str))
    if args.check:
        missed = [name for name, ok in (
            ("decision traceability 100%", t["share"] == 1.0),
            ("straight-through opening 70% or higher", s["share"] >= 0.70),
            ("role enforcement 100% refused and logged", r["share"] == 1.0),
            ("NIGO test set consistent with its answer key", n["recall"] == 1.0 and n["false_nigo"] == 0),
            ("every AI suggestion review recorded with the reviewer and audited",
             a["reviewed"] == a["suggestions"] == a["with_reviewer"] == a["audited"] > 0
             and a["changes_with_reason"] == a["changes"])) if not ok]
        print("\nGate check: " + ("all measured targets met" if not missed else "MISSED: " + "; ".join(missed)))
        sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()
