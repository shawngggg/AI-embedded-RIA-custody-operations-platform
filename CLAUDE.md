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
- `app/`: FastAPI, SQLAlchemy, Alembic (`migrations/`). `app/access.py` is the capability model; menus and the API
  use the same answers. `app/portal_view.py` is everything RIA users and clients see of a case.
  `app/ai_suggestions.py` stores each value the AI read and what the reviewer did (the acceptance metric).
- `web/`: React and TypeScript (Vite). `npm run build` in `web/`.
- `tools/gate_review.py`: the MVP gate metrics (CI runs `--check --no-github`). `tools/smoke_test.py`: live checks.

## Before pushing

```
python -m pytest onboarding -q
python -m pytest app/tests -q                     # also on Postgres: TEST_DATABASE_URL=postgresql+psycopg://...
python tools/gate_review.py --check --no-github
cd web && npx tsc --noEmit && npm run build
```

A schema change needs an Alembic migration (autogenerate against Postgres, name constraints the way Postgres does,
then `alembic upgrade head` and `alembic check`). Seed data changes reseed the live demo on the next deploy by
themselves (the seed fingerprint); AI evaluation runs survive.

## Access model (what each person sees)

- Menus and API by role and team (`capabilities()`); a refused request is logged (`audit.refuse`).
- Operations teams: onboarding (intake), CIP, client service (account maintenance), and teams whose workbench comes in
  a later release (asset transfer, trade, billing): planned page, channel, restricted accounts, area risk events.
- Risk events belong to an area; each person sees their area's (the platform admin: access and authentication only).
- The platform admin has no case files. The portal sees no custodian operations: every review state is "In review".
- Every team that works with accounts sees a case's decisions, sanctions determinations included.
- A package read by AI waits for onboarding (firm and Form ADV facts) and CIP (identity facts) to accept, edit, or
  reject each value before the rules run; a licensed principal accepts at the end. The acceptance metric counts live
  values only; reviewed values survive a demo reset.
- Every screening alert goes to compliance: sanctions hits to the sanctions team, PEP and adverse-media alerts and
  EDD to AML compliance (a PEP alert the rating doesn't send to EDD waits for AML compliance to clear it or require EDD).

## Docs to keep current with each change

`README.md`, `PRD.md`, and the PRD doc (Claude Doc "PRD: AI-Embedded RIA Custody Operations Platform",
claude.ai/artifact/8ePrFa1NJ43rXEi2YJaPsj), `docs/DEPLOY.md`, and the build tracker
(`/mnt/user-data/outputs/ria-custody-coached-build-tracker.xlsx` when it's available in the session).
Writing style: plain words, short sentences, specific numbers.
