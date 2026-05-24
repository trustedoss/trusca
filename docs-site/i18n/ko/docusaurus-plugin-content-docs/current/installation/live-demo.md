---
id: live-demo
title: 라이브 읽기 전용 데모
description: TrustedOSS Portal을 공개 읽기 전용 라이브 데모로 운영하고 매일 밤 데이터셋을 초기화합니다.
sidebar_label: 라이브 데모
sidebar_position: 4
---

# 라이브 읽기 전용 데모

포털을 **공개 라이브 데모**로 운영할 수 있습니다. 누구나 시드된 데모 계정으로
로그인해 실제 프로젝트·스캔·취약점·라이선스·SBOM·보고서를 둘러볼 수 있지만,
**모든 쓰기 작업은 비활성화**되며 데이터셋은 매일 밤 깨끗한 상태로 초기화됩니다.

두 가지 독립적인 구성 요소로 이루어집니다.

1. **`DEMO_READ_ONLY` 읽기 전용 모드** (Docker Compose·GCP 등 모든 배포).
2. **일일 자동 리셋** (GCP 전용 — 번들 Terraform 모듈의 Cloud Scheduler → Cloud
   Run Job).

## 1. 읽기 전용 모드 (`DEMO_READ_ONLY`)

백엔드 환경 변수를 설정합니다.

```bash
DEMO_READ_ONLY=true
```

(허용되는 truthy 값: `1`, `true`, `yes`, `on` — 대소문자 무관. 요청 시점에
읽으므로, 값을 바꿀 때 재빌드 없이 프로세스 재시작만 하면 됩니다.)

활성화되면 단일 미들웨어가 **API 전체**에 정책을 강제하므로, 개별 엔드포인트가
정책을 우회할 수 없습니다.

- **읽기는 항상 통과** — `GET`·`HEAD`·`OPTIONS` (마지막은 CORS preflight 유지용).
- **쓰기는 기본 차단** — 모든 `POST`·`PUT`·`PATCH`·`DELETE`(및 기타 메서드)는
  **허용 목록(allow-list)** 에 없으면 거부됩니다.
- 허용 목록은 데모에 꼭 필요한 인증 흐름뿐입니다: `POST /auth/login`,
  `POST /auth/refresh`, `POST /auth/logout`. 그 외 — 회원가입, 비밀번호 재설정·
  변경, 프로젝트 생성, 스캔 트리거, 승인, 설정, 웹훅, 파일 업로드 — 는 모두
  차단됩니다.

차단된 요청은 `Content-Type: application/problem+json` 의 **RFC 7807** `403` 을
받습니다.

```json
{
  "type": "urn:trustedoss:problem:demo-read-only",
  "title": "Read-only demo",
  "status": 403,
  "detail": "This is a read-only live demo. Creating, updating, or deleting data is disabled. …",
  "instance": "/v1/projects",
  "demo_read_only": true
}
```

### 우회 방지

이 가드는 **차단 목록이 아니라 허용 목록**입니다. 나중에 추가되는 변이
엔드포인트는 별도 수정 없이 자동으로 차단됩니다. 허용 목록 비교 전에 경로를
정규화(역슬래시 변환, `.`/`..` 세그먼트 해석, 끝 슬래시 제거)하므로
`/v1/projects/../auth/login` 같은 트래버설로 쓰기 경로를 허용 목록에 끼워 넣을 수
없습니다. HTTP 메서드는 대소문자 무관으로 비교하고 허용 목록은 `(메서드, 경로)`
쌍을 키로 쓰므로, 비정상 메서드가 허용된 경로에 편승할 수 없습니다.

### 프론트엔드 동작

SPA는 공개 `GET /health` 응답(`{"status":"ok","demo_read_only":true}`)에서
플래그를 읽어,

- 앱 상단에 슬림한 **"읽기 전용 데모"** 배너를 표시하고,
- 쓰기 액션(예: "스캔", "프로젝트 등록" 버튼)을 비활성화하고 이유를 툴팁으로
  안내합니다.

실제 경계는 미들웨어이며, UI 게이팅은 막다른 클릭을 줄이는 보조 수단입니다.

## 2. 일일 자동 리셋 (GCP)

번들 Terraform 모듈은 하루 한 번 `scripts/reset_demo.py` 를 실행하는
**Cloud Scheduler → Cloud Run Job** 을 제공합니다. 이 작업은,

- **데모 데이터셋만 삭제** — `demo-org` 조직(FK cascade로 팀 → 프로젝트 → 스캔 →
  finding 제거)과 데모 사용자(안정적인 `@demo.trustedoss.dev` 이메일 접미사로
  매칭; cascade로 멤버십·알림 제거). 전체 truncate가 아니므로 다른 테넌트의
  데이터는 절대 건드리지 않습니다.
- 멱등한 `seed_demo._seed` 로 **재시드** 하므로 데이터셋 형태가 일반 시드와 단일
  소스로 일치합니다.
- `APP_ENV` 가 `dev` 또는 `demo` 가 아니면 **실행을 거부**합니다(프로덕션
  데이터베이스에는 절대 실행되지 않음).

`terraform.tfvars` 에서 활성화합니다(기본값 표시).

```hcl
demo_read_only       = true          # 인증 외 모든 변이 차단
demo_reset_enabled   = true          # 일일 Scheduler + Job 프로비저닝
demo_reset_schedule  = "17 3 * * *"  # cron (Cloud Scheduler 구문)
demo_reset_time_zone = "Etc/UTC"

# 선택 — 공개 데모 자격 증명이 매일 밤 바뀌지 않도록 데모 슈퍼 관리자 비밀번호를
# 고정합니다. 미설정 시 매일 밤 무작위로 회전됩니다(새 값은 Job 로그에 1회 출력).
# demo_super_admin_password = "REPLACE_ME_MIN_12_CHARS"
```

리셋 Job은 백엔드 이미지·서비스 계정·Cloud SQL 연결·시크릿을 재사용하므로 별도로
빌드하거나 권한을 부여할 것이 없습니다. 전체 배포 런북은
[GCP 데모 SaaS 배포](./gcp-deploy.md) 를 참고하세요.

:::note 수동 리셋
스케줄을 기다리지 않고 즉시 리셋을 실행할 수 있습니다.

```bash
gcloud run jobs execute <name_prefix>-<env>-demo-reset --region <region>
```

Job 이름은 `demo_reset_job_name` Terraform 출력에 있습니다.
:::

## 로컬 읽기 전용 데모 (Docker Compose)

읽기 전용 모드는 모든 배포에서 동작합니다. 로컬 읽기 전용 인스턴스는 `.env` 에
`DEMO_READ_ONLY=true` 를 추가하고 백엔드를 재시작하면 됩니다. 일일 리셋은 GCP
전용이며, 로컬에서는 깨끗한 데이터셋이 필요할 때마다
`apps/backend/scripts/reset_demo.py` 를 (`APP_ENV=demo` 로) 다시 실행하세요.
