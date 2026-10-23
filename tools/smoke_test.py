"""
Smoke test the live site after a deploy: the new revision is serving, the screens load, each kind of user gets the
right access, the portal shows no custodian operations, and live AI is on. Reads only; it changes no demo data
(demo sign-ins are logged, nothing else).

    python tools/smoke_test.py https://ria-custody-1638463558.us-central1.run.app [--revision ria-custody-00009-abc]

Exits 1 if any check fails. In GitHub Actions it also writes the results to the job summary.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

PORTAL_STATUSES = {"PENDING_ITEMS", "RETURNED", "OPEN", "OPEN_RESTRICTED", "APPLIED", "REDIRECTED", "DECLINED",
                   "CONFIRMING", "IN_REVIEW", "SUBMITTED",
                   "COMPLETED", "BANK_RETURNED", "IN_TRANSIT"}         # release 2 transfers, in the portal's words
INTERNAL_KEYS = {"result", "decisions", "decision_log", "restrictions", "queue", "sla", "path", "application"}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}", flush=True)
    if not ok and os.environ.get("GITHUB_ACTIONS"):
        print(f"::error title=Smoke test failed::{name}{': ' + detail if detail else ''}", flush=True)


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.opener.open(req, timeout=60) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def get(self, path):
        return self.call("GET", path)

    def sign_in(self, username: str) -> "Session":
        status, _ = self.call("POST", "/api/auth/demo", {"username": username})
        check(f"demo sign-in as {username}", status == 200, f"status {status}")
        return self


def wait_until_up(base: str, seconds: int = 120) -> dict:
    """Cloud Run scales to zero, so the first request may wait for a cold start and Neon to resume."""
    deadline = time.time() + seconds
    while True:
        status, body = Session(base).get("/api/health")
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            return body
        if time.time() > deadline:
            raise SystemExit(f"FAIL  the site didn't come up within {seconds}s (last status {status})")
        time.sleep(5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--revision", help="the Cloud Run revision the deploy just created")
    args = ap.parse_args()

    health = wait_until_up(args.base)
    check("health", True, f"revision {health.get('revision')}, seed {health.get('seed')}")
    if args.revision:
        check("the new revision is serving", health.get("revision") == args.revision,
              f"expected {args.revision}, got {health.get('revision')}")
    status, page = Session(args.base).get("/")
    check("the screens load", status == 200 and '<div id="root">' in str(page), f"status {status}")

    ops = Session(args.base).sign_in("ops.analyst")
    status, work = ops.get("/api/work")
    check("onboarding analyst: Work page", status == 200 and isinstance(work, dict) and "needs_you" in work)
    status, queues = ops.get("/api/queues")
    keys = {q["key"] for q in queues.get("queues", [])} if isinstance(queues, dict) else set()
    check("onboarding analyst: only onboarding's queues", keys == {"ai_review", "items_requested", "rework"},
          str(sorted(keys)))
    check("onboarding analyst: no approvals", ops.get("/api/decisions")[0] == 403)

    admin = Session(args.base).sign_in("admin.platform")
    check("platform admin: no case files", admin.get("/api/cases")[0] == 403)
    status, ev = admin.get("/api/ai-evaluation")
    ev = ev if isinstance(ev, dict) else {}
    why = ev.get("why_not") or ""
    # Live AI is on when a run is allowed, or when only today's call cap stands in the way
    check("live AI is configured", status == 200 and (bool(ev.get("can_run")) or "cap" in why), why)
    latest = ev.get("latest")
    if latest:
        check("latest AI evaluation meets the NIGO recall target", bool(latest.get("met")),
              f"recall {latest.get('recall')}, false NIGO {latest.get('false_nigo')}, prompt {latest.get('prompt_version')}")
    live = (ev.get("suggestions") or {}).get("live")
    check("AI suggestion acceptance is tracked", isinstance(live, dict) and "share_accepted" in live,
          f"{live['accepted']} of {live['reviewed']} live values accepted unchanged, {live['pending']} waiting"
          if isinstance(live, dict) else "no suggestion summary")
    status, wt = ops.get("/api/walkthrough/summary")
    wt = wt if isinstance(wt, dict) else {}
    check("reviewer walkthrough is timed", status == 200 and "first_time_runs" in wt,
          f"{wt.get('first_time_runs')} first-time runs, median {wt.get('median_seconds')}s, "
          f"{wt.get('under_target')} under {wt.get('target_seconds')}s")
    sup = Session(args.base).sign_in("ops.supervisor")
    status, st = sup.get("/api/settlement")
    st = st if isinstance(st, dict) else {}
    checks = st.get("checks", [])
    check("release 2 ledger: client accounts, RIA masters, and the general ledger agree",
          status == 200 and len(checks) == 3 and all(c.get("ok") for c in checks),
          f"business day {st.get('as_of')}, last cycle {(st.get('last_run') or {}).get('business_date')}")
    status, risk = admin.get("/api/risk-events")
    areas = {e["area"] for e in risk.get("events", [])} if isinstance(risk, dict) else set()
    check("platform admin: only access and authentication risk events", areas <= {"access"}, str(sorted(areas)))

    rias = Session(args.base).sign_in("granite.advisor")
    status, cases = rias.get("/api/cases")
    cases = cases if isinstance(cases, list) else []
    check("RIA user: sees the firm's requests", status == 200 and len(cases) > 0, f"{len(cases)} requests")
    leaks = [c["id"] for c in cases if c.get("status") not in PORTAL_STATUSES or INTERNAL_KEYS & set(c)]
    if cases:
        status, one = rias.get(f"/api/cases/{cases[0]['id']}")
        if isinstance(one, dict) and (INTERNAL_KEYS & set(one) or one.get("status") not in PORTAL_STATUSES):
            leaks.append(cases[0]["id"])
        check("RIA user: no case audit trail", rias.get(f"/api/cases/{cases[0]['id']}/audit")[0] == 403)
    check("RIA user: no custodian operations in requests", not leaks, f"leaking requests {leaks}" if leaks else "")
    status, tr = rias.get("/api/money/transfers")
    tr = tr if isinstance(tr, list) else []
    firms = {r.get("account") for r in tr}
    status_a, mine = rias.get("/api/money/accounts")
    allowed = {a["id"] for a in mine} if isinstance(mine, list) else set()
    check("RIA user: money movement shows only the firm's accounts", status == 200 and firms <= allowed,
          f"{len(tr)} transfers on {len(firms)} accounts")
    for path in ("/api/queues", "/api/risk-events", "/api/admin/rules", "/api/restrictions/accounts",
                 "/api/settlement", "/api/recon"):
        check(f"RIA user: {path} refused", rias.get(path)[0] in (403, 404))

    failed = [r for r in results if not r[1]]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Live smoke test: {len(results) - len(failed)} of {len(results)} checks passed\n\n"
                    "| Check | Result | Detail |\n| --- | --- | --- |\n")
            f.writelines(f"| {n} | {'pass' if ok else '**FAIL**'} | {d} |\n" for n, ok, d in results)
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
