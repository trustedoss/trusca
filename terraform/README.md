# Terraform — TrustedOSS Portal Demo SaaS (GCP)

Reproducible infra-as-code for the GCP Demo SaaS deployment.

## Layout

```
terraform/
  main.tf                          # provider + module wiring
  variables.tf                     # tunables + secrets
  outputs.tf                       # service URLs, db connection
  versions.tf                      # provider pins + GCS state backend
  terraform.tfvars.example         # operator template
  modules/
    cloud_sql/                     # Postgres 17, db-f1-micro, private IP
    memorystore_redis/             # Redis 7, BASIC 1 GB, private IP
    cloud_run_backend/             # FastAPI, scale-to-zero, Cloud SQL Auth Proxy
    cloud_run_frontend/            # Vite static, scale-to-zero
```

## Cost target

Idle: **~$46 / month** at us-central1 (July 2026 list prices).

| Resource              | Tier            | Idle / month |
| --------------------- | --------------- | ------------ |
| Cloud Run backend     | min_instances=0 | $0           |
| Cloud Run frontend    | min_instances=0 | $0           |
| Cloud SQL             | db-f1-micro     | ~$7          |
| Memorystore Redis     | BASIC 1 GB      | ~$36         |
| VPC Connector         | e2-micro × 2    | ~$3          |
| GCS state             | < 1 MB          | < $0.10      |

`terraform plan` will not exceed the cost budget at idle. Bursting traffic
incurs Cloud Run per-request charges (rounded up to the 100 ms).

## First-time init

The state bucket name is parameterized via `-backend-config`:

```sh
gsutil mb -p "$PROJECT_ID" -l us-central1 "gs://$PROJECT_ID-tfstate"
gsutil versioning set on "gs://$PROJECT_ID-tfstate"

terraform -chdir=terraform init \
  -backend-config="bucket=$PROJECT_ID-tfstate"
```

After init, `cp terraform.tfvars.example terraform.tfvars`, fill in the
secrets, then:

```sh
terraform -chdir=terraform plan
terraform -chdir=terraform apply
```

## Destroying

```sh
terraform -chdir=terraform destroy
```

`deletion_protection` is **off** on Cloud SQL so destroy works without
manual override. This is intentional for a **demo** deployment that we can
re-seed at any time. Production deploys go through Helm and never use this
module.

## Validation locally (no GCP creds)

```sh
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```
