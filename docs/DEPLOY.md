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

### Live AI without an API key: workload identity federation (recommended)

Instead of storing an API key, the service proves who it is with a Google-signed identity token, and Anthropic
exchanges it for a Claude API token that lasts minutes. Nothing secret is stored, and only this service can get a
token. The app uses federation whenever `ANTHROPIC_FEDERATION_RULE_ID` and `ANTHROPIC_ORGANIZATION_ID` are set
([Anthropic's guide](https://platform.claude.com/docs/en/manage-claude/wif-providers/gcp)).

1. Give the service its own service account (federation doesn't accept the default compute account), and let it
   read the two secrets:

   ```bash
   gcloud iam service-accounts create ria-custody-run --display-name="RIA custody Cloud Run service"
   SA=ria-custody-run@$(gcloud config get-value project).iam.gserviceaccount.com
   for s in ria-database-url ria-secret-key; do
     gcloud secrets add-iam-policy-binding $s --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
   done
   gcloud iam service-accounts describe $SA --format='value(uniqueId)'    # needed in step 2
   ```

2. In the Claude Console, open **Settings → Workload identity → Connect workload → Google Cloud**. The issuer is
   `https://accounts.google.com`. In the federation rule, set the audience to `https://api.anthropic.com` and match
   both claims: `sub` = the unique ID from step 1, `email` = the service account address. Note the four IDs the
   wizard shows: federation rule, organization, service account, and workspace.

3. Deploy with the service account and the four IDs. They're identifiers, not secrets, so they go in plain
   environment variables, and `ANTHROPIC_API_KEY` is left out:

   ```bash
   gcloud run deploy ria-custody --source . --region us-central1 --allow-unauthenticated \
     --service-account $SA \
     --min-instances 0 --max-instances 1 --cpu 1 --memory 512Mi --concurrency 40 --timeout 60 \
     --set-env-vars COOKIE_SECURE=true,DEMO_MODE=true,AI_DAILY_CAP=50,ANTHROPIC_FEDERATION_RULE_ID=...,ANTHROPIC_ORGANIZATION_ID=...,ANTHROPIC_SERVICE_ACCOUNT_ID=...,ANTHROPIC_WORKSPACE_ID=... \
     --set-secrets DATABASE_URL=ria-database-url:latest,SECRET_KEY=ria-secret-key:latest
   ```

The workbench's **New application** page says how live AI signs in ("signed in by workload identity federation").

Then sign in as the platform administrator and open **Users and rules → AI evaluation → Run the evaluation**. It reads
the 20 test packages with the live model (20 calls, about a cent with Claude Haiku 5.5, counted against
`AI_DAILY_CAP`) and records NIGO detection recall and false NIGOs: the MVP gate's measure.

The other AI measure is how often reviewers accept the AI's values unchanged. Sign in as the onboarding analyst, open
**New application**, load a synthetic test package (or paste your own), and choose **Read and open a case**. The case
waits in the AI review queue: accept, edit, or reject each value. The share shows on the operations dashboard and on
**Users and rules → AI evaluation**.

### Email through Microsoft 365 (Outlook)

Every notice the platform sends (items requested, account opened, application declined, change applied) is written to
the **Email outbox** first. With no provider set, it stays there. To send through the custodian's Microsoft 365, the
app calls Microsoft Graph (`POST /users/{sender}/sendMail`) from one shared mailbox. Demo mode holds every message
whatever the provider, because the seeded recipients are synthetic.

1. **Shared mailbox.** In the Exchange admin center, create a shared mailbox to send from, for example
   `notices@yourdomain.com`. A shared mailbox needs no license.

2. **App registration.** In the Microsoft Entra admin center, open **App registrations → New registration** (single
   tenant). Note the application (client) ID and the directory (tenant) ID, and from **Enterprise applications** the
   app's object ID.

3. **Let it send from that mailbox only.** Don't add `Mail.Send` under the app's API permissions: granted there, it
   lets the app send as any mailbox in the tenant. Grant it in Exchange Online instead, scoped to the one mailbox
   ([RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)):

   ```powershell
   Connect-ExchangeOnline
   New-ServicePrincipal -AppId <client-id> -ObjectId <enterprise-app-object-id> -DisplayName "RIA custody notices"
   New-ManagementScope -Name "RIA custody notices" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'notices@yourdomain.com'"
   New-ManagementRoleAssignment -App <enterprise-app-object-id> -Role "Application Mail.Send" -CustomResourceScope "RIA custody notices"
   ```

4. **Sign-in without a secret (recommended).** On the app registration, open **Certificates & secrets → Federated
   credentials → Add credential → Other issuer**. Issuer `https://accounts.google.com`, subject identifier the
   unique ID of the Cloud Run service account (the same `ria-custody-run` account and ID as the Anthropic setup above),
   audience `api://AzureADTokenExchange`
   ([Microsoft's guide](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust)).
   The service then presents its Google identity token instead of a stored secret. If you'd rather use a client
   secret, create one under **Client secrets** and store it in Secret Manager with `read -s`, as in step 3.

5. **Deploy with the mail settings.** Add these to `--set-env-vars` (they're identifiers, not secrets). While
   testing, `MAIL_ALLOWED_DOMAINS` keeps the app from emailing anyone outside your own domain:

   ```
   MAIL_PROVIDER=m365,MS_TENANT_ID=<tenant-id>,MS_CLIENT_ID=<client-id>,MS_FEDERATED=true,MAIL_SENDER=notices@yourdomain.com,MAIL_ALLOWED_DOMAINS=yourdomain.com
   ```

   With a client secret instead, leave out `MS_FEDERATED` and add `--set-secrets ...,MS_CLIENT_SECRET=ria-ms-client-secret:latest`.
   Real mail goes out only with `DEMO_MODE=false`, so try it on a separate non-demo deployment with your own
   address as a firm user.

The **Email outbox** page shows the connection, and each message's status: held (and why), sent, or failed with
Graph's error. A platform administrator can re-send a failed message once the cause is fixed.

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
| `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID`, `ANTHROPIC_WORKSPACE_ID` | none | Turn on live AI through workload identity federation, with no key (preferred on Cloud Run) |
| `AI_DAILY_CAP` | `50` | Live AI reads per day, across all users |
| `RIA_EXTRACTION_MODEL` | `claude-haiku-5-5` | Model for live extraction |
| `SESSION_HOURS` | `8` | Session length |
| `MAIL_PROVIDER` | `outbox` | `m365` sends notices through Microsoft 365; `outbox` keeps them in the outbox only |
| `MS_TENANT_ID`, `MS_CLIENT_ID` | none | The Entra app registration that sends mail |
| `MS_FEDERATED` | `false` | `true` signs in to Entra with the service's Google identity token (no secret) |
| `MS_CLIENT_SECRET` | none | The app's client secret, if not federated (from Secret Manager) |
| `MAIL_SENDER` | none | The mailbox notices are sent from |
| `MAIL_ALLOWED_DOMAINS` | none | Comma-separated domains a live provider may send to; empty means any |
| `PORTAL_URL` | the demo's portal URL | Base of the portal links in notices |

## Updating

Merging a pull request into `main` deploys it (next section). By hand, run `gcloud run deploy ria-custody --source .
--region us-central1` from the repository: with no other flags, the service keeps its identity, live AI settings,
secrets, and scaling. Data in Neon survives deploys.

In demo mode the app fingerprints its seed data. When a deploy brings different seed data, it resets the demo on
startup, so nobody has to remember to. A platform administrator can also reset it any time with **Users and rules →
Reset the demo**. Either way, the AI evaluation runs and the reviewed AI suggestions are kept: they're governance
evidence, not demo data.

## Automatic deploys from GitHub

`.github/workflows/deploy.yml` deploys to Cloud Run once CI passes on `main`, then runs `tools/smoke_test.py`
against the live site: the new revision is serving, the screens load, each kind of user gets the right access, the
portal shows no custodian operations, and live AI is on. The results are on the run's summary page in the Actions
tab.

GitHub signs in to Google Cloud with workload identity federation, so no key is stored in GitHub. Only this
repository's `main` branch is accepted, and the deployer can do only what a deploy needs. Run this once in Cloud Shell:

```bash
PROJECT=ria-custody-demo
PROJECT_NUMBER=1638463558
REPO=shawngggg/AI-embedded-RIA-custody-operations-platform
REPO_ID=1405013520          # the repository's numeric id: it doesn't change if the repository is renamed

# 1. Trust GitHub Actions tokens from this repository's main branch only
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool=github \
  --display-name="GitHub" --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository_id=='$REPO_ID' && assertion.ref=='refs/heads/main'"

# 2. A deployer identity with what a deploy from source needs
gcloud iam service-accounts create ria-custody-deployer --display-name="GitHub deployer for ria-custody"
DEPLOYER=ria-custody-deployer@$PROJECT.iam.gserviceaccount.com
for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin \
            roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$DEPLOYER" --role=$role --condition=None
done
# It deploys the service as its own identity, and its builds run as the default compute account
for sa in ria-custody-run@$PROJECT.iam.gserviceaccount.com $PROJECT_NUMBER-compute@developer.gserviceaccount.com; do
  gcloud iam service-accounts add-iam-policy-binding $sa --member="serviceAccount:$DEPLOYER" \
    --role=roles/iam.serviceAccountUser
done

# 3. Only this repository may act as the deployer
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
```

New grants can take a minute or two to reach Google's servers: a deploy right after setup can fail with
`PERMISSION_DENIED` and pass when re-run (**Re-run jobs** on the run's page). If a deploy fails on a permission
after that, the run's annotation names it; grant that one role to the deployer and re-run.
