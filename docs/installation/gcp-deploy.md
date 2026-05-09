# GCP Demo SaaS Deploy

Operator runbook for deploying the TrustedOSS Portal Demo SaaS on Google
Cloud Platform. Reproducible via the `terraform/` module at the repository
root.

The Demo SaaS is the public, low-cost (<$50/month idle) showcase
deployment. It is **not** intended for customer production data —
production deploys go through the Helm chart instead.

## 1. Prerequisites

- A GCP **project** with billing enabled. The project must be empty —
  Terraform will create the VPC, Cloud Run services, Cloud SQL, Memorystore,
  and IAM bindings.
- `gcloud` CLI authenticated as a user (or service account) with the
  Editor / Owner role on the project.
- `terraform` 1.7+ on the workstation. Use `tfenv` if you maintain
  multiple versions.
- `docker` for building + pushing the backend / frontend images.

Enable the required APIs once (Terraform also enables them but doing this
up front shortens the first apply):

```sh
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  vpcaccess.googleapis.com \
  secretmanager.googleapis.com \
  servicenetworking.googleapis.com \
  compute.googleapis.com \
  artifactregistry.googleapis.com
```

## 2. Configure variables

```sh
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Edit `terraform/terraform.tfvars` and fill in:

| Variable          | How to generate                                                  |
| ----------------- | ---------------------------------------------------------------- |
| `project_id`      | GCP project ID (e.g. `my-trustedoss-demo`)                       |
| `db_password`     | `openssl rand -base64 24 \| tr -d '=+/' \| cut -c1-24`           |
| `app_secret_key`  | `openssl rand -hex 32`                                           |
| `backend_image`   | Artifact Registry path + tag (filled in step 4 once images exist) |
| `frontend_image`  | Same                                                             |

`terraform.tfvars` is git-ignored. Never commit it.

## 3. Initialize + apply

Create the GCS state bucket and initialize:

```sh
gsutil mb -p "$PROJECT_ID" -l us-central1 "gs://$PROJECT_ID-tfstate"
gsutil versioning set on "gs://$PROJECT_ID-tfstate"

terraform -chdir=terraform init \
  -backend-config="bucket=$PROJECT_ID-tfstate"
```

Plan + apply:

```sh
terraform -chdir=terraform plan -out=demo.tfplan
terraform -chdir=terraform apply demo.tfplan
```

First apply takes ~12 minutes (Cloud SQL is the long pole at ~8 min). Note
the outputs:

- `backend_service_url`  — Cloud Run backend HTTPS URL
- `frontend_service_url` — Cloud Run frontend HTTPS URL
- `cloud_sql_connection_name` — for the seed step

## 4. Build + push images

Once the Artifact Registry repo exists (step 3 creates it), build the
backend + frontend images and push them. The exact commands live in
`docs-site/docs/operations/release.md`; here is the short form:

```sh
gcloud auth configure-docker us-central1-docker.pkg.dev

docker build -t "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/backend:2.0.0-rc1" \
  -f apps/backend/Dockerfile .
docker push "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/backend:2.0.0-rc1"

docker build -t "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/frontend:2.0.0-rc1" \
  -f apps/frontend/Dockerfile.prod apps/frontend
docker push "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/frontend:2.0.0-rc1"
```

Update `terraform.tfvars` with the actual tags and re-run `apply` so Cloud
Run picks up the new images.

## 5. Run database migrations + seed the demo dataset

Run Alembic migrations against the Cloud SQL instance via the Auth Proxy:

```sh
cloud-sql-proxy "$(terraform -chdir=terraform output -raw cloud_sql_connection_name)" &
PROXY_PID=$!

DATABASE_URL="postgresql+asyncpg://trustedoss:$DB_PASSWORD@127.0.0.1:5432/trustedoss" \
  alembic -c apps/backend/alembic.ini upgrade head

APP_ENV=demo DATABASE_URL="postgresql+asyncpg://trustedoss:$DB_PASSWORD@127.0.0.1:5432/trustedoss" \
  python3 apps/backend/scripts/seed_demo.py

kill $PROXY_PID
```

The seed script is **idempotent** — running twice yields the same dataset.
Output is a single JSON line with the seeded user emails and project IDs.

The demo super-admin credentials are:

- email: `admin@demo.trustedoss.dev`
- password: `DemoAdmin2026!`

These are public, documented credentials. The Demo SaaS is for demos
only — never store real customer data here.

## 6. Verify

Open `frontend_service_url` in a browser, sign in as
`admin@demo.trustedoss.dev`, and confirm:

- The dashboard shows 5 demo projects.
- `portal-web` and `portal-mobile` each have 10 CVEs and 5 license findings.
- The notifications dropdown shows 2 unread + 1 read for the developer
  user.

## 7. Cost monitoring

Set a budget alert before walking away:

```sh
gcloud billing budgets create \
  --billing-account="$BILLING_ACCOUNT" \
  --display-name="trustedoss-demo-monthly" \
  --budget-amount=50USD \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90
```

Idle cost target: **<$50/month** (Cloud SQL `db-f1-micro` ~$7 + Memorystore
1 GB BASIC ~$36 + VPC connector ~$3).

## 8. Cleanup

```sh
terraform -chdir=terraform destroy
gsutil rm -r "gs://$PROJECT_ID-tfstate"
```

Cloud SQL `deletion_protection` is **off** in the demo module so destroy
works without override. This is intentional — the demo dataset can always
be re-seeded.

## Troubleshooting

- **Cloud Run startup probe failing**: check
  `gcloud run services logs read $SERVICE_NAME --region=us-central1 --limit=100`.
  The most common cause is a stale `DATABASE_URL` after rotating the DB
  password — secrets are fetched at container start, so a Cloud Run
  "Edit & Deploy New Revision" forces a refresh.
- **`seed_demo.py` refuses with `APP_ENV=...`**: only `dev` and `demo` are
  allowed. The Cloud Run backend deploy already sets `APP_ENV=demo`. When
  running locally over the Auth Proxy, prefix the command with
  `APP_ENV=demo`.
- **VPC peering errors**: the `google_service_networking_connection`
  resource occasionally races with API enablement on a brand-new project.
  Re-run `terraform apply`.
