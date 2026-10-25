# Project brief for Claude

Shawn Ghodrati's AI-embedded RIA custody operations platform: a portfolio product showing process mapping, operations
excellence, controls, and AI enablement across the RIA custody lifecycle. Shawn owns the process design, the PRD, and
every review call; Claude writes the code to the PRD and the process maps. Read `PRD.md` and `README.md` first.

Live demo: https://ria-custody-1638463558.us-central1.run.app/ (Cloud Run, project `ria-custody-demo`, Neon Postgres).
Process maps: https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/

## Standing rules

- Synthetic data only. Keep former employers' confidential details (volumes, headcounts, vendors, AUM) out of code,
  docs, and copy. "Better to be right than fake": don't invent facts, and say plainly what isn't measured or built.
- Secrets never go in the chat or the repo. Shawn enters them in Cloud Shell with `read -s` into Secret Manager.
  Identifiers (project number, service account emails, federation rule ids) aren't secrets.
- Work on the `mvp` branch. Commit and push when asked or as the established workflow; then open a pull request to
  `main` (the REST API works: `gh api repos/shawngggg/ai-embedded-ria-custody-operations-platform/pulls -f ...`;
  `gh pr` uses GraphQL, which is blocked here). Shawn reviews and merges. End commit messages and PR bodies with the
  attribution lines the session provides.
- Merging deploys: `.github/workflows/deploy.yml` deploys after CI passes on `main` and smoke tests the live site.
  Read the result with `gh api repos/shawngggg/ai-embedded-ria-custody-operations-platform/actions/runs?branch=main`;
  a failed deploy or smoke check writes its error as an annotation: `gh api repos/.../check-runs/<job id>/annotations`
  (full job logs live on a host the sandbox can't reach, and so does the live site). Re-run a failed run with
  `gh api -X POST repos/.../actions/runs/<run id>/rerun-failed-jobs`.
- Shawn's walkthrough notes arrive as GitHub issues (template: walkthrough note) or in chat. Batch related notes into
  one pull request, and say which issues it closes.

## Layout

- `onboarding/`: the decision engine (rules, restriction codes, flagged list, risk, transferability, AI extraction
  behind the controls gate, the NIGO test set). Every module is also loaded by the browser console in `docs/console/`:
  after changing one, run `python tools/sync_console.py`.
- `custody/`: the release 2 engine (pure Python, no database): `ledger.py` (three layers, the rollup check),
  `money.py` (map 08 transfers, the simulated rails), `settlement.py`, `recon.py`, `policy.py` (every threshold as a
  dated rule with its source), and the engines not yet in the app (`entitlements.py`, `products.py`, `lifecycle.py`).
  A workflow is a function of the case's request, approved decisions, and recorded events; `app/servicing.py` replays
  it and posts what's missing (each posting has a stable ref). The later queues stay hidden while
  `LATER_ENABLED = False` in `app/queues.py`.
- `app/`: FastAPI, SQLAlchemy, Alembic (`migrations/`). `app/access.py` is the capability model; menus and the API
  use the same answers. `app/portal_view.py` is everything RIA users and clients see of a case.
  `app/ai_suggestions.py` stores each value the AI read and what the reviewer did (the acceptance metric).
- `app/ledger.py` stores postings; `app/settlement.py` runs the daily cycle; `app/recon.py` reconciles;
  `app/seed_r2.py` seeds the books (opening balances, incoming ACATS, 18 transfer scenarios, receipts, breaks);
  `app/routers/money.py` is the transfers, settlement, recon, and ledger API.
- `synthetic/`: synthetic data. `cases.py` adds seeded cases across every queue; `book.py` builds the release 3 book
  (trading and billing) on the ledger's positions, served read-only by `app/routers/preview.py` (the seed also uses its
  funding rows); `python -m synthetic.export` writes everything, ledger postings included, to `synthetic/data/` (rerun
  it after a seed change). The image copies `synthetic/` (the seed imports it).
