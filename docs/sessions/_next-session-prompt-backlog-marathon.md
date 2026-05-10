---
session_prompt: Backlog marathon — post-Session 4 잔여 일괄 정리
target_branches: 9 묶음 (A4+A5 → D1 → D2 → R+S+T → 4a → Q → API-path → L1 → 4b~4f)
estimated_total: ~12 세션 (+ v2.1 sprint 별도)
date_authored: 2026-05-10
authoring_session: post-Session 4 (PR #59 머지 후)
status: ready
---

# 다음 세션 시작 prompt — Backlog 잔여 marathon

> 본 prompt 는 `main` 의 backlog 잔여를 권장 순서대로 1 묶음 = 1 PR 로 처리하는 자율 실행 가이드다.
> **새 세션 시작 시 첫 메시지로 본 파일 경로를 그대로 인용**하면 메인 세션이 컨텍스트를 자동 복원한다.

## 0. 컨텍스트 (자동 복원)

- **레포**: `github.com/trustedoss/trustedoss-portal`, main HEAD = PR #59 (`24a075a`) 직후 또는 그 이후.
- **단일 진실 문서**: `docs/v2-execution-plan.md`, `docs/chore-backlog.md`, `CLAUDE.md`.
- **이전 세션**: `docs/sessions/2026-05-10-screenshot-series-cleanup.md` (Session 4 cleanup 머지 완료).
- **사용자 정책 (메모리)**:
  - `git push` + `gh pr create` 는 직접 수행 가능.
  - `force-push` / `git reset --hard` / `rm -rf` / `prune` 등 destructive 는 명시 승인 필요.
  - PR 머지는 **사용자**가 직접. Claude 는 `--admin` 자동 머지 사용 금지.

## 1. 공통 진행 정책

### 1.1 묶음 단위 라이프사이클

각 묶음은 다음 7단계로 진행:

1. `git checkout main && git pull --ff-only` — 직전 묶음의 사용자 머지 결과 반영.
2. 환경 검증 — `docker-compose -f docker-compose.dev.yml ps` 6 healthy + `docker-compose exec -T backend alembic upgrade head`.
3. 새 브랜치 — `git checkout -b <branch>` (각 묶음에 명시).
4. 작업 — backend / frontend / docs / tests EN+KO 동시.
5. 검증 — lint / typecheck / 단위 테스트 ≥80% coverage / E2E green / Docusaurus EN+KO build.
6. commit + push + `gh pr create` — Conventional Commits, body 에 Test plan 포함.
7. 사용자 머지 대기 → 머지 시 사용자가 "머지 했어" 알림 → 다음 묶음 시작.

### 1.2 CLAUDE.md 강제 규칙 (생략 금지)

- PostgreSQL only — SQLite 임시 사용 금지.
- Alembic forward-only — `downgrade()` 는 `pass` / `raise NotImplementedError`.
- ORT/cdxgen/Trivy 는 Celery 비동기.
- DT 호출 전 health 확인 + Circuit Breaker.
- 하네스 우선 (test-writer 가 하네스 verb 먼저, 그 다음 spec).
- 새 코드 단위 line coverage **≥80%**.
- EN/KO 번역 동시 — `i18next-parser` drift 0.
- Docusaurus 문서 동행 — 새 기능 = `docs-site/docs/...` 동시 갱신 + KO mirror.
- RFC 7807 Problem Details — 모든 4xx/5xx 응답.
- 비밀번호 ≥ 12자 / bcrypt cost 12 / JWT access 30분 / refresh 7일 + rotation.
- `docker-compose` (V1, 하이픈) — V2 미설치.
- `os.getenv()` 런타임 호출 — 모듈 레벨 캐싱 금지.
- `:latest` 이미지 태그 금지.

### 1.3 Harness 패턴

- **Producer-Reviewer**: 인증 / API Key / DT 연동 / OAuth / 빌드 게이트 / DB cascade / RLS — 구현 후 `security-reviewer` 검증 필수.
- **Fan-out/Fan-in**: 같은 묶음 내 backend + frontend + tests + docs 가 독립이면 병렬 에이전트 호출.
- **Expert Pool**: DB 설계 → `db-designer`, K8s/Terraform → `devops-engineer`, Celery 통합 → `scan-pipeline-specialist`.

### 1.4 메모리 등재 트리거

각 묶음 종료 시 다음 항목 발견되면 `~/.claude/projects/...trustedoss-portal/memory/` 에 등재:

- 사용자 정정 / 비명시 합의 → `feedback_*.md`
- 구조적 결정 / 단발성 외부 의존 → `project_*.md`
- 외부 시스템 위치 → `reference_*.md`

backlog 갱신 ✅ 마킹은 머지 commit hash 와 함께 본 prompt 의 묶음 항목에 추가.

### 1.5 핸드오프 문서

각 묶음 종료 시 `docs/sessions/<YYYY-MM-DD>-<bundle-slug>.md` 작성 (양식: `docs/v2-execution-plan.md` §7).

---

## 2. 묶음 진행 — 9 묶음

### 묶음 1 — A4 + A5: Manual sys-bug fix 회수

- **브랜치**: `chore/manual-sys-bug-fix-a4-a5`
- **추정**: ~1 세션
- **선결**: 없음 (main 위 직접 시작)
- **에이전트**: `db-designer` (A5 migration) → `backend-developer` (A4 endpoint) → `security-reviewer` (Producer-Reviewer)

#### A4 — DT breaker reset endpoint
- **목표**: 운영자가 OPEN 상태에 갇힌 DT breaker 를 강제 CLOSED 전이.
- **변경**:
  - `apps/backend/api/v1/admin/dt.py` — `POST /v1/admin/dt/breaker/reset` (super_admin only). RFC 7807 problem 응답 (404 if breaker not initialized, 409 if already CLOSED).
  - `apps/backend/services/dt_health_service.py` — `force_reset()` 메서드. `circuit_state` 즉시 CLOSED + `failure_count = 0` + 감사 로그 (`actor_user_id`, `event_type='dt_breaker_reset'`, `before_state`, `after_state`).
  - `apps/frontend/src/features/admin/dt/AdminDTPage.tsx` — status 카드에 "Reset breaker" 버튼 (OPEN 상태에서만 enable). `useResetBreaker` hook + 확인 다이얼로그 + toast.
  - `apps/frontend/src/locales/{en,ko}/admin.json` — `admin_dt.breaker.reset.*` 키 4개 (label / confirm_title / confirm_body / toast_done).
  - 단위 테스트: `tests/unit/services/test_dt_health_service.py::test_force_reset_*`.
  - 통합 테스트: `tests/integration/test_admin_dt_api.py::test_reset_breaker_*` (super_admin 만, audit 로그 1건, OPEN→CLOSED, 409 if CLOSED).
  - Docusaurus: `docs-site/docs/admin-guide/dt-connector.md` 의 "Troubleshooting" 섹션에 reset 절차 + KO mirror.

#### A5 — Last super_admin DB-level CHECK constraint
- **목표**: application 우회 시에도 마지막 super_admin role 변경/삭제 차단.
- **변경**:
  - Alembic migration `apps/backend/alembic/versions/00XX_last_super_admin_constraint.py`:
    ```python
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_last_super_admin() RETURNS TRIGGER AS $$
        BEGIN
            IF (TG_OP = 'DELETE' AND OLD.role = 'super_admin')
               OR (TG_OP = 'UPDATE' AND OLD.role = 'super_admin' AND NEW.role <> 'super_admin') THEN
                IF (SELECT count(*) FROM users WHERE role = 'super_admin'
                    AND id <> OLD.id AND deleted_at IS NULL) = 0 THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'last super_admin cannot be removed or demoted';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_last_super_admin
        BEFORE UPDATE OR DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION enforce_last_super_admin();
    """)
    ```
  - `apps/backend/services/user_service.py` — application 가드는 그대로 유지 (depth-in-defense). DB exception 캐치 시 422 + RFC 7807 (`urn:trustedoss:problem:last_super_admin_required`).
  - 통합 테스트: `tests/integration/test_users_admin_api.py::test_last_super_admin_db_trigger_blocks_*` (raw SQL UPDATE / DELETE 시도 → trigger 차단).
  - Docusaurus: `docs-site/docs/admin-guide/users-and-teams.md` 의 Roadmap 섹션에서 sys-bug-u&t-1 항목 제거 + 본문에 DB-level 가드 명시 (EN+KO).

#### 검증
- `pytest apps/backend/tests/ -k "test_force_reset or test_reset_breaker or test_last_super_admin_db_trigger" -v`
- `cd apps/frontend && npm run lint && npx tsc --noEmit`
- `cd docs-site && npm run build`
- `make screenshots-capture` (회귀 가드, 자산 변동 없을 것)

#### Producer-Reviewer
- `security-reviewer` 호출:
  > "PR `chore/manual-sys-bug-fix-a4-a5` 의 A4 admin endpoint (RBAC + 감사 로그 + RFC 7807) 와 A5 DB trigger (FK cascade / RLS / role 분리 미적용 환경에서의 우회 가능성) 을 검토해라. 특히 ondelete CASCADE 가 trigger 를 우회하는지, super_admin 의 soft delete (`deleted_at IS NOT NULL`) 가 카운트에서 빠지는지, 통합 테스트가 raw SQL 우회 케이스를 cover 하는지 확인."

#### commit / PR
- commit: `fix(manual-sys-bugs): A4 DT breaker reset endpoint + A5 last super_admin DB trigger`
- PR title: `fix(manual-sys-bugs): A4 + A5 회수`
- backlog 갱신: sys-bug-dt-1 + sys-bug-u&t-1 → ✅ + 머지 commit hash.

---

### 묶음 2 — D1: Phase 5 fixme 잔여 (auth_and_profile test 3)

- **브랜치**: `fix/seed-no-password-flag`
- **추정**: ~0.5~1 세션
- **선결**: 묶음 1 머지 (충돌 가능성 낮으나 main 동기화 위해)

#### 작업
- `apps/backend/scripts/seed_e2e_user.py` — `--no-password` flag 추가. 활성화 시 `hashed_password = NULL` + `oauth_identities` 행 1개 필수 (인증 수단 0 차단).
- `apps/frontend/tests/_harness/seed.ts` — `seedE2eUser({ withPassword: false })` 옵션 mirror. 기본 `true` 로 회귀 차단.
- `apps/frontend/tests/e2e/manual-aligned/auth_and_profile.spec.ts` — test 3 (last-only blocks-login alert) `test.fixme` 해제. 시드 옵션 사용 + URN `urn:trustedoss:problem:oauth_unlink_blocks_login` 검증 + inline 빨강 배너 visibility.
- 단위 테스트 (Python): `tests/unit/scripts/test_seed_e2e_user.py::test_no_password_requires_oauth_identity` (인증 수단 0 시 `ValueError`).

#### 검증
- `pytest apps/backend/tests/unit/scripts/test_seed_e2e_user.py -v`
- `cd apps/frontend && npx playwright test tests/e2e/manual-aligned/auth_and_profile.spec.ts --grep "blocks-login"` (3 시나리오 모두 green)
- `npm run lint && npx tsc --noEmit`

#### commit / PR
- commit: `fix(seed): --no-password flag for OAuth-only e2e users (Phase 5 fixme D1)`
- backlog 갱신: D1 fixme → ✅.

---

### 묶음 3 — D2: tasks.backup shell 의존 제거

- **브랜치**: `chore/backup-task-no-shell-deps`
- **추정**: ~1.5 세션
- **선결**: 묶음 2 머지
- **에이전트**: `backend-developer` + `test-writer` + `security-reviewer`

#### 작업
- **결정**: backlog 옵션 (b) — `tasks.backup` 을 `DATABASE_URL` 직접 사용으로 리팩터링. shell 의존 제거.
- `apps/backend/tasks/backup.py`:
  - 기존 `subprocess.run(["bash", "scripts/backup.sh", ...])` 제거.
  - `pg_dump` 를 asyncpg 가 아닌 별도 binary 호출로 유지하되 worker 이미지에 `postgresql-client-17` 설치 (Dockerfile.worker apt-get).
  - 또는 **순수 Python 옵션 (권장)**: `asyncpg` 로 `pg_dump` 의 minimal subset 재구현 — `\copy` for table data + `pg_catalog` schema dump. 그러나 복잡도 큰 경우 `postgresql-client` 설치 fallback.
  - 출력: tar.gz archive (with manifest + checksum) → `WORKSPACE_HOST_PATH/backups/<name>/`.
- `apps/backend/Dockerfile.worker` — `postgresql-client-17` 추가 (option A 의 경우).
- `scripts/backup.sh` 는 host 운영자용으로 유지 (변경 없음).
- 통합 테스트: `tests/integration/test_backup_task.py` — Postgres 컨테이너에 시드 데이터 생성 → backup task 실행 → tar.gz integrity → restore 라운드트립.
- E2E: `apps/frontend/tests/e2e/manual-aligned/admin_backup.spec.ts` test 4 (manual trigger row check) `test.fixme` 해제.

#### Producer-Reviewer
- `security-reviewer`:
  > "tasks.backup 리팩터링이 (1) DATABASE_URL 의 password 가 subprocess argv 또는 logs 로 leak 되는지, (2) tar archive 의 path traversal 가드 (member name `..` / 절대경로 차단), (3) decompression bomb 가드 (PR #36 H3 와 동일 5GiB / 50GiB cap 유지), (4) backup name regex (`_NAME_RE`) 와의 정합성을 검증."

#### commit / PR
- commit: `refactor(backup): remove shell dependency from tasks.backup (Phase 5 fixme D2)`
- backlog 갱신: D2 fixme → ✅, "celery-worker docker-compose V1 missing" 환경 chore → ✅ (해소).

---

### 묶음 4 — R + S + T: Post-GA 보안 잔여

- **브랜치**: `chore/post-ga-security-r-s-t`
- **추정**: ~1.5 세션
- **선결**: 묶음 3 머지
- **에이전트**: `backend-developer` + `devops-engineer` (S 의 Memorystore 부분) + `security-reviewer`

#### Chore R — Backup upload 이름 충돌 + 정리 누수 (M4 + M5)
- **M4 fix**:
  - `apps/backend/services/backup_service.py` — `target_path` 충돌 시 `time.sleep(1)` + 재시도 (최대 3회). 그래도 충돌이면 `ConflictError` (409 + RFC 7807 `urn:trustedoss:problem:backup_name_collision`).
  - `_NAME_RE` 그대로 유지 (uuid 접미사 미허용 — 충돌 방지가 본질적 해결).
- **M5 fix**:
  - `scripts/restore.sh` — `BACKUP_RESTORE_CONFIRM=yes` env 처리 제거. argv flag `--confirm` 으로 전환.
  - `.github/workflows/install-uat.yml` — `bash restore.sh ... --confirm` 으로 호출 변경.
  - `docs-site/docs/installation/uat-checklist.md` (EN+KO) — env 인용 제거, `--confirm` flag 명시.

#### Chore S — Notification link 검증 + Memorystore AUTH (L1 + L2)
- **L1 fix**:
  - `apps/backend/schemas/notification.py` — `Notification.link` 필드에 Pydantic validator. 정규식: `^/[^/].*` 또는 `None`. (`/` 시작 + `//` 시작 아님).
  - `apps/backend/services/notification_service.py` — DB write 직전 가드 (validator 우회 케이스 차단).
  - 단위 테스트: 적대적 입력 parametrize — `//evil.com`, `javascript:`, `data:`, `\\\\evil.com`, `/\\evil.com`, NULL byte, CRLF.
- **L2 fix**:
  - `terraform/modules/memorystore/main.tf` — `auth_enabled = true` + `transit_encryption_mode = "SERVER_AUTHENTICATION"`.
  - AUTH string 을 Secret Manager 에 저장 후 backend / worker 에 binding.
  - `terraform/README.md` 에 회전 절차 명시.

#### Chore T — Audit 로그 PII 보강 + provider_user_id_hash salt (L3 + L4)
- **L3 fix**:
  - `apps/backend/services/backup_service.py` 의 trigger / delete 경로 — structlog binding 에 `actor_email = mask_pii(actor.email)` 추가.
  - `apps/backend/utils/log_masking.py::mask_pii` 의 email 분기 회귀 테스트 (이미 있으면 재확인).
- **L4 fix**:
  - `apps/backend/services/oauth_service.py::_hash_provider_user_id` — `hashlib.sha256(...)` → `hashlib.blake2b(provider_user_id_bytes, key=settings.AUDIT_HASH_KEY.encode())`.
  - `apps/backend/core/config.py` — `AUDIT_HASH_KEY` env (32 bytes random). `.env.example` 추가.
  - 마이그레이션 절차: 기존 `provider_user_id_hash` 컬럼은 그대로 두고 새 컬럼 `provider_user_id_hmac` 추가 → 다음 OAuth 로그인 시 자동 채움 (lazy backfill). 6개월 후 별도 chore 에서 old 컬럼 제거.

#### Producer-Reviewer
- `security-reviewer`:
  > "R/S/T 묶음의 (1) `_NAME_RE` 와 충돌 재시도 race, (2) `--confirm` flag 의 argv 노출 (process listing leak), (3) Notification link validator 의 unicode confusable / IDN homograph 우회, (4) Memorystore AUTH string 회전 시 downtime, (5) `AUDIT_HASH_KEY` 의 회전 정책 / 기존 hash 와의 매칭 정책 검토."

#### commit / PR
- commit: `chore(security): R+S+T post-GA backlog (backup name / notification link / audit PII)`
- backlog 갱신: Chore R/S/T → ✅.

---

### 묶음 5 — Screenshot OUT 4a: 헤더 종 unread badge 캡처

- **브랜치**: `chore/screenshot-bell-with-badge`
- **추정**: ~0.5 세션
- **선결**: 묶음 4 머지

#### 작업
- `apps/backend/scripts/seed_e2e_user.py` — `--with-notifications COUNT` flag (default 0). 활성화 시 unread `notifications` 행 N 개 생성 (kind 분산: `scan_completed`, `cve_detected`, `policy_gate_failed`).
- `apps/frontend/tests/_harness/seed.ts` — `seedE2eUser({ notificationCount: number })` 옵션 mirror.
- `apps/frontend/tests/screenshots/global-setup.ts` — `notificationCount: 3` 추가.
- `apps/frontend/tests/screenshots/capture_user_guide.spec.ts` — 신규 spec `user-notifications-bell` (PortalPage 의 dashboard 또는 `/projects` 페이지에서 헤더 영역 visible 한 viewport 캡처).
- `docs-site/docs/user-guide/notifications.md` (EN+KO) — Session 4 에서 제거한 placeholder 라인 자리에 `/img/screenshots/user-notifications-bell.png` 삽입.

#### 검증
- `make screenshots-capture` — 30/30 pass.
- Docusaurus EN+KO build SUCCESS.

#### commit / PR
- commit: `chore(screenshots): bell-with-unread-badge capture (Session 4 OUT 4a)`
- backlog 갱신: 4a → ✅, 총합 PNG 27개.

---

### 묶음 6 — Q: Cloud Run backend 외부 노출 가드

- **브랜치**: `chore/cloud-run-backend-armor`
- **추정**: ~0.5 세션
- **선결**: 묶음 5 머지
- **에이전트**: `devops-engineer` + `security-reviewer`
- **차단 요소**: Demo SaaS 운영 진입 일정. 사용자 noise 따라 우선순위 조정 가능 (skip → 묶음 7).

#### 작업
- **결정**: backlog 옵션 A — Cloud Armor + external HTTPS LB.
- `terraform/modules/cloud_run_backend/main.tf`:
  - `roles/run.invoker` → `allUsers` 바인딩 제거. `roles/run.invoker` → `allAuthenticatedUsers` 또는 specific service account.
  - `google_compute_backend_service` (Cloud Run NEG) + `google_compute_url_map` + `google_compute_target_https_proxy` + `google_compute_global_forwarding_rule`.
  - `google_compute_security_policy`:
    - rate-limit rule (per-IP 100 RPM)
    - WAF preconfigured rules (`xss-stable`, `sqli-stable`, `lfi-stable`, `rfi-stable`, `rce-stable`)
    - default rule `allow`.
- `terraform/modules/cloud_run_backend/variables.tf` + `outputs.tf` — LB IP / cert refs.
- `apps/backend/main.py` — `TrustedHost` 미들웨어 화이트리스트에 LB hostname 추가.
- 통합 테스트: 신규 `terraform/tests/test_security_policy.py` (terratest 또는 `terraform plan` JSON 검증).
- Docusaurus: `docs-site/docs/installation/gcp-deploy.md` (EN+KO) — Cloud Armor 절차 + Cloud LB cert 발급 절차.

#### Producer-Reviewer
- `security-reviewer`:
  > "Cloud Armor 정책의 (1) WAF rule sensitivity (paranoia level) 가 정상 트래픽 차단하지 않는지, (2) rate-limit 가 합법 CI burst 를 막지 않는지, (3) IAP 우회 가능성, (4) LB SSL cert 자동 갱신 (managed cert) 의 회전 단절 검토."

#### commit / PR
- commit: `chore(infra): Cloud Run backend Cloud Armor + LB (Chore Q post-GA)`
- backlog 갱신: Chore Q → ✅.

---

### 묶음 7 — API path consistency `/api/v1` vs `/v1`

- **브랜치**: `chore/api-path-consistency`
- **추정**: ~0.5 세션
- **선결**: 묶음 6 머지

#### 작업
- **검증**: `git grep -rn "/api/v1" apps/ docs-site/ docs/ scripts/ .github/` — 발견된 위치 일람.
- 매뉴얼 (PR #43) 이 통일한 기준은 `/v1`. 따라서 모든 `/api/v1` → `/v1` 정정.
- 일반적 발생 위치:
  - `apps/frontend/src/lib/*.ts` (axios baseURL, fetch URL)
  - `apps/frontend/src/features/*/api*.ts`
  - `apps/backend/main.py` (router prefix — 이미 `/v1` 추정, 검증 필요)
  - `.github/workflows/*.yml` (smoke test URL)
  - `scripts/install.sh` health probe URL
  - `tests/load/locustfile.py`
  - 외부 SDK: `examples/github-action/`, `examples/gitlab-ci/`, `examples/jenkins/Jenkinsfile`
- E2E 회귀: `make screenshots-capture` + `npx playwright test --grep @manual-aligned` 모두 green.

#### commit / PR
- commit: `chore(api): unify all references to /v1 prefix (drop /api/v1 drift)`
- backlog 갱신: API path consistency → ✅.

---

### 묶음 8 — L1: PostgreSQL role 분리

- **브랜치**: `chore/postgres-role-separation`
- **추정**: ~1.5 세션
- **선결**: 묶음 7 머지
- **에이전트**: `db-designer` + `backend-developer` + `security-reviewer`

#### 작업
- **목표**: `audit_logs` trigger (PR #48) 의 runtime 우회 가능성 제거. backend runtime 을 DML-only role 로 격리.
- Alembic migration `apps/backend/alembic/versions/00XX_role_separation.py`:
  ```sql
  CREATE ROLE trustedoss_owner WITH LOGIN PASSWORD '<from env>';  -- migrations
  CREATE ROLE trustedoss_app WITH LOGIN PASSWORD '<from env>';    -- runtime
  GRANT USAGE ON SCHEMA public TO trustedoss_app;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trustedoss_app;
  REVOKE TRUNCATE ON audit_logs FROM trustedoss_app;
  REVOKE UPDATE, DELETE ON audit_logs FROM trustedoss_app;  -- trigger 의존도 낮춤
  GRANT INSERT, SELECT ON audit_logs TO trustedoss_app;
  -- migrations 는 owner role 로
  ALTER TABLE <every-table> OWNER TO trustedoss_owner;
  ```
- `apps/backend/core/config.py` — `DATABASE_URL` 분리:
  - `DATABASE_URL_APP` (runtime, `trustedoss_app`)
  - `DATABASE_URL_OWNER` (alembic only, `trustedoss_owner`)
- `apps/backend/alembic/env.py` — `DATABASE_URL_OWNER` 사용.
- `apps/backend/main.py` + `apps/backend/tasks/celery_app.py` — `DATABASE_URL_APP` 사용.
- `docker-compose.yml` + `.env.example` — 두 URL.
- `scripts/install.sh` — 두 role / 두 password 자동 생성.
- 회귀: 모든 통합 테스트가 app role 로 실행 (DDL 시도 시 fail), 단 alembic migration 만 owner role.
- 통합 테스트 신규: `tests/integration/test_role_separation.py` — app role 이 (1) audit_logs UPDATE/DELETE/TRUNCATE 시도 → 권한 거부, (2) 정상 INSERT 가능, (3) DDL 시도 → 권한 거부.

#### Producer-Reviewer
- `security-reviewer`:
  > "L1 role 분리가 (1) PR #48 의 audit_logs trigger 와 어떻게 상호작용하는지 (trigger 가 runtime 권한과 독립인지), (2) Celery task 의 DB 연결이 app role 인지 owner role 인지, (3) restore 절차에서 owner role 의 password rotation 시 backend 재시작 필요 여부, (4) Cloud SQL / Memorystore 환경에서 두 role 의 IAM 별도 관리 가능성을 검토."

#### commit / PR
- commit: `chore(security): postgres role separation (audit_logs trigger 강화)`
- backlog 갱신: L1 → ✅.

---

### 묶음 9 — Screenshot OUT 4b ~ 4f: 시각 자료 강화

5개 sub-항목을 단일 PR 또는 분할 PR 로 처리. 각 sub 가 ~0.5~1.5 세션이라 분할 권장.

#### 9-1. 4b — Visual regression CI (`chore/screenshot-visual-regression-ci`)
- **결정**: backlog 옵션 C — Playwright `expect(page).toHaveScreenshot()` + GitHub Actions cache (self-hosted).
- 신규 spec `apps/frontend/tests/screenshots/regression.spec.ts` — 26 PNG 의 baseline 등록 + `--update-snapshots` Makefile target.
- `.github/workflows/visual-regression.yml` — PR 트리거 (해당 라벨 또는 docs-site/* 변경 시) + diff artifact upload.
- 추정: ~1.5 세션.

#### 9-2. 4c — Animated walkthroughs (`chore/screenshot-animated-walkthroughs`)
- 5~10초 워크플로 시연: backup round-trip, OAuth login, approval workflow.
- Playwright `video: 'on'` + ffmpeg 후처리 (1080×675, 30fps, mp4 + gif).
- 산출물 위치: `docs-site/static/video/walkthroughs/*.mp4`, `*.gif`.
- 마크다운 삽입: 해당 user-guide 페이지에 `<video controls>` (Docusaurus MDX 지원).
- 추정: ~1 세션.

#### 9-3. 4d — Locale-specific KO 캡처 (`chore/screenshot-ko-locale-specific`)
- 한글 데이터 가시성이 핵심인 페이지: `/notifications` (한글 본문), `/admin/audit` (한글 메시지), `/projects` 의 한글 프로젝트명.
- 시드 (`seedE2eUser`) 에 KO 옵션 (`localeData: "ko"`) 추가 — 한글 프로젝트명 / 한글 알림 메시지 생성.
- KO Docusaurus 페이지 별도 PNG path 로 분리 (`/img/screenshots/ko/...`).
- 추정: ~1 세션.

#### 9-4. 4e — a11y alt-text 감사 (`chore/screenshot-a11y-alt-audit`)
- `i18n-specialist` 에이전트 호출 — 26 PNG 의 alt text 검토 (screen reader 친화 + 도메인 용어 정확성).
- 업데이트 mark in EN+KO 동시.
- 추정: ~0.5 세션.

#### 9-5. 4f — 이미지 압축 자동화 (`chore/screenshot-compression-automation`)
- Makefile target `screenshots-optimize` — `oxipng -o 4` + `pngquant --quality 75-90` 파이프.
- `.github/workflows/screenshot-size-gate.yml` — PR diff 의 PNG 합계 사이즈가 baseline +10% 초과 시 fail.
- 기존 26 PNG 일괄 최적화 (수치적 압축 — alt text / 픽셀 의미 변경 없음).
- 추정: ~0.5 세션.

#### commit / PR
- 각 sub 별도 commit + PR. backlog 갱신: 4b ~ 4f 각 ✅.

---

## 3. 묶음 종료 조건

모든 9 묶음 머지 완료 시 다음 상태 도달:

- backlog 의 "Manual Walkthrough Verification" + "Post-GA 정리" + "Screenshots automation" 섹션 — 잔여 0.
- v2.1 sprint (B 묶음) 만 남은 상태 — 별도 sprint planning prompt 필요 (`docs/sessions/_next-session-prompt-v2.1-sprint-planning.md`, 본 prompt 와는 분리).

종료 후 사용자 confirmation:
- 본 prompt 파일에 `status: complete` 추가 후 commit (별도 chore PR 또는 마지막 묶음에 묶음).
- 다음 cycle 은 **v2.1 sprint planning** — backlog 의 "B 묶음 v2.1 sprint" 6 기능을 별도 prompt 로 spec → backend → frontend → E2E 4단계 분할.

## 4. 묶음 간 트랜지션 패턴

각 묶음의 PR 가 사용자 머지된 후, 사용자 메시지 "머지 했어" / "다음 묶음" 둘 중 하나로 트리거:

```
사용자: 머지 했어
Claude: <main pull → 묶음 N+1 시작>
```

오류 / 차단 발생 시 (예: Q 의 Demo SaaS 일정 차단) — 사용자에게 묶음 skip 또는 보류 결정 요청.

## 5. 새 세션 시작 패턴

새 세션의 첫 메시지로 다음을 그대로 사용:

> 본 세션은 `docs/sessions/_next-session-prompt-backlog-marathon.md` 에 따라 backlog 잔여를 묶음 단위로 처리한다. 현재 main HEAD 부터 시작해 묶음 1 (A4 + A5) 부터 권장 순서대로 진행. 각 묶음은 머지 후 사용자가 "머지 했어" 알리면 다음 묶음으로 전환.

또는 특정 묶음만 진행 시:

> 본 세션은 `docs/sessions/_next-session-prompt-backlog-marathon.md` 의 묶음 N (...) 만 처리한다.

---

## 6. 위험 요소 / 운영 노트

- **묶음 8 (L1 role 분리)** 가 가장 운영 부담. 기존 dev-stack 데이터 보존 위해 마이그레이션 전 `docker-compose exec postgres pg_dumpall > pre-l1.sql` 스냅샷 권장. dev-reset 후에만 진행.
- **묶음 6 (Q Cloud Armor)** 는 Demo SaaS 운영 일정에 의존. 현재 데모 단계라면 묶음 7 로 우선 skip 가능.
- **묶음 9-3 (KO locale-specific)** 시드 변경이 다른 capture spec 회귀 가능 — 별도 시드 옵션 (`localeData`) 으로 격리, 기본 EN.
- **묶음 3 (D2 backup refactor)** 의 옵션 (a/b) 결정 — 본 prompt 는 (b) 권장이나 worker 이미지 사이즈 +수십 MB 우려 시 (a) (`postgresql-client-17` 설치) 가 더 안전. 첫 시작 시 사용자에게 옵션 확인.
- **묶음 4 의 L4 (provider_user_id_hash salt)** 의 lazy backfill 정책 — 이미 가입한 OAuth 사용자의 hash 가 다음 로그인까지 미정정. 강제 backfill (Celery one-shot task) 옵션 사용자에게 확인.
