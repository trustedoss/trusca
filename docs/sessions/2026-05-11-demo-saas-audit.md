---
title: Demo SaaS 출시 준비도 audit (묶음 1)
date: 2026-05-11
session: demo-saas-launch / bundle 1
authors: Claude Opus 4.7 + 5 parallel subagents
inputs: docs/sessions/_next-session-prompt-demo-saas-launch.md §2
outputs: 묶음 2-9 의 scope fix (본 문서 §10)
status: complete
---

# Demo SaaS 출시 준비도 audit

> 본 문서는 9 묶음 SaaS 출시 준비의 **묶음 1** 산출물이다. 코드/인프라/문서를 5 개의 독립
> audit 스레드 (OAuth · multi-tenant · free tier · backup · observability) 로 병렬 점검하고,
> 발견된 리스크를 P0/P1/P2 등급으로 묶음 2-9 에 배분한다.

## 0. 점검 범위와 방법

- **레포**: `github.com/trustedoss/trustedoss-portal`, main HEAD `b91c9d2` (backlog-marathon 종료).
- **방법**: 5 개 영역을 5 개의 독립 subagent 에게 read-only audit 으로 위임. 각 결과를 본 문서로 통합.
- **산출물**: 본 문서 + 묶음 2-9 backlog scope fix.
- **사용자 외부 작업**: 없음 (audit only).

---

## 1. 핵심 발견 (Executive Summary)

### 1.1 P0 — 출시 차단 (반드시 묶음 2 머지 전 해결)

| # | 영역 | finding | 묶음 |
|---|------|---------|------|
| **P0-A** | OAuth | branch (b) 자동 link 가 `is_verified=False` password 사용자를 그대로 link → ATO (OWASP A07) | 묶음 2 |
| **P0-B** | 배포 토폴로지 | terraform 에 **Celery worker / beat 모듈 자체가 없음**. 스캔/알림/백업/DT health 모두 동작 불가 | (신규) 묶음 6.5 또는 묶음 9 |

P0-A 는 `apps/backend/services/oauth_service.py:407-452` + 테스트가 동작을 expected 로 codify (`test_oauth_api.py:372-405`). 코드 + 테스트 동시 수정 필요.

P0-B 는 backup audit 의 사이드 발견이지만 **Demo SaaS 의 핵심 기능 (스캔)이 동작하지 않는다**는 의미. terraform/main.tf 가 `cloud_run_backend`, `cloud_run_frontend`, `cloud_sql`, `memorystore_redis` 4 개 모듈만 wiring — Celery worker / beat 부재. **선택지**:
- **옵션 A**: Cloud Run Job (worker) + Cloud Scheduler (beat trigger) 모듈 신설. 비용 ↑, 정합성 ↑.
- **옵션 B**: Demo SaaS 한정 스캔 동기화 (Demo 용 fixture data 만 노출) + auto-backup beat 비활성. 비용 ↓, 데모 가치 ↓.
- 권고: **옵션 A**. 출시 의의가 "실제 스캔이 동작하는 데모"이므로. 비용은 worker Cloud Run Job 의 콜드 스타트 + 스캔당 5-60분 실행 부담만큼.

### 1.2 P1 — 출시 직전 정리 (묶음 2/6 통합)

| # | 영역 | finding | 묶음 |
|---|------|---------|------|
| **P1-1** | Multi-tenant | API Key principal 의 scope-narrowing 결여 — project-scoped key 가 issuer 전체 team_ids 권한 보유 | 묶음 2 |
| **P1-2** | OAuth | `redirect_after` open redirect (state JWT 에 raw 저장) | 묶음 2 |
| **P1-3** | OAuth | OAuth `/authorize`, `/callback` 의 rate limit 부재 — abuse / outbound DoS 표면 | 묶음 2 |
| **P1-4** | OAuth | audit_logs listener `_hash_pii` 가 여전히 bare SHA-256 — keyed BLAKE2b 미통합 | 묶음 8 |
| **P1-5** | Multi-tenant | Webhook secret row-bound HMAC 검증의 단위 케이스 부재 | 묶음 2 |
| **P1-6** | Observability | structlog → Cloud Logging 필드 매핑 전무 (severity/message/trace/sourceLocation/stack_trace) | 묶음 6 |
| **P1-7** | Observability | user_id / team_id 가 structlog contextvars 에 bind 되지 않음 — CLAUDE.md §5 위반 | 묶음 6 |
| **P1-8** | Observability | `/health/ready` 부재 — Cloud Run startup_probe 가 TCP 소켓만 확인 → DB unreachable 상태로 traffic 수신 | 묶음 6 |

