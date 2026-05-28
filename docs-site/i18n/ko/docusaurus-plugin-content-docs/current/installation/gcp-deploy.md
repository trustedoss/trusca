---
id: gcp-deploy
title: GCP 데모 SaaS 배포
description: GCP 공개 데모 SaaS 운영자 런북 — 번들 Terraform 모듈을 통한 Cloud Run + Cloud SQL + Memorystore 배포.
sidebar_label: GCP 데모 SaaS
sidebar_position: 3
---

# GCP 데모 SaaS 배포 가이드

TrustedOSS Portal 데모 SaaS를 Google Cloud Platform에 배포하기 위한 운영자 가이드. 저장소 루트의 `terraform/` 모듈로 재현 가능하다.

데모 SaaS는 공개적으로 접근 가능한 저비용(유휴 시 월 $50 미만) 쇼케이스 배포용이다. 실제 고객 운영 데이터를 위한 환경이 **아니다** — 운영 환경 배포는 Helm 차트를 통해 진행한다.

## 1. 사전 준비

- 결제가 활성화된 GCP **프로젝트**. 비어 있는 프로젝트여야 한다 — Terraform이 VPC, Cloud Run 서비스, Cloud SQL, Memorystore, IAM 바인딩을 생성한다.
- 프로젝트에 Editor / Owner 역할을 가진 사용자(또는 서비스 계정)로 인증된 `gcloud` CLI.
- 워크스테이션에 `terraform` 1.7 이상. 여러 버전을 관리한다면 `tfenv`를 사용한다.
- 백엔드 / 프론트엔드 이미지를 빌드 및 푸시할 `docker`.

필요한 API를 한 번만 활성화한다 (Terraform도 활성화하지만, 미리 해두면 첫 apply가 빨라진다):

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

## 2. 변수 설정

```sh
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

`terraform/terraform.tfvars`를 편집해 다음 값을 채운다:

| 변수              | 생성 방법                                                          |
| ----------------- | ------------------------------------------------------------------ |
| `project_id`      | GCP 프로젝트 ID (예: `my-trustedoss-demo`)                         |
| `db_password`     | `openssl rand -base64 24 \| tr -d '=+/' \| cut -c1-24`             |
| `app_secret_key`  | `openssl rand -hex 32`                                             |
| `backend_image`   | Artifact Registry 경로 + 태그 (4단계에서 이미지 생성 후 채움)      |
| `frontend_image`  | 동일                                                               |

`terraform.tfvars`는 git-ignore 처리된다. 절대 커밋하지 말 것.

## 3. 초기화 + apply

GCS 상태 버킷 생성 및 초기화:

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

첫 apply는 약 12분 소요된다 (Cloud SQL이 약 8분으로 가장 오래 걸린다). 출력값을 확인한다:

- `backend_service_url`  — Cloud Run 백엔드 HTTPS URL
- `frontend_service_url` — Cloud Run 프론트엔드 HTTPS URL
- `cloud_sql_connection_name` — 시드 단계에서 사용

## 4. 이미지 빌드 + 푸시

Artifact Registry 저장소가 만들어진 뒤(3단계에서 자동 생성), 백엔드 / 프론트엔드 이미지를 빌드 후 푸시한다. 정확한 커맨드는 `docs-site/docs/operations/release.md`를 참고하고, 요약은 다음과 같다:

```sh
gcloud auth configure-docker us-central1-docker.pkg.dev

docker build -t "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/backend:2.0.0-rc1" \
  -f apps/backend/Dockerfile .
docker push "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/backend:2.0.0-rc1"

docker build -t "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/frontend:2.0.0-rc1" \
  -f apps/frontend/Dockerfile.prod apps/frontend
docker push "us-central1-docker.pkg.dev/$PROJECT_ID/trustedoss/frontend:2.0.0-rc1"
```

`terraform.tfvars`에 실제 태그를 반영하고 `apply`를 다시 실행해 Cloud Run이 새 이미지를 가져오게 한다.

## 5. 마이그레이션 + 데모 데이터 시드

Auth Proxy를 통해 Cloud SQL에 Alembic 마이그레이션 실행:

```sh
cloud-sql-proxy "$(terraform -chdir=terraform output -raw cloud_sql_connection_name)" &
PROXY_PID=$!

