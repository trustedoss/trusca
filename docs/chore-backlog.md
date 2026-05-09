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

### Chore E — backup.sh / restore.sh 실제 검증
**기반 PR**: #24 (Step 9)
**브랜치 제안**: `chore/phase7-pr20-install-uat`
**예상 소요**: 0.5 세션

미흡:
- fresh Linux machine에서 `bash scripts/install.sh` end-to-end 시나리오 테스트
- shellcheck CI 게이트 추가 (현재 syntax check만)
- 멀티 PostgreSQL 버전 (16.x → 17.x) 마이그레이션 시나리오 검증

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

### Chore L2 — Webhook test fixture HMAC drift fix
**기반 PR**: #31 (Chore L 잔여)
**예상 소요**: 0.25 세션

PR #31 의 13 xfail 정리:
- fixture가 `project.webhook_secret` 을 setattr 만 하고 `await session.commit()` 누락 → backend 가 DB 에서 다른 secret 으로 검증 → 401.
- 1 API key role-scope test (`test_get_developer_does_not_see_foreign_team_keys`)가 422 반환 — POST validation 시그니처 정렬.
- 모두 `@pytest.mark.xfail(strict=False)` 표시. fix 하면 strict=True 로 전환.

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

**모든 chore 처리 완료.** 잔여 항목 — Chore E (install/restore UAT — fresh Linux 머신 필요), Chore L2 (xfail 13개 fixture drift fix). 둘 다 별도 단일 세션 분량.

---

## 새 세션 시작 시 사용

`docs/sessions/_next-session-prompt-chore-backlog.md` 파일이 작성됨.
새 세션 첫 메시지에 그 파일 내용을 그대로 붙여넣으면 정확한 컨텍스트로 시작.