- `web/`: React and TypeScript (Vite). `npm run build` in `web/`.
- `tools/gate_review.py`: the MVP gate metrics (CI runs `--check --no-github`). `tools/smoke_test.py`: live checks.

## Before pushing

```
python -m pytest onboarding -q
python -m pytest custody -q
python -m pytest app/tests -q                     # also on Postgres: TEST_DATABASE_URL=postgresql+psycopg://...
python tools/gate_review.py --check --no-github
cd web && npx tsc --noEmit && npm run build
```

A schema change needs an Alembic migration (autogenerate against Postgres, name constraints the way Postgres does,
then `alembic upgrade head` and `alembic check`). Seed data changes reseed the live demo on the next deploy by
themselves (the seed fingerprint); AI evaluation runs survive. On Postgres, `seed_database()` builds the seed in a
local SQLite file and bulk-copies it across (Neon is a network hop away; the seed is about 5,600 statements, the copy
about 140), and the Postgres tests take the same path: keep the seed free of Postgres-only SQL.

## Access model (what each person sees)

- Menus and API by role and team (`capabilities()`); a refused request is logged (`audit.refuse`).
- Operations teams: onboarding (intake), CIP, client service (account maintenance), asset transfer (Money movement:
  requests by mail or from a receiving firm, callbacks, fixes, release under maker-checker; ops.transfers makes,
  ops.money checks, the supervisor checks above $50,000), settlement (ops.settlement: the cycle, receipts, recon; trade
  and asset transfer read it), and teams whose workbench comes in release 3 (trade, billing): planned page, channel,
  restricted accounts, area risk events.
- Risk events belong to an area; each person sees their area's (the platform admin: access and authentication only).
- The platform admin has no case files. The portal sees no custodian operations: every review state is "In review".
- Every team that works with accounts sees a case's decisions, sanctions determinations included. Every review,
  accept, or reject option links its documents (`DocLinks` in `web/src/docs.tsx`), the Approvals list included.
- The principal ticks every document in good order as reviewed before Accept (`acceptance.to_review`; the server
  refuses an acceptance that leaves one out) and can send the case back to operations with a reason
  (`/send-back`: rework, no RIA notice). Every internal document open is logged (`document_opened`).
- A package read by AI waits for onboarding (firm and Form ADV facts) and CIP (identity facts) to accept, edit, or
  reject each value before the rules run; a licensed principal accepts at the end. The acceptance metric counts live
  values only; reviewed values survive a demo reset.
- Every screening alert goes to compliance: sanctions hits to the sanctions team, PEP and adverse-media alerts and
  EDD to AML compliance (a PEP alert the rating doesn't send to EDD waits for AML compliance to clear it or require EDD).
- A failed identity check (`app/identity_check.py`): insufficient information or the wrong document goes back to the
  RIA to resubmit; a suspected fraudulent identity is held (IDFR) for AML compliance. Never show a fraud suspicion to
  the portal: no note, document review note, notice, or status beyond "In review".
- Money leaves only under maker-checker and the controls gate (MM-005, MM-006). The portal sees transfers in its own
  words (`portal_view.KIND_STATUS`) and balances at the client layer only: never the master, the GL, a risk review, or
  a sanctions hold.
- The release 3 pages (trading, billing) open with a read-only preview of the synthetic book: operations sees its
  area; an RIA its own firm; a client their own account. The portal never gets a restriction code or a fail.
- The timed walkthrough (`app/walkthrough.py`): Start the clock on the sign-in page; the server times a fresh case from
  Start to open account. Finished runs, like AI evaluation runs, survive a demo reset.

## Docs to keep current with each change

`README.md`, `PRD.md`, and the PRD doc (Claude Doc "PRD: AI-Embedded RIA Custody Operations Platform",
claude.ai/artifact/8ePrFa1NJ43rXEi2YJaPsj), `docs/DEPLOY.md`, and the build tracker
(`/mnt/user-data/outputs/ria-custody-coached-build-tracker.xlsx` when it's available in the session).
Writing style: plain words, short sentences, specific numbers.
