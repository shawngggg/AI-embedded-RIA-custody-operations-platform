# Deploying the MVP: Google Cloud Run + Neon

The plan is to run at no cost. The app runs on Google Cloud Run and scales to zero when nobody is using it.
The database is Neon's free Postgres. Live AI reads are capped at 50 a day.

You create three accounts yourself: Google Cloud (with billing turned on, which Cloud Run requires),
Neon, and optionally Anthropic for live AI. Without an Anthropic key, the app still runs. AI intake then
uses the built-in sample package and its illustrative, hand-written model response.

## What it costs

| Piece | Plan | Why it stays at $0 for a demo |
|---|---|---|
| Cloud Run | Request-based billing, minimum 0 instances, maximum 1 | The free tier covers 2 million requests, 180,000 vCPU-seconds, and 360,000 GiB-seconds a month ([pricing](https://cloud.google.com/run/pricing)). An idle service costs nothing. |
| Neon | Free | 100 compute-hours and 1 GB of storage per project a month. The database suspends after 5 idle minutes ([limits](https://neon.com/faqs/free-plan-limits-and-quotas)). |
| Artifact Registry | Cleanup policy keeps the latest image only | One image is well within the free storage. |
| Secret Manager | Three secrets | The free tier covers 6 active secret versions and 10,000 reads a month ([pricing](https://cloud.google.com/secret-manager/pricing)). Each cold start reads the three secrets once. |
| Anthropic (optional) | Pay as you go, with `AI_DAILY_CAP=50` | Claude Haiku 5.5 costs about $0.001 per package read, so at most about $1–2 a month. Also set a spend limit in the Anthropic Console. |

Free tiers change. Check the linked pages before you deploy. A budget alert (step 6) emails you if
anything starts to cost money. It notifies you; it doesn't stop spending.

## 1. Neon: create the database

1. Sign up at [neon.com](https://neon.com) and create a project named `ria-custody`. Pick a US region,
   for example AWS US East (Ohio), close to Cloud Run's `us-central1`.
2. Open **Connect**, turn **Connection pooling** off, and copy the direct connection string. It looks
   like `postgresql://USER:PASSWORD@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require`.

With one app instance, a direct connection is simplest. The app reconnects after Neon suspends.

## 2. Google Cloud: project and APIs

Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install), then:

```bash
gcloud auth login
gcloud projects create ria-custody-demo-<suffix>      # any globally unique id
gcloud config set project ria-custody-demo-<suffix>
gcloud billing projects link ria-custody-demo-<suffix> --billing-account=<BILLING_ACCOUNT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com billingbudgets.googleapis.com
```

`gcloud billing accounts list` shows your billing account id.

## 3. Secrets

```bash
printf '%s' 'postgresql://USER:PASSWORD@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require' \
  | gcloud secrets create ria-database-url --data-file=-
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create ria-secret-key --data-file=-
printf '%s' 'sk-ant-...' | gcloud secrets create ria-anthropic-key --data-file=-   # optional

PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
for s in ria-database-url ria-secret-key ria-anthropic-key; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

## 4. Deploy

From the repository root:

```bash
gcloud run deploy ria-custody --source . --region us-central1 \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 \
  --cpu 1 --memory 512Mi --concurrency 40 --timeout 60 \
  --set-env-vars COOKIE_SECURE=true,DEMO_MODE=true,AI_DAILY_CAP=50 \
  --set-secrets DATABASE_URL=ria-database-url:latest,SECRET_KEY=ria-secret-key:latest,ANTHROPIC_API_KEY=ria-anthropic-key:latest
```

Leave out `ANTHROPIC_API_KEY=...` if you skipped that secret.

Cloud Build builds the `Dockerfile` (the React screens, then the Python app). On startup, the container
applies database migrations and seeds the demo data into the empty database. `--max-instances 1` keeps
migrations from racing and keeps the AI cap exact.

The startup check refuses to run with `COOKIE_SECURE=true` and the development `SECRET_KEY`, so a
missing secret fails loudly instead of signing sessions with a public key.

## 5. Keep only the latest image

```bash
cat > cleanup.json <<'EOF'
[
  {"name": "keep-latest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 1}},
  {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"tagState": "any"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location=us-central1 --policy=cleanup.json --no-dry-run
```

Cleanup runs in the background, about once a day.

## 6. Budget alert

```bash
gcloud billing budgets create --billing-account=<BILLING_ACCOUNT_ID> \
  --display-name="RIA custody demo" --budget-amount=5USD \
  --threshold-rule=percent=0.5 --threshold-rule=percent=1.0
```

Alerts go to the billing account's administrators by email.

## 7. Check it

```bash
URL=$(gcloud run services describe ria-custody --region us-central1 --format='value(status.url)')
curl -s $URL/api/health
```

Open the URL and sign in as a demo user. The first request after an idle period takes a few seconds,
because Cloud Run starts the container and Neon resumes the database.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | SQLite file in `data/` | Postgres connection string in production |
| `SECRET_KEY` | development key | Signs session cookies. Required when `COOKIE_SECURE=true` |
| `COOKIE_SECURE` | `false` | `true` behind HTTPS (Cloud Run) |
| `DEMO_MODE` | `true` | Shows demo sign-in by role and allows the demo reset |
| `SEED_ON_START` | `true` | Seeds an empty database on startup |
| `ANTHROPIC_API_KEY` | none | Turns on live AI extraction |
| `AI_DAILY_CAP` | `50` | Live AI reads per day, across all users |
| `RIA_EXTRACTION_MODEL` | `claude-haiku-5-5` | Model for live extraction |
| `SESSION_HOURS` | `8` | Session length |

## Updating

Run the same `gcloud run deploy` command again. Data in Neon survives deploys. A platform administrator
can put the demo back to its starting point with **Users and rules → Reset the demo**.