DATABASE_URL="postgresql+asyncpg://trustedoss:$DB_PASSWORD@127.0.0.1:5432/trustedoss" \
  alembic -c apps/backend/alembic.ini upgrade head

APP_ENV=demo DATABASE_URL="postgresql+asyncpg://trustedoss:$DB_PASSWORD@127.0.0.1:5432/trustedoss" \
  python3 apps/backend/scripts/seed_demo.py

kill $PROXY_PID
```

시드 스크립트는 **멱등(idempotent)**이다 — 두 번 실행해도 동일한 데이터셋이 된다. 출력은 시드된 사용자 이메일과 프로젝트 ID를 담은 한 줄짜리 JSON이다.

데모 슈퍼 관리자 자격 증명:

- 이메일: `admin@demo.trustedoss.dev`
- 비밀번호: `DemoAdmin2026!`

위는 공개된, 문서화된 자격 증명이다. 데모 SaaS는 데모 전용 — 실제 고객 데이터를 절대 저장하지 말 것.

## 6. 검증

브라우저에서 `frontend_service_url`을 열고 `admin@demo.trustedoss.dev`로 로그인한 뒤 다음을 확인한다:

- 대시보드에 5개 데모 프로젝트가 표시된다.
- `portal-web`과 `portal-mobile`에 각각 CVE 10건, 라이선스 발견 5건이 있다.
- 알림 드롭다운에 개발자 사용자 기준으로 미읽음 2건 + 읽음 1건이 표시된다.

## 7. 비용 모니터링

자리를 비우기 전 예산 알림을 설정한다:

```sh
gcloud billing budgets create \
  --billing-account="$BILLING_ACCOUNT" \
  --display-name="trustedoss-demo-monthly" \
  --budget-amount=50USD \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90
```

유휴 비용 목표: **월 $50 미만** (Cloud SQL `db-f1-micro` ~$7 + Memorystore 1 GB BASIC ~$36 + VPC 커넥터 ~$3).

## 8. 정리(Cleanup)

```sh
terraform -chdir=terraform destroy
gsutil rm -r "gs://$PROJECT_ID-tfstate"
```

데모 모듈에서는 Cloud SQL `deletion_protection`이 **꺼져 있어** 별도 재정의 없이 destroy가 동작한다. 의도된 동작이다 — 데모 데이터셋은 언제든 다시 시드할 수 있다.

## 트러블슈팅

- **Cloud Run 시작 프로브 실패**: `gcloud run services logs read $SERVICE_NAME --region=us-central1 --limit=100`로 로그 확인. 가장 흔한 원인은 Secret Manager 비밀번호 회전 후 `DB_PASSWORD` 값이 오래된 경우다 — Cloud Run은 컨테이너 시작 시 시크릿을 읽으므로 Cloud Run "Edit & Deploy New Revision"으로 강제 새로고침할 수 있다.
- **DB 연결 환경변수 규약**: Cloud Run 모듈은 `DB_USER`, `DB_PASSWORD` (Secret Manager 출처), `DB_HOST` (`/cloudsql/<connection_name>` 유닉스 소켓), `DB_NAME`을 분리해 노출한다. 애플리케이션의 `core.config.database_url()`이 런타임에 asyncpg DSN을 합성하고 비밀번호를 URL-encode 한다 (CLAUDE.md 핵심 규칙 #11). Cloud Run revision에 `DATABASE_URL`을 **설정하지 말 것** — 합성 DSN을 덮어써 Secret Manager를 우회한다.
- **`seed_demo.py`가 `APP_ENV=...`로 거부**: `dev`와 `demo`만 허용된다. Cloud Run 백엔드 배포는 이미 `APP_ENV=demo`를 설정한다. Auth Proxy를 거쳐 로컬에서 실행할 때는 명령어 앞에 `APP_ENV=demo`를 붙인다.
- **VPC 피어링 오류**: 새 프로젝트에서 `google_service_networking_connection` 리소스가 API 활성화와 가끔 경합한다. `terraform apply`를 다시 실행한다.