### 1.3 P2 — 출시 후 hardening

| # | 영역 | finding | 묶음 |
|---|------|---------|------|
| **P2-1** | Multi-tenant | `GET /v1/scans/{id}` 403 → 404 existence-hide 통일 | 묶음 2 |
| **P2-2** | Multi-tenant | Approvals 라우터 integration cross-team test 부재 | 묶음 2 |
| **P2-3** | OAuth | state JWT single-use (consumption tracking) 미구현 | 묶음 8 |
| **P2-4** | OAuth | PKCE 미적용 (GitHub/Google 모두) | 묶음 8 |
| **P2-5** | Observability | `mask_pii` 함수 2 곳 정의 충돌 — `core/logging.py` 의 단일값 helper 호출처 0건 | 묶음 6 |
| **P2-6** | Free tier | 사용량 조회 API 부재 (`GET /v1/me/usage`) | 묶음 5 |

### 1.4 OK (변경 불필요)

- OAuth scope 최소 권한 (`read:user user:email` / `openid email profile`).
- Provider access_token 미저장 (함수 스코프 폐기).
- OAuth callback 의 정규화된 error 코드 응답 (raw provider error 미노출).
- service-layer `assert_team_access` 가 60+ endpoint 에 일관 적용. admin 의 `require_super_admin_or_404` (PR #13). drawer 의 404 existence-hide.
- ProjectUpdate `team_id` immutable. Celery task 의 server-side team_id 도출. WS first-frame JWT + close 4404 cross-team gate.
- `redact_url_userinfo` 가 scan_source.py 의 git_url 3 라인에서 정확히 호출.
- RFC 7807 422 validation error sanitize (`core/errors.py:_redact_validation_errors`).

---

## 2. OAuth 코드 완성도 (스레드 #1)

### 2.1 항목별 등급

| # | 항목 | 등급 | 코드 위치 |
|---|------|------|----------|
| 1 | State CSRF (single-use) | P2 | `services/oauth_service.py:149-188` |
| 2 | PKCE | P2 | `integrations/oauth/github.py:65-74`, `google.py:58-76` |
| 3 | Redirect URI 검증 (`redirect_after`) | **P1** | `api/v1/oauth.py:128-156, 227-232`, `services/oauth_service.py:166-167` |
| 4 | Scope 최소 권한 | OK | `integrations/oauth/github.py:50`, `google.py:43` |
| 5 | Provider token 저장 | OK | `services/oauth_service.py:315-322` |
| 6 | 계정 link / 하이재킹 (`is_verified`) | **P0** | `services/oauth_service.py:407-452`, `tests/integration/test_oauth_api.py:360-405` |
| 7 | Audit hash (listener `_hash_pii`) | P1 | `core/audit.py:94, 150-163` |
| 8 | 에러 응답 (RFC 7807) | OK | `api/v1/oauth.py:216-218` |
| 9 | Rate limit (`/authorize`, `/callback`) | P1 | `api/v1/oauth.py:123-157, 164-234` |
| 10 | 로깅 (PII 마스킹) | OK | (전수 grep 결과 토큰/평문 0 건) |

### 2.2 P0-A 상세 — `is_verified=False` 자동 OAuth link ATO

**시나리오**:
1. 공격자가 `victim@example.com` 으로 password 가입 시도. 가입은 성공하나 verification email 미확인 → `User.is_verified=False`.
2. 공격자가 victim 인 척하지 않고 그냥 자기 GitHub 계정 (어떤 이메일이든) 의 primary email 을 `victim@example.com` 으로 등록 후 verify.
3. 공격자가 OAuth 로 portal 로그인 → branch (b) 가 동일 email 의 기존 User 와 자동 link.
4. 이후 공격자는 OAuth 로 victim 계정에 무제한 접근.

**현행 테스트**: `test_callback_links_to_existing_password_user` 가 이 동작을 *expected behavior* 로 lock-in 중. 코드 + 테스트 동시 수정.

**권고 (묶음 2)**:
- branch (b) 진입 시 `user.is_verified=True` 필수 → False 면 OAuth callback 이 "이메일 확인 후 다시 시도" 페이지로 302 (또는 명시적 link-confirmation email).
- 더 강한 안전망 — 모든 password 가입에 verification email 발송 + 미확인 계정의 OAuth link 자체 금지.
- 통합 테스트 갱신 — `is_verified=False` 시나리오를 "차단됨" 으로 재정의.

### 2.3 P1-2 상세 — `redirect_after` open redirect

**경로**: `/authorize?redirect_after=https://evil.example` → 서버가 검증 없이 state JWT 에 서명 → callback 에서 `RedirectResponse(target)`.

**권고 (묶음 2)**: same-origin 허용 — 상대 경로 (`/` 시작) + no `//` (protocol-relative) + no `\` (Windows 우회). authorize 진입 시점 검증 후 reject (400 Problem) 또는 silent drop.

### 2.4 P1-3 상세 — OAuth endpoint rate limit

`@limiter.limit` 미적용. **권고 (묶음 2)**: `authorize` 10/min, `callback` 20/min (IP). state 디코드 성공 후 사용자 단위 nested limit 도 고려.

---

## 3. Multi-tenant 격리 (스레드 #2)

### 3.1 표 요약

60+ endpoint 점검 결과 **service-layer 의 `assert_team_access` 보급률 매우 높음**. 다만 다음 4 가지가 묶음 2 backlog.

| # | 등급 | 항목 | 작업량 |
|---|------|------|--------|
| **P1-1** | P1 | API Key principal scope-narrowing | 1.5 일 |
| **P1-5** | P1 | Webhook secret row-bound HMAC 검증 단위 케이스 | 0.5 일 |
| **P2-1** | P2 | `GET /v1/scans/{id}` 403 → 404 existence-hide | 0.5 일 |
| **P2-2** | P2 | `test_approvals_api.py` integration cross-team test 신설 | 0.5 일 |

### 3.2 P1-1 상세 — API Key principal scope-narrowing

**파일**: `apps/backend/core/api_key_auth.py:148-149` (`get_api_key_principal`).

**문제**: project-scoped key 가 발급되었지만 인증 시 `CurrentUser.team_ids` 는 issuer 의 모든 memberships 를 그대로 주입.

```python
team_ids = [m.team_id for m in memberships]   # key.team_id 무시
team_roles = {m.team_id: m.role for m in memberships}
```

**영향**: CI 에서 발급된 project-scoped key 가 유출되면 폭발 반경 = issuer 의 모든 team. SCA 시장에서 광고하는 "project-scoped CI key" 컨트랙트 위반.

**권고 (묶음 2)**:
- `key.scope` 별 team_ids narrowing —
  - `scope='org'` → 그대로 (super_admin only)
  - `scope='team'` → `[key.team_id]`
  - `scope='project'` → `[key.team_id]` + `scoped_project_id=key.project_id` 부착, service-layer 가 cross-project 차단
- `CurrentUser.api_key_scope: Literal["jwt","org","team","project"] | None` 신규 필드
- unit test: project-scoped key 로 다른 team_id gate-result 호출 → 404 existence-hide
- memo `feedback_api_key_scope_inheritance` 등재

### 3.3 P1-5 상세 — Webhook row-bound HMAC

`webhook_service.py::process_github_webhook` / `process_gitlab_webhook` 가 payload 의 `repository.html_url` (GH) / `repository.git_http_url` (GL) 로 Project lookup 후 그 row 의 `webhook_secret` 으로 HMAC 검증. 코드상 동일 row 안에서 진행되나 row-binding 회귀 검증 테스트 부족.

**권고 (묶음 2)**: parametrize unit test 30+ 케이스 — "valid HMAC for project A, repo URL pointing to project B → 401", "audit log emitted", "constant-time comparison".

---

## 4. Free tier enforcement (스레드 #3)

### 4.1 현황

| 항목 | 현재 | 코드 위치 |
|------|------|----------|
| `tier` / `quota_*` 컬럼 (teams) | **부재** | `models/auth.py:101-130` (Team 클래스에 컬럼 자체 없음) |
| project 생성 한도 | **무제한** | `services/project_service.py:105-164` (count 검사 없음) |
| scans/day 한도 | **무제한** | `services/scan_service.py:168-300` (per-project active scan 만 partial unique index, per-team per-day 없음) |
| components/project 한도 | **무제한** | `tasks/scan_source.py:715-790` (`_persist_components` 가 무조건 upsert) |
| 사용량 조회 API | **부재** | `api/v1/users_me.py` 에 quota/usage 없음 |
| rate limit vs quota 분리 | rate limit (`slowapi`) 만 존재 | `core/ratelimit.py:28-83` |

**결론**: free tier enforcement 는 코드 차원에서 **완전 부재**. 현 상태로 Demo SaaS 공개 시 가입자 1명이 unbounded 자원 소비 가능. 묶음 5 backlog 가 confirmed.

### 4.2 묶음 5 backlog (확정)

1. Alembic 0015 — `teams` 컬럼 확장:
   - `tier` ENUM `('free','pro','enterprise')` NOT NULL DEFAULT `'free'`
   - `quota_max_projects`, `quota_max_scans_per_day`, `quota_max_components_per_project` INTEGER NULL
   - 데이터 migration 0016 (분리, 멱등) — 기존 team 은 `tier='enterprise'` + quota_* NULL 백필
   - `FREE_TIER_DEFAULTS` 상수 (`core/config.py`): projects 3, scans/day 10, components/project 1000
2. `services/quota_service.py` 신설 — `check_project_quota`, `check_scan_today_quota`, `check_component_count_quota`
3. endpoint 통합 — `project_service.create_project` 직전, `scan_service.start_scan` (disk guard 뒤), `tasks/scan_source._persist_components` 진입부
4. RFC 7807 type URN: `urn:trustedoss:problem:free_tier_quota_exceeded` (status 429, `Retry-After` 헤더 + `reset_at`)
5. `GET /v1/me/usage` — `{tier, projects: {used, limit}, scans_today: {used, limit, reset_at}, components_high_water: [...]}`
6. frontend `<UsageBanner />` — 80% yellow / 100% red. `useUsage()` TanStack Query 5분 캐시. `/projects/new` 한도 임박 시 CTA 비활성 + tooltip
7. quota check race: 동시 4건 project create → `SELECT ... FOR UPDATE` on teams row 또는 advisory lock
8. Producer-Reviewer: tier upgrade audit log 기록

---

## 5. Backup / Cloud SQL 결합 (스레드 #4)

### 5.1 가장 큰 발견 — Celery worker/beat 부재

`terraform/modules/` 에 `cloud_run_backend`, `cloud_run_frontend`, `cloud_sql`, `memorystore_redis` 만 존재. **Celery worker / beat 모듈 없음**.

영향:
- 묶음 3 의 `daily-auto-backup` Celery Beat 가 **GCP 환경에서 절대 발화하지 않음**.
- 스캔 파이프라인 (cdxgen → ORT → DT → Trivy, 5-60분) **전체 동작 불가**.
- 알림 dispatch task, DT health monitor heartbeat, 자동 backup prune 모두 동작 불가.
- 시스템이 "그래도 안 무너지는" 이유 = Cloud SQL automated backup 이 매일 03:00 UTC dump 떠줌. 백업 책임이 **암묵적으로 Cloud SQL 에 위임**된 상태.

→ **P0-B**. 묶음 6.5 또는 묶음 9 에 신규 backlog 추가.

### 5.2 Cloud SQL 설정 (terraform 실측)

| 항목 | 현재 값 | 평가 |
|------|---------|------|
| `backup_configuration.enabled` | `true` | OK |
| `start_time` | 03:00 UTC | OK |
| `point_in_time_recovery_enabled` | **`false`** | **부적합** — drop-table 실수 시 24h 손실 |
| `backup_retention_settings.retained_backups` | 7 | OK (Demo 한정) |
| `availability_type` | ZONAL | OK (Demo) |
| `deletion_protection` | `false` | Demo 한정. **GA helm 에선 강제 true** |
| Cross-region 백업 | 없음 | Demo 한정 |

### 5.3 묶음 9 launch checklist 항목

- [ ] Cloud SQL PITR 활성 (`point_in_time_recovery_enabled = true`, `transaction_log_retention_days = 7`). 비용 ~$1-2/월
- [ ] `db_backup_retention_count`, `db_pitr_enabled` 를 `terraform/variables.tf` 에 노출
- [ ] **Celery worker/beat 토폴로지 결정** (P0-B):
  - 옵션 A: Cloud Run Job + Cloud Scheduler 신설
  - 옵션 B: Demo 한정 스캔 동기 + auto-backup beat 비활성
  - 옵션 A 권고
- [ ] DR runbook — `docs-site/docs/admin-guide/backup-and-restore.md` 에 "Demo SaaS / GCP 전용" 섹션. `gcloud sql backups list/restore`, PITR clone, 30분 SLO
- [ ] restore drill 1회 (staging Cloud SQL → smoke test)
- [ ] `docs-site/docs/installation/gcp-deploy.md` 에 backup/restore 단계 추가 (현재 0건)
- [ ] `data-retention-policy.md` 신규 (Cloud SQL 7 / 앱 7일 / manual 90일 cap)
- [ ] CMEK 결정 기록 — Demo 는 Google-managed, GA 는 CMEK 강제 ADR
- [ ] admin UI "Run manual backup now" 버튼이 Cloud SQL on-demand snapshot 호출하도록 재배선 (옵션 B 채택 시)

---

## 6. Observability (스레드 #5)

### 6.1 가장 큰 발견 — structlog ↔ Cloud Logging 매핑 전무

| 필드 | 현재 출력 | Cloud Logging 기대 | 일치? |
|------|----------|-------------------|------|
| severity | `level` (소문자) | `severity` (대문자) | NO — 모든 라인이 DEFAULT 로 떨어짐 |
| message | `event` | `message` | NO — summary line 빈 채로 표시 |
| trace | (없음) | `logging.googleapis.com/trace` | NO |
| spanId | (없음) | `logging.googleapis.com/spanId` | NO |
| sourceLocation | (없음) | `logging.googleapis.com/sourceLocation` | NO |
| stack_trace (Error Reporting) | `exception` | `stack_trace` + `@type` Error Reporting magic | NO |
| request_id | `request_id` | `jsonPayload.request_id` | YES (자유 필드) |
| user_id | (대부분 라인 부재) | `jsonPayload.user_id` | NO — `_load_current_user` 가 contextvars 에 bind 안 함 |
| team_id | (없음) | `jsonPayload.team_id` | NO |

**파급 효과**: Cloud Logging 의 severity 필터, Cloud Trace 로그 join, Error Reporting 자동 집계가 모두 동작하지 않음. 묶음 6 의 알람 정책이 severity 가 아닌 jsonPayload 필터로만 가능해져 복잡도 ↑.

### 6.2 `mask_pii` 함수 충돌

두 곳에 서로 다른 시그니처:
- `core/logging.py:60` — `mask_pii(value) -> str`. **호출처 0건** (테스트 제외).
- `core/pii_mask.py:70` — `mask_pii(value, *, _depth) -> Any`. 재귀 트리 마스킹.

CLAUDE.md §5 의 "마스킹 헬퍼 통과" 의무가 사실상 미작동.

### 6.3 묶음 6 backlog (확정 + 확장)

1. **6-1. structlog Cloud Logging processor 추가** (`core/logging.py`):
   - severity 변환 (level → SEVERITY uppercase)
   - event → message 복사
   - trace context processor — `X-Cloud-Trace-Context` 헤더 파싱 (W3C `traceparent` fallback) → contextvars 에 `trace_id`, `span_id`, `trace_sampled` bind → `logging.googleapis.com/trace = projects/{project}/traces/{trace_id}`
   - `CallsiteParameterAdder` → `sourceLocation` 재포장
   - ERROR 레벨 + traceback → `stack_trace` + `@type` Error Reporting magic
   - dev 환경 (APP_ENV=dev) 분기 — 콘솔 JSONRenderer 유지
2. **6-2. user_id/team_id contextvars 바인딩** — `core/security.py:_load_current_user` 에 `structlog.contextvars.bind_contextvars(user_id=..., team_ids=...)` 추가, RequestIDMiddleware finally 절에서 unbind
3. **6-3. `mask_pii` 함수 단일화** — `core/logging.py` 의 단일값 helper 삭제 (호출처 0건). 422 validation error 로그 라인 추가
4. **6-4. `/health/live` + `/health/ready` 분리** — startup_probe 를 `/health/ready` (DB SELECT 1 + Redis PING) 로 변경 (`terraform/modules/cloud_run_backend/main.tf:154-161`). `/health/*` request_completed 로그를 DEBUG 로 강등 (Cloud Run probe noise 제거)
5. **6-5. `terraform/modules/monitoring/` 신규 모듈** — 알람 4 개:
   - error rate spike (log-based metric, severity≥ERROR)
   - scan queue depth (Celery `inspect().reserved()` → custom metric, 50 task 5분 지속)
   - DT health flap (`dt_breaker_opened` count, 10분 안 2회 이상)
   - Cloud SQL connection saturation (`num_backends/max_connections > 80%` 5분)
6. **6-6. notification channels** — `google_monitoring_notification_channel` (email + Slack), terraform variable
7. **6-7. monitoring dashboard** — `google_monitoring_dashboard` JSON. 4 시그널 + Cloud Run request rate/p95 + DT breaker heatmap
8. **6-8. 추가 알람 후보** — 5xx burst, Cloud Run cold start latency p95, Memorystore memory ratio, Secret Manager access denied audit
9. **6-9. dt/health.py auto-restart stderr** — `result.stderr[:500]` 운영 환경 sanitize (env/볼륨/네트워크 노출 위험)

---

## 7. 묶음 7 (법적 페이지) — audit 영향 없음

원 scope 유지. ToS / Privacy / Cookie / data-retention-policy 초안 + 푸터 링크. 법무 검토 placeholder 명시.

---

## 8. 묶음 8 (invite-only beta) — audit 영향 작음

원 scope 유지 + **P1-4 흡수**:
- audit_logs listener `_hash_pii` 의 bare SHA-256 → keyed BLAKE2b (`oauth_identity_service.py` 와 동일 패턴) 로 통합.
- key rotation 정책 ADR 추가.
- `.env.example` 의 `AUDIT_HASH_KEY` 가 prod 필수임을 강조.

P2-3 (state single-use), P2-4 (PKCE) 도 묶음 8 의 보안 hardening 묶음으로 함께.

---

## 9. 묶음 9 (launch readiness) — 대폭 확장

원 체크리스트에 다음 항목 추가:
- [ ] **Celery worker/beat Cloud Run Job 모듈 배포 + 검증** (P0-B 가 옵션 A 로 결정된 경우)
- [ ] Cloud SQL PITR 활성 + restore drill 1 회
- [ ] data-retention-policy.md 머지
- [ ] CMEK ADR 머지
- [ ] terraform monitoring 모듈 적용 + 4 알람 trigger 시나리오 문서화
- [ ] admin manual backup 버튼 동작 검증 (옵션 A: Celery job, 옵션 B: Cloud SQL on-demand)
- [ ] structlog → Cloud Logging severity 변환 검증 (Logs Explorer 에서 ERROR 라인 필터)
- [ ] /health/live, /health/ready Cloud Run probe 정확도 검증

---

## 10. 묶음 2-9 scope fix 요약표

| 묶음 | 원 scope | audit 후 추가 | 등급 |
|------|---------|---------------|------|
| **2** Multi-tenant 강화 | BOLA/IDOR test | + OAuth P0-A (`is_verified` link), OAuth P1-2 (redirect_after), OAuth P1-3 (rate limit), MT P1-1 (API Key scope), MT P1-5 (webhook), MT P2-1 (scan 404), MT P2-2 (approvals integ) | **P0** |
| **3** OAuth runbook | provider 등록 절차 | 변경 없음 | — |
| **4** Cloud Armor | WAF + rate limit | 변경 없음 | — |
| **5** Free tier | quota 도입 | + RFC 7807 URN 명시, P2-6 `/v1/me/usage` 추가 | P1 |
| **6** Observability | structlog + 4 알람 | + GCP field mapping (severity/message/trace), user_id 바인딩, /health/ready 분리, mask_pii 단일화 | **P0/P1** |
| **6.5** (신규) Celery worker/beat 배포 | — | terraform Cloud Run Job + Cloud Scheduler, 또는 옵션 B (Demo 동기 + beat off) | **P0** |
| **7** 법적 페이지 | ToS/Privacy/Cookie | 변경 없음 | — |
| **8** Invite-only beta | invite token | + P1-4 (listener BLAKE2b), P2-3 (state single-use), P2-4 (PKCE) | P1 |
| **9** Launch checklist | GA 체크리스트 | + PITR/restore drill/CMEK/data-retention/worker 배포 검증 등 8 개 신규 | P0 |

---

## 11. 묶음 2 진입 prompt (다음 세션 첫 메시지)

```
docs/sessions/2026-05-11-demo-saas-audit.md §10 의 묶음 2 scope 로 진행한다.
P0-A (OAuth is_verified ATO) 가 가장 시급. 코드 + 테스트 동시 수정.
이어서 P1-1 (API Key scope-narrowing), P1-2 (redirect_after), P1-3 (OAuth rate limit),
P1-5 (webhook row-bound), P2-1 (scan 404), P2-2 (approvals integ).
security-reviewer 머지 전 통과 필수.
```

## 12. 사용자 결정 필요 사항

다음 4 가지는 사용자 결정이 필요. 묶음 2 진입 전 또는 도중에 알려주면 됨.

1. **P0-B 옵션 A vs B** — Celery worker/beat 토폴로지.
   - **A** (권고): Cloud Run Job + Cloud Scheduler 신설. 비용 추가, 진짜 데모.
   - **B**: Demo 한정 스캔 동기 + beat 비활성. 비용 절약, 데모 가치 ↓.
2. **password 가입 verification email 도입 여부** — P0-A 의 더 강한 안전망. 도입하면 `/auth/register` 응답이 "이메일 확인 안내", `/auth/verify-email/{token}` 신설 필요.
3. **PITR 활성화 비용 승인** — 월 $1-2 추가. 묶음 9 의 사용자 외부 작업 (terraform apply).
4. **OAuth 동일 이메일 cross-provider link 정책** — GitHub email = Google email = password email 일 때 자동 link 할지 (현행 동작) 사용자 확인 후 link 할지. 묶음 2 에서 같이 결정.

---

## 13. 작성 메모

- 본 audit 은 **read-only**. 코드 변경 없음.
- 5 개 subagent 가 각자 독립 grep + read 로 결론 도출. 결과는 본 메인 세션에서 통합.
- audit 후 묶음 2-9 backlog 가 fix 된 것이 가장 큰 산출물. backlog 표 §10 은 다음 세션의 단일 진실.
