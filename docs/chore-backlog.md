# TrustedOSS Portal v2 — Chore Backlog

> Step 1~12 (PR #16~#27) 머지 과정에서 시간 제약 또는 한도 도달로 미루어진 항목 정리.
>
> 각 항목은 독립적인 chore PR로 처리 가능. **우선순위 순서**로 나열했고, 같은 chore PR 안에 함께 묶을 수 있는 항목은 그룹으로 표시.

---

## 우선순위 1 — 사용자 가시성 / GA blocker

### ~~Chore A1 — 비밀번호 찾기 + i18n 게이트 (frontend-only)~~ ✅ PR #28 (2026-05-09)
**기반 PR**: #22 (Step 7 backend — `/auth/forgot-password`, `/auth/reset-password` 존재)
**브랜치**: `chore/frontend-bundle` (머지 후 삭제)
**머지 commit**: `df5bb5e`

처리 결과:
- ✅ `/forgot-password` → `POST /auth/forgot-password` 연동 (anti-enumeration 균일 success view)
- ✅ `/reset-password` 신규 (`?token=` → `POST /auth/reset-password`, missing-token UI 포함)
- ✅ `i18next-parser` 9.4.0 + `i18n:check` CI 게이트 (EN/KO drift 0 강제)

### Chore A2 — 인앱 알림 센터 + 채널 ON/OFF (backend + frontend)
**기반 PR**: 없음 (PR #22 는 outbound dispatcher 만 배포, in-app notification center 백엔드 미존재)
**브랜치 제안**: `chore/phase6-inapp-notifications`
**예상 소요**: 1.5 세션

미흡 — **백엔드 신규 작업 필요**:
- 백엔드: `notifications` 테이블 + Alembic + `/v1/notifications` (GET list, PATCH /:id/read), `/v1/users/me/notification-prefs` (PUT)
- 프론트: `/notifications` 페이지 + 헤더 벨 아이콘 (읽음/안읽음 카운트)
- 프론트: 사용자 설정 페이지 — 채널별 ON/OFF (email/slack/teams)

### ~~Chore B — Frontend OAuth 버튼~~ ✅ PR #28 (2026-05-09)
**기반 PR**: #26 (Step 11 backend)
**브랜치**: `chore/frontend-bundle` (A1, C와 함께 묶음)
**머지 commit**: `df5bb5e`

처리 결과:
- ✅ `/login` 페이지에 GitHub / Google 버튼
- ✅ `redirect_after` 쿼리 파라미터 → 백엔드 authorize endpoint로 propagation
- ✅ 7개 OAuth 에러 코드 i18n 매핑 (denied / invalid_state / failed / user_inactive / no_organization / missing_params / unknown)
- ✅ E2E 시나리오: 버튼 visibility + `?error=oauth_denied` 배너 (실제 클릭은 외부 provider redirect라 visibility-only)
- 백엔드 경로 보정: `/auth/oauth/<provider>/...` (no `/v1` prefix — 라우터 자체 prefix 선언)

### ~~Chore C — /integrations 페이지~~ ✅ PR #28 (2026-05-09)
**기반 PR**: #20 (Step 5)
**브랜치**: `chore/frontend-bundle` (A1, B와 함께 묶음)
**머지 commit**: `df5bb5e`

처리 결과:
- ✅ `/integrations` 페이지: API Key 생성/조회/폐기 UI + AppShell nav (KeyRound 아이콘)
- ✅ 평문 키 1회 노출 dialog + 복사 버튼 + 경고 문구
- ✅ Webhook 수신 URL 안내 섹션 (GitHub HMAC, GitLab token)
- 백엔드 RBAC 의존: 백엔드가 scope 별 권한 강제 (super_admin → org, team_admin → team, developer → project)
- 미반영: `expires_in_days` 필드 (백엔드 `APIKeyCreateIn` 미정의 — 향후 추가 시 1줄 변경)

---

## 우선순위 2 — 운영 안정성

### ~~Chore D — 자동 백업 + 수동 백업/복원 UI + WebSocket 재연결~~ ✅ PR #29 (2026-05-09)
**머지 commit**: `f2b9f9e`

처리 결과:
- ✅ Celery Beat `daily-auto-backup` (00:00 UTC), 7일 retention (auto-* 만; manual-* 영구)
- ✅ `/admin/backup` UI: 수동 트리거 + 스트리밍 다운로드 + 업로드+복원 (type-"restore" gate) + 삭제
- ✅ `useScanWebSocket` 의 visibility-listener — 탭 복귀 시 즉시 reconnect (5분 budget 보존)

### ~~Chore E — backup.sh / restore.sh 실제 검증~~ ✅ PR #38 (2026-05-09)
**기반 PR**: #24 (Step 9)
**브랜치**: `chore/install-restore-uat`

처리 결과 — 옵션 A (자동 CI) + 옵션 B (수동 체크리스트) 결합:
- ✅ `scripts/install.sh` `--no-prompt` 모드 추가. `INSTALL_HOST`, `INSTALL_ADMIN_EMAIL`, `INSTALL_ADMIN_PASSWORD`, `INSTALL_SECRET_KEY`, `INSTALL_REUSE_ENV` env 지원. 기존 대화형 동작은 그대로 유지.
- ✅ `.github/workflows/install-uat.yml` 신규 — Ubuntu 22.04 GitHub-hosted runner에서 `docker-compose` V1 1.29.2 설치 → `install.sh --no-prompt` → /health probe → login + projects API smoke → `backup.sh` → `restore.sh` 라운드트립 → post-restore /health probe. `workflow_dispatch` + 매주 일요일 03:00 UTC cron.
- ✅ `.github/workflows/ci.yml`에 `shellcheck` 잡 추가 — `--severity=warning` (error + warning hard-fail). 모든 기존 스크립트 통과 (info-수준 SC1091 2건만 advisory).
- ✅ `scripts/upgrade.sh`의 SC2034 (unused `i` → `_`) + `scripts/release.sh`의 SC2046 (의도적 word-split disable 주석 추가) fix.
- ✅ `docs-site/docs/installation/uat-checklist.md` (+ KO mirror) — 운영자가 fresh Ubuntu/Rocky VM에서 cross-host 백업/복원까지 따라할 8단계 체크리스트. sidebar 등록 + 양 로케일 build SUCCESS.
- ⏸ 멀티 PostgreSQL 버전 (16.x → 17.x) 마이그레이션 — 운영자 체크리스트 §6에 "선택" 절차로 문서화만. 자동 검증은 별도 chore.

---

## 우선순위 3 — Demo SaaS / GA 준비

### ~~Chore F — GCP Terraform + Cloud Run + seed_demo~~ ✅ PR #33 (2026-05-09)
**머지 commit**: `f684ed3`

처리 결과:
- ✅ `terraform/`: Cloud Run × 2 + Cloud SQL PG17 db-f1-micro + Memorystore 1 GB BASIC + VPC peering + Secret Manager
- ✅ 비용 추정 ~$46/월 (Memorystore = 78%)
- ✅ `apps/backend/scripts/seed_demo.py`: 1 org / 3 teams / 5 users / 5 projects / 10 fake CVEs / 5 license findings — APP_ENV in {dev,demo} guard
- ✅ `docs/installation/gcp-deploy.md` + `.ko.md` 운영 runbook

### ~~Chore G — Admin OAuth identity 관리 UI~~ ✅ PR #33 (2026-05-09)
**머지 commit**: `f684ed3`

처리 결과:
- ✅ `/profile` 페이지 + Connected Accounts 섹션
- ✅ Unlink 버튼 + inline 확인 + 9 (`urn:trustedoss:problem:oauth_unlink_blocks_login`) 시 inline 빨강 배너
- ✅ 백엔드: `GET / DELETE /v1/users/me/oauth-identities[/{id}]` — 마지막 인증 수단 보호 (password 없고 OAuth 1개 → 409 차단), 감사 로그 (provider_user_id sha256 hashed)

---

## 우선순위 4 — 보안·성능·릴리스 강화

### ~~Chore H — SAST HARD FAIL 전환~~ ✅ PR #30 (2026-05-09)
**머지 commit**: `3beb997`

처리 결과:
- ✅ bandit HARD FAIL on High+
- ✅ semgrep HARD FAIL on ERROR
- ✅ Trivy 2-step split: HARD FAIL on CRITICAL + HIGH advisory
- ✅ 21개 ERROR finding triage (`.semgrepignore` + 인라인 nosemgrep — 모두 false-positive 정당화 첨부)

### ~~Chore I — 부하 테스트 (Locust)~~ ✅ PR #30 (2026-05-09)
**머지 commit**: `3beb997`

처리 결과:
- ✅ `tests/load/locustfile.py`: AuthenticatedUser × 4 weighted GETs + ScanTriggerUser × 1
- ✅ `docker-compose.load.yml`: locust master + 2 worker
- ✅ `tests/load/README.md`: p95 < 1s 목표 검증 + 운영 runbook
- ✅ CI 미적용 (staging-only)

### ~~Chore J — SCA on self (dog-fooding)~~ ✅ PR #30 (2026-05-09)
**머지 commit**: `3beb997`

처리 결과:
- ✅ `.github/workflows/sca-self.yml`: nightly cron 07:00 UTC + workflow_dispatch
- ✅ cdxgen 12.3.3 → CycloneDX SBOM → Trivy 0.58.0 → CRITICAL ≥ 1 시 GitHub Issue 자동 생성/업데이트, 0 시 close
- ✅ README "SCA self-scan" 배지 + 섹션

### ~~Chore K — v2.0.0 정식 릴리스~~ ✅ tag `v2.0.0` (2026-05-09)
**머지 commit (CHANGELOG)**: `727942b`
**Release**: https://github.com/trustedoss/trustedoss-portal/releases/tag/v2.0.0

처리 결과:
- ✅ CHANGELOG.md `## [2.0.0]` 섹션 (rc.1 promotion + PR #28~#31 변경)
- ✅ `git tag v2.0.0` 푸시
- ✅ GitHub Release 발행 (CHANGELOG body)

---

## 우선순위 5 — 테스트 / 코드 품질

### ~~Chore L — API Keys / Webhooks 백엔드 테스트 보강~~ ✅ PR #31 (2026-05-09, 부분)
**기반 PR**: #20 (Step 5)
**브랜치**: `chore/phase5-pr16-tests-plus-release`

처리 결과:
- ✅ `tests/unit/services/test_api_key_service.py`: 52 unit tests (coverage 88.24%)
- ✅ `tests/integration/test_api_keys_api.py`: 30 tests (1 xfail)
- ⚠️ `tests/integration/test_webhooks_github.py`: 23 tests (6 xfail)
- ⚠️ `tests/integration/test_webhooks_gitlab.py`: 20 tests (6 xfail)

### ~~Chore L2 — Webhook test fixture HMAC drift fix~~ ✅ 완료 (2026-05-09)
**기반 PR**: #31 (Chore L 잔여)
**처리 결과**: 13 xfail → 13 PASS (+ 3 추가 적대적 입력 parametrize). 실제 원인은 fixture commit 누락이 아니라:
1. **테스트 격리**: webhook fixture 의 `git_url` 이 모든 테스트에 동일한 상수 (`https://github.com/acme/widgets`) → DB 가 세션 간 truncate 되지 않아 19개+ 의 동일 git_url 프로젝트가 누적 → `_find_project_by_git_url(...).first()` 가 임의의 stale 프로젝트 (다른 webhook_secret) 를 반환 → HMAC 401. fix: `unique_suffix()` 로 per-call 고유 URL.
2. **`MissingGreenlet` 회귀 (실제 backend 버그)**: `_record_delivery` 의 IntegrityError rollback 이후 `project.id` 접근 시 lazy reload 가 greenlet 외부에서 발생. fix: `services/webhook_service.py` 에서 rollback 가능 호출 전에 `project_id_str = str(project.id)` 캐시.
3. **NUL/CRLF 방어 (실제 backend 버그)**: 컨트롤 바이트가 들어간 git_url 을 그대로 `WHERE git_url IN (...)` 에 넘겨 asyncpg `CharacterNotInRepertoireError` 로 500. fix: `_normalize_repo_url` 이 `\x00`/C0 control bytes 면 `None` 반환 → 깨끗한 404.
4. **API Key**: `?page_size=500` 이 `Query(le=200)` 를 위반 → 422. fix: 200 으로 정정.

세부는 PR #35 본문 참조.

---

## 진행 순서 권장

병렬 가능한 항목은 한 세션에 묶어서 처리:

| 세션 | 묶음 | PRs | 결과 |
|-----|------|-----|------|
| 1 | 우선순위 1 (UI) | A1 + B + C | ✅ PR #28 (`df5bb5e`) |
| 2 | 우선순위 2 (운영) | D | ✅ PR #29 (`f2b9f9e`) |
| 3 | 우선순위 4 (보안·성능) | H + I + J | ✅ PR #30 (`3beb997`) |
| 4 | 우선순위 5 + 4 K | L + K | ✅ PR #31 (`06559d8`) + tag `v2.0.0` (`727942b`) |
| 5 | 우선순위 3 (Demo SaaS) | F + G | ✅ PR #33 (`f684ed3`) |
| 6 | 우선순위 1 (잔여) | A2 | ✅ PR #32 (`df54562`) |

**기존 11 chore 처리 완료.** v2.0.0 GA 후속 정리 단계로 6 chore 추가 등재.

---

## Post-GA 정리 (v2.0.0 후속)

> 다음 세션 시작 prompt: `docs/sessions/_next-session-prompt-post-ga-cleanup.md`.
> 우선순위 순서로 한 PR = 한 chore.

### ~~Chore M — 문서화 회수 (`docs-site/`)~~ ✅ PR #34 (2026-05-09)
**우선순위**: 1 (CLAUDE.md §8 "문서 동행" 위반 회수)
**브랜치**: `chore/docs-refresh-post-ga`
**처리 결과**: EN 8개 신규 + KO 13개 미러 + 3개 갱신 + sidebars/intro/.gitignore 정비. EN/KO Docusaurus build 양쪽 SUCCESS.
- user-guide/{auth-and-profile, notifications, integrations}.md 신규 (EN + KO)
- admin-guide/{backup-and-restore, api-keys}.md 갱신 (UI 사용법 추가)
- contributor-guide/* 신규 디렉토리 (4 파일 EN + KO)
- ci-integration/* KO 미러 (4 파일)
- reference/* KO 미러 (3 파일)
- intro.md GA 배지 + What's new + release-notes/v2.0.0.md
- `docs/installation/gcp-deploy*.md` → `docs-site/docs/installation/` 이동 + sidebar 등록

### ~~Chore N — UAT 시나리오 갱신 (PR #28 ~ #33)~~ ✅ PR #37 (2026-05-09)
**우선순위**: 4
**브랜치**: `chore/uat-scenarios-v2.0.0`

**처리 결과**: `docs/sessions/2026-05-09-uat-v2.0.0-scenarios.md` 신규 작성 (950 라인). 시나리오 16~27 (12개 / 44개 sub-scenario). PR #28~#33 GA 신규 기능 망라 — forgot/reset, OAuth GitHub/Google, `/profile` Connected Accounts + Unlink, `/integrations` API Key + Webhook, `/admin/backup` UI + typing-gate, 알림 벨 + Inbox + Preferences (in-app always-on 가드 포함), WebSocket 재연결, GCP Demo SaaS 배포, EN/KO 토글.

### ~~Chore O — security-reviewer pass (PRs #29 / #32 / #33)~~ ✅ PR #36 (2026-05-09)
**우선순위**: 3 (CLAUDE.md §7 Producer-Reviewer 회수)
**브랜치**: `chore/security-reviewer-pass`

**처리 결과** — security-reviewer 에이전트 사후 검토. 0 Critical / 3 High / 5 Medium / 4 Low / 2 Info(passed). Tier 1+2의 H1/H2/H3/M2/M3 본 PR에서 fix:
- **H1** OAuth identity unlink TOCTOU race → `with_for_update()` 행 lock + 회귀 가드 테스트 (`test_unlink_acquires_for_update_lock_on_owning_user`)
- **H2** Terraform `__DB_PASSWORD__` placeholder 미동작 → 4분리 env (`DB_USER/PASSWORD/HOST/NAME`)로 변경, `core/config.database_url()` 런타임 합성
- **H3** Backup restore decompression bomb → 멤버 5GiB / 누적 50GiB cap + 413 + RFC 7807 problem
- **M2** Demo super-admin 비밀번호 하드코드 → `_resolve_demo_password()` 런타임 함수, `DEMO_SUPER_ADMIN_PASSWORD` env 우선, dev/demo는 랜덤 + stdout JSON 1회 출력
- **M3** In-app notification opt-out drift → 422 가드 + RFC 7807 problem, frontend 계약 정합

이월 (별도 chore — 본 backlog의 신규 항목 Q/R/S/T/U로 등재):
- M1 (Cloud Run backend 앞 Cloud Armor / WAF + `--no-allow-unauthenticated`)
- M4 (`_NAME_RE` regex가 uuid-suffix manual-* 형식 거부 — restore upload 충돌 케이스)
- M5 (`scripts/restore.sh`의 `BACKUP_RESTORE_CONFIRM` env 우회 가능성 → argv flag로 변경)
- L1 (notification.link 스킴 검증 — `//evil.com` 프로토콜-상대 URL 우회 방지)
- L2 (Memorystore AUTH + transit encryption)
- L3 (audit 로그에 masked actor email 추가 — structlog binding)
- L4 (`provider_user_id_hash`에 HMAC salt — GitHub 숫자 user-id rainbow 방어)

### Chore Q — Cloud Run backend 외부 노출 가드 (M1 이월)
**우선순위**: 7 (Demo SaaS 운영 단계 진입 시 필수)
**브랜치 제안**: `chore/cloud-run-backend-armor`
**예상 소요**: 0.5 세션

미흡 — 현재 `terraform/modules/cloud_run_*/main.tf`에서 `roles/run.invoker` → `allUsers` 바인딩으로 backend가 직접 외부 노출. Cloud Armor / IAP / Cloud LB 없이 FastAPI 전체 경로(login + admin)가 공개:
- 옵션 A: `google_compute_security_policy` (Cloud Armor) + rate-limit + WAF rules + external HTTPS LB
- 옵션 B: Cloud Run을 internal-only로 전환 + IAP 또는 LB 우회 단일 진입점 강제

### Chore R — Backup upload 이름 충돌 + 정리 누수 (M4 + M5 이월)
**우선순위**: 7 (운영 발견 시 사용성 저하)
**브랜치 제안**: `chore/backup-upload-name-and-confirm-flag`
**예상 소요**: 0.5 세션

미흡:
- M4: `target_path` 충돌 시 `manual-<utc>-<uuid6>` fallback이 `_NAME_RE = ^(auto|manual)-\d{8}T\d{6}Z$`를 위배 → restore task에서 `BackupNotFoundError` + orphan 디렉토리 잔존. 해결: 동초 충돌 시 `time.sleep(1)` + 재시도, 또는 regex에 uuid 접미사 허용
- M5: `scripts/restore.sh`가 `BACKUP_RESTORE_CONFIRM=yes` env로 y/N 프롬프트 스킵 → 환경변수 leak 시 Confused Deputy. argv 플래그 (`--confirm`)로 전환

### Chore S — Notification link / Memorystore AUTH (L1 + L2 이월)
**우선순위**: 8
**브랜치 제안**: `chore/notification-link-and-redis-auth`
**예상 소요**: 0.5 세션

미흡:
- L1: `Notification.link` 검증 — `/`로 시작하고 `//`로 시작하지 않을 것 (Pydantic validator + 서버 가드)
- L2: Memorystore Redis `auth_enabled = true` + `transit_encryption_mode = "SERVER_AUTHENTICATION"` + AUTH string Secret Manager binding

### Chore T — Audit 로그 PII 보강 + provider_user_id_hash salt (L3 + L4 이월)
**우선순위**: 8
**브랜치 제안**: `chore/audit-pii-and-provider-hash-salt`
**예상 소요**: 0.5 세션

미흡:
- L3: backup trigger / delete 로그에 `actor_email=mask_pii(actor.email)` 추가
- L4: `_hash_provider_user_id`에 HMAC salt — `hashlib.blake2b(provider_user_id, key=settings.AUDIT_HASH_KEY)`. GitHub 숫자 user-id (~2^31)가 SHA-256 단독으로는 단일 GPU rainbow 가능

### ~~Chore P — Trivy HIGH hard-fail + worker-image refresh~~ ✅ PR TBD (2026-05-09)
**브랜치**: `chore/phase8-worker-image-refresh`

처리 결과 (Dockerfile.worker base 의존성 bump):
- ✅ `python:3.12.7-slim` → `python:3.12.13-slim` (6 patch CPython security fixes)
- ✅ Go SDK `1.22.7` → `1.25.10` (CVE-2025-68121 CRITICAL crypto/tls 수정 포함; .trivyignore 에서 해당 엔트리 제거)
- ✅ Gradle `8.10.2` → `8.14.3` (latest 8.x; 9.x major bump 회피 — Java 8/11 source compat 유지)
- ✅ npm `11.13.0` → `11.14.1` (latest 11.x; cross-spawn-free + bundled-tar 7.x 유지)
- ✅ ORT `85.0.0` → `85.1.1` (85.x 패치라인 — JRE 21 그대로, `ort evaluate` CLI 변경 없음)
- ✅ cdxgen `12.3.3` 유지 (이미 npm registry latest)
- ✅ Trivy `0.70.0` 유지 (이미 GitHub releases latest)
- ✅ `.trivyignore` 정비:
  - 제거 1건 (CVE-2025-68121 — Go bump으로 해소)
  - 갱신 3건 (json-smart / bcprov-in-jruby / msgpack-core — re-evaluate 일정 2026-11-09 로 갱신, ORT 85.1.1 reach surface 명시)
  - 추가 0건
- ✅ CI workflow: Trivy 두 step (CRITICAL hard / HIGH advisory) 통합 → `severity: CRITICAL,HIGH` 단일 hard-fail step

회귀 가드:
- CI image-scan job 이 본 PR 의 1차 검증 (HIGH 잔여 0 보장)
- 통합 테스트 (`apps/backend/tests/integration/`) 가 worker image 변경 후 SBOM/scan 출력 회귀 캡처

---

## 새 세션 시작 시 사용

`docs/sessions/_next-session-prompt-post-ga-cleanup.md` 파일이 작성됨 (2026-05-09).
새 세션 첫 메시지에 그 파일 내용을 그대로 붙여넣으면 정확한 컨텍스트로 시작.

이전 세션 prompt (`_next-session-prompt-chore-backlog.md`) 는 11 chore 모두 처리 후 **deprecated**.
