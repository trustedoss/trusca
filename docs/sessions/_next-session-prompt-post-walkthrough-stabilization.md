# 다음 세션 시작 prompt — Post-Walkthrough Stabilization (환경 + sys-bug + fixme)

> **DEPRECATED 2026-05-10** — 본 prompt 의 세션 1~3 모두 머지 완료. 통합 핸드오프는 `docs/sessions/2026-05-10-stabilization-cad-bundle.md` 참고.
> - 세션 1 (C 묶음): PR #47 (`c8c44a0`)
> - 세션 2 (A 묶음): PR #48 (`defc7e2`)
> - 세션 3 (D 묶음): PR #49 (`b68b0a5`) — D2 (admin_backup test 4) + auth_and_profile test 3 는 fixme 유지 (사유는 본 prompt 외 chore-backlog 참조)
> - 부산물: docs-site lockfile commit PR #50 (`4401a8a`)
>
> 다음 세션 후보 (chore-backlog.md "Manual Walkthrough Verification" 섹션 참조): A4/A5 chore 합본 / L1 PostgreSQL role 분리 / D2 backup.sh 리팩터링 / D1 잔여 fixme / B 묶음 v2.1 sprint.

> Manual Walkthrough Verification (PR #40 ~ #46, 2026-05-09 ~ 10) 직후 후속.
> 본 세션의 발견 항목 중 즉시 실행 가능한 3 묶음 (환경 안정화 → 시스템 버그 fix → Phase 5 fixme 해소)을 순차적으로 자율 실행.
> 본 파일을 새 세션 첫 메시지에 그대로 붙여넣으면 정확한 컨텍스트로 시작.
> **세션 중간에 끊겨도 본 파일 + main HEAD + chore-backlog.md 만으로 이어 진행 가능.**

---

TrustedOSS Portal v2 작업을 `~/projects/trustedoss-portal/` 에서 이어서 진행한다.

## 0. 현재 상태 (2026-05-10 기준)

- main HEAD = `e4fba24` (Phase 6 — CI matrix split, PR #46)
- 누적 머지: PR #1 ~ #46 + tag `v2.0.0`
- 직전 6 세션 통합 핸드오프: `docs/sessions/2026-05-10-manual-walkthrough-complete.md`
- 본 prompt 의 세션 1~3 은 **walkthrough 후속 안정화** 단계

```bash
git log --oneline -3
# e4fba24 ci: split e2e into matrix [scan-flow, manual-aligned] (Phase 6) (#46)
# e1cccb4 test(e2e): manual-aligned harnesses + 27 scenarios (Phase 5) (#45)
# ad9436e docs(admin-guide): align v2.0.0 manual with shipped behavior (26 drift fixes) (#44)
```

본 작업은 walkthrough 부산물로 식별된 3 카테고리:
- **환경 chore (C 묶음)** — postgres dev volume disk-full / celery-worker stale image / Vite proxy 정합성 (Tier 2 동적 검증 차단 원인)
- **시스템 버그 fix (A 묶음)** — 5건 walkthrough 시스템 버그 중 코드 fix 가능한 3건 (audit immutability / restore 412 / CSV BOM)
- **Phase 5 fixme 해소 (D 묶음)** — PR #45 의 deferred 4 시나리오 (OAuth identity seed + worker manual-trigger)

## 1. 단일 진실

- `docs/sessions/2026-05-10-manual-walkthrough-complete.md` — 직전 세션 통합 핸드오프 (fix 후보 명세)
- `docs/sessions/2026-05-09-user-manual-walkthrough.md` — 시스템 버그 BUG-USR-001/002 원본
- `docs/sessions/2026-05-10-admin-manual-walkthrough.md` — 시스템 버그 sys-bug-* 5건 원본
- `docs/chore-backlog.md` — "Manual Walkthrough Verification (Phase 1 ~ 6)" 섹션 + system bug list + env chore candidates
- `apps/frontend/tests/_harness/{NotificationsHarness,ProfileHarness,AdminBackupHarness}.ts` — Phase 5 산출 (D 묶음에서 fixme 해소)
- `apps/backend/scripts/seed_e2e_user.py` — D1 에서 `--with-oauth-identity` 옵션 추가 대상
- `apps/backend/Dockerfile.worker` — C2 의 stale image 원인
- `apps/frontend/vite.config.ts` — C3 의 Vite proxy 정합성 결정 대상
- `CLAUDE.md` — 핵심 규칙 + 품질·보안 표준 (변경 X)
- 본 파일 — **세션 1~3 의 단일 진실**. 각 세션 prompt 가 self-contained.

## 2. 시작 시 검증 (반드시)

```bash
# 환경 + 진행 상태 확인
git status                                            # working tree clean (untracked는 무시)
git checkout main && git pull --ff-only               # 최신 반영
git log --oneline -3                                  # 최신 commit 확인 (e4fba24 가 HEAD 인지)
gh run list --branch main --limit 3                   # main CI 상태

# 시스템 버그 위치 확인 (A 묶음)
ls apps/backend/api/v1/admin_backups.py               # A2 대상
ls apps/backend/api/v1/admin_audit.py                 # A3 대상
ls apps/backend/models/audit_log.py                   # A1 대상
ls apps/backend/alembic/versions/                     # A1, A5 (deferred) 대상

# 환경 상태 확인 (C 묶음)
docker-compose -f docker-compose.dev.yml ps           # celery-worker 가 restarting 인지 (C2)
docker volume ls | grep trustedoss                    # postgres 볼륨 (C1)
docker images --format "{{.Repository}}:{{.Tag}} {{.CreatedSince}}" | grep trustedoss
                                                      # worker 이미지 age (C2)

# Phase 5 fixme 위치 확인 (D 묶음)
grep -nE "test\.fixme" apps/frontend/tests/e2e/{auth_and_profile,admin_backup}.spec.ts
grep -nE "with.oauth.identity|OAuthIdentity" apps/backend/scripts/seed_e2e_user.py
                                                      # 부재 확인 (D1)
```

main 의 untracked 잔여 (무시):
- `.claude/scheduled_tasks.lock`
- `apps/frontend/@/` (artifact 폴더, .gitignore 처리됨)
- `docs/review-binaryanalysis-ng.md`
- `docs-site/package-lock.json`
- `docs/sessions/_next-session-prompt-phase4-pr15-plus-chore-pr9.md` (구 prompt)
- `docs/sessions/_next-session-prompt-manual-walkthrough.md` (deprecated 2026-05-10)

## 3. 진행 우선순위 (전체 상)

| 세션 | 묶음 | 작업 | PR | 추정 | 의존 |
|------|------|------|-----|------|------|
| 1 | **C** — 환경 안정화 | C1 postgres prune + C2 worker rebuild + C3 Vite proxy 결정 | 1 PR `chore/dev-stack-stabilization` | 1 세션 | 없음 |
| 2 | **A** — 시스템 버그 fix | A1 audit immutability + A2 restore 412 + A3 CSV BOM | 1 PR `fix/walkthrough-system-bugs` | 1 세션 | 없음 |
| 3 | **D** — Phase 5 fixme 해소 | D1 seed `--with-oauth-identity` + D2 admin_backup manual-trigger 활성화 | 1 PR `chore/manual-aligned-fixme-resolved` | 0.5 세션 | C2 머지 필수 |

**총합 ~2.5 세션.** 각 세션은 독립 PR. C → A → D 순서 권장 (D 가 C2 의존).

## 4. 핵심 결정 (사전 합의)

본 작업 진행 시 다음 6개 결정을 적용한다:

1. **A 묶음 → 단일 PR 묶음** — 5건 시스템 버그 중 fix PR 가능한 3건 (A1/A2/A3) 만 본 묶음에 포함. A4 (DT breaker reset endpoint) + A5 (last super_admin DB constraint) 는 별도 chore (각 0.5 세션, B 묶음과 일정 협의 후 결정).
2. **A2 매뉴얼 reverse-drift 회수** — PR #44 가 매뉴얼을 400 으로 일치시켰으므로, A2 코드 fix (400 → 412) 후 매뉴얼도 412 로 다시 정정. 동일 PR 안에서 처리 (코드 + 매뉴얼 EN/KO + RFC 7807 problem URI 신설).
3. **A1 trigger DDL 은 security-reviewer Producer-Reviewer 검증 필수** — CLAUDE.md §7 정합. db-designer 가 Alembic forward-only migration 작성 후, security-reviewer 가 trigger 회피 가능성 + UPDATE/DELETE blocking 의 운영적 영향 검토.
4. **C3 Vite proxy 결정 — 옵션 A (proxy 추가) 권장** — Phase 5 회귀 (`__authStore.accessToken` 노출 + harness fallback http://localhost:8000) 의 근본 원인 해소. SPA + 하네스 모두 `/v1/*` 단일 경로로 통일. 단, `VITE_API_BASE_URL` env 는 proxy bypass용으로 보존 (CI 또는 cross-host 시).
5. **D2 worker manual-trigger 시나리오 — celery task polling 추가** — 기존 fixme 는 단순히 "worker 의존" 이라 표시. C2 머지 후 worker 가 healthy 면 trigger → row 등장까지 polling (timeout 30s) 추가. waitForTimeout 금지 (test-writer.md 게이트).
6. **destructive 동작 격리** — A1 trigger migration 은 `audit_logs` 의 UPDATE/DELETE 차단이라, 본 PR merge 즉시 운영 환경에 적용. 테스트 환경 검증 + 롤백 시나리오 (`DROP TRIGGER` migration revision 필요 시 manual hotfix) 미리 준비.

---

## 세션 1 — 묶음 C: 환경 안정화

**브랜치**: `chore/dev-stack-stabilization`

**자율 실행 프로토콜 그대로 따른다** (`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜").

### 1.1 작업 범위 (3 sub-task)

#### C1 — postgres dev volume disk-full

- **현상**: `docker volume ls` 의 trustedoss postgres 볼륨이 100% (이미지 ~57GB). Phase 2/3 walkthrough 시 recovery loop 발생
- **작업**:
  - `scripts/dev-reset.sh` (NEW) — `docker-compose -f docker-compose.dev.yml down -v` + `docker volume prune -f --filter "label=com.docker.compose.project=trustedoss-portal"` + 재기동 + seed
  - `docker-compose.dev.yml` 의 postgres volume 에 `tmpfs` 또는 `size` 옵션 명시 검토 (overlay2 driver 한계 고려)
  - `docs-site/docs/installation/local-dev.md` (또는 contributor-guide) 에 dev-reset 사용법 1 섹션 추가 (EN+KO)

#### C2 — celery-worker stale image (`aiosmtplib` ModuleNotFoundError)

- **현상**: PR #39 (Chore P) 직후에도 `docker images` 의 worker tag 가 stale → restart loop 로 `aiosmtplib` 모듈 부재
- **원인 추정**:
  - `Dockerfile.worker` 가 `requirements-dev.txt` 만 install (transitive `-r requirements.txt` 동작은 정상)
  - build cache 가 stale 한 layer 재사용
- **작업**:
  - `apps/backend/Dockerfile.worker` line 86~88 — `pip install -r requirements.txt -r requirements-dev.txt` 명시 (transitive 의존 X)
  - `Makefile` (NEW 또는 확장) — `make dev-rebuild-worker` target = `docker-compose build celery-worker --no-cache && docker-compose up -d --force-recreate celery-worker`
  - `scripts/dev-reset.sh` 도 worker rebuild 옵션 (`--rebuild-worker`) 포함

#### C3 — Vite proxy 정합성 (옵션 A 권장)

- **결정**: SPA + 하네스 모두 `/v1/*` 단일 경로 사용. 옵션 A 채택
- **작업**:
  - `apps/frontend/vite.config.ts` 의 `server` 블록에 `proxy: { '/v1': 'http://backend:8000', '/auth': 'http://backend:8000', '/ws': { target: 'ws://backend:8000', ws: true } }` 추가
  - `apps/frontend/src/lib/{api,wsBase,apiBase}.ts` — `VITE_API_BASE_URL` 처리 보존 (CI / cross-host 시 fallback)
  - `apps/frontend/tests/_harness/{NotificationsHarness,ProfileHarness,AdminBackupHarness}.ts` 의 `backendBaseUrl()` 에서 `this.baseUrl` (Vite 5173) 우선 fallback 으로 다시 변경 가능 (proxy 통과). 단 `VITE_API_BASE_URL` 명시 시 (CI) 그대로 직행
  - 회귀 가드: 기존 39 + 27 시나리오 모두 PASS 확인

### 1.2 검증

```bash
# C1
bash scripts/dev-reset.sh                             # smoke run
docker volume ls | grep trustedoss                    # postgres 정리됨

# C2
make dev-rebuild-worker                               # 또는 직접 명령
docker-compose -f docker-compose.dev.yml exec celery-worker python -c "import aiosmtplib; print(aiosmtplib.__version__)"

# C3
cd apps/frontend && npm run dev &                     # Vite 5173
curl http://localhost:5173/v1/health                  # 200 (proxy 통과)
npm run test:e2e -- --grep "@auth"                    # 회귀 X
npm run test:e2e -- --grep "@manual-aligned"          # PR #45 27 시나리오 PASS

# 종합
docker-compose -f docker-compose.dev.yml ps           # 6/6 healthy
```

### 1.3 PR + 머지

```bash
git checkout -b chore/dev-stack-stabilization
# (직접 작업 + devops-engineer 에이전트 위임)
git add scripts/dev-reset.sh Makefile apps/backend/Dockerfile.worker apps/frontend/vite.config.ts \
        apps/frontend/tests/_harness/ docs-site/docs/contributor-guide/local-dev.md \
        docs-site/i18n/ko/docusaurus-plugin-content-docs/current/contributor-guide/local-dev.md
git commit -m "chore(dev-stack): postgres prune + worker rebuild + Vite proxy (C1+C2+C3)"
git push -u origin chore/dev-stack-stabilization
gh pr create --title "chore(dev-stack): post-walkthrough environment stabilization (C bundle)" --body "..."
```

### 1.4 세션 종료 시

`docs/sessions/2026-05-XX-stabilization-c-bundle.md` 핸드오프. backlog 의 env chore 후보 3건에 ~~취소선~~ + PR # + commit sha.

---

## 세션 2 — 묶음 A: 시스템 버그 fix

**브랜치**: `fix/walkthrough-system-bugs`

### 2.1 작업 범위 (3 sub-task)

#### A1 — `audit_logs` immutability constraint (sys-bug-audit-1, Low)

- **현재**: 매뉴얼이 약속한 append-only 가 DB-차원 미강제. PR #44 에서 매뉴얼 약속을 제거했으나, defense-in-depth 차원에서 trigger 도입
- **작업**:
  - `apps/backend/alembic/versions/<NEW>_audit_logs_immutable_trigger.py` — Alembic forward-only migration:
    ```sql
    CREATE OR REPLACE FUNCTION audit_logs_prevent_mutation()
    RETURNS TRIGGER AS $$
    BEGIN
      RAISE EXCEPTION 'audit_logs is append-only (TG_OP=%)', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER audit_logs_immutable_trigger
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_prevent_mutation();
    ```
  - 회귀 테스트: `apps/backend/tests/integration/test_audit_log_db_immutable.py` (NEW) — psql 직접 UPDATE/DELETE 시 IntegrityError 확인
  - 매뉴얼 reverse-drift: PR #44 에서 제거된 "DB CHECK constraint prevents..." 문구를 trigger 기반으로 복원 (`docs-site/docs/admin-guide/audit-log.md` §1, EN+KO)

#### A2 — restore 412 + RFC 7807 problem (sys-bug-bkp-1, Low)

- **현재**: missing `X-Confirm-Restore` → 400. RFC 7807 semantic 상 412 Precondition Failed
- **작업**:
  - `apps/backend/api/v1/admin_backups.py` 의 restore handler — header missing 경로:
    - status 400 → 412
    - RFC 7807 problem `urn:trustedoss:problem:restore_confirmation_required`
    - title `"Restore confirmation header missing"`
    - detail field 보존
  - `apps/backend/api/v1/problems.py` (또는 problem URI registry) 에 신규 URN 등재
  - 회귀 테스트: `apps/backend/tests/integration/test_admin_backup_restore.py` — 412 + content-type `application/problem+json` + `type` URN 정확
  - 매뉴얼 reverse-drift: PR #44 에서 400 으로 일치시킨 `docs-site/docs/admin-guide/backup-and-restore.md` §3 부분을 412 + 신규 URN 으로 다시 정정 (EN+KO). PR description 에 "PR #44 ↔ A2 양방향 회수" 명시

#### A3 — audit CSV UTF-8 BOM (sys-bug-audit-2, Low)

- **현재**: CSV export 가 BOM 없이 시작 → Excel 비-ASCII 깨짐
- **작업**:
  - `apps/backend/api/v1/admin_audit.py` 의 CSV stream 생성 부분 — 첫 chunk 에 `b'\xef\xbb\xbf'` prepend
  - `Content-Type: text/csv; charset=utf-8` 명시 (이미 있을 수 있음, 검증)
  - 회귀 테스트: `apps/backend/tests/unit/api/test_admin_audit_csv.py` 또는 integration — 응답 첫 3 byte `b'\xef\xbb\xbf'`
  - 매뉴얼 정정 불필요 (PR #44 에서 매뉴얼이 BOM 약속을 제거했으나, 이번에 BOM 추가 후 매뉴얼에 다시 약속 명시 — `docs-site/docs/admin-guide/audit-log.md` §4 EN+KO)

### 2.2 Producer-Reviewer 검증 (필수)

A1 의 trigger DDL 은 security-reviewer 에이전트 검토 필수 (§4 핵심 결정 #3):
- trigger 회피 가능성 (예: `ALTER TABLE` 으로 trigger drop 후 mutation)
- 운영적 영향 (정상 시 UPDATE/DELETE 호출 코드 경로 부재 확인)
- DB 마이그레이션 롤백 시나리오 (CLAUDE.md §6 forward-only 정책 정합)

### 2.3 검증

```bash
# A1
cd apps/backend && alembic upgrade head
pytest tests/integration/test_audit_log_db_immutable.py -v
docker-compose -f docker-compose.dev.yml exec postgres psql -U trustedoss -d trustedoss \
  -c "UPDATE audit_logs SET action='tampered' WHERE id=(SELECT id FROM audit_logs LIMIT 1);"
# ↑ ERROR: audit_logs is append-only (TG_OP=UPDATE)

# A2
pytest tests/integration/test_admin_backup_restore.py::test_missing_confirm_header_returns_412 -v
curl -X POST http://localhost:8000/v1/admin/backup/restore -F file=@dummy.tar.gz \
  -H "Authorization: Bearer <admin-token>"
# ↑ 412 + application/problem+json + urn:trustedoss:problem:restore_confirmation_required

# A3
pytest tests/unit/api/test_admin_audit_csv.py::test_csv_starts_with_utf8_bom -v
curl http://localhost:8000/v1/admin/audit/export.csv -H "Authorization: Bearer <admin-token>" \
  | head -c 3 | xxd
# ↑ 0000: efbb bf

# Docusaurus build (매뉴얼 reverse-drift)
cd docs-site && npm run build && npm run build -- --locale ko
```

### 2.4 PR + 머지

```bash
git checkout -b fix/walkthrough-system-bugs
# (backend-developer + db-designer + security-reviewer 협업)
git add apps/backend/alembic/versions/ apps/backend/api/ apps/backend/tests/ \
        docs-site/docs/admin-guide/{audit-log,backup-and-restore}.md \
        docs-site/i18n/ko/docusaurus-plugin-content-docs/current/admin-guide/{audit-log,backup-and-restore}.md
git commit -m "fix: walkthrough system bugs A1+A2+A3 + manual reverse-drift"
git push -u origin fix/walkthrough-system-bugs
gh pr create --title "fix: audit immutability + restore 412 + CSV BOM (A bundle)" --body "..."
```

### 2.5 세션 종료 시

`docs/sessions/2026-05-XX-stabilization-a-bundle.md` 핸드오프. backlog 의 system bug list 5건 중 3건 ~~취소선~~ + PR # + commit sha. A4/A5 별도 chore 등재 유지.

---

## 세션 3 — 묶음 D: Phase 5 fixme 해소

**브랜치**: `chore/manual-aligned-fixme-resolved`

**선결 조건**: 세션 1 (C 묶음, 특히 C2 worker rebuild) 머지 완료. D2 가 worker healthy 의존.

### 3.1 작업 범위 (2 sub-task)

#### D1 — `seed_e2e_user --with-oauth-identity` 옵션

- **블록 해제**: `auth_and_profile.spec.ts` 의 last-only OAuth Unlink + Unlink-with-fallback 2 fixme
- **작업**:
  - `apps/backend/scripts/seed_e2e_user.py` — argparse `--with-oauth-identity {github,google}` 추가:
    - DB 에 `OAuthIdentity` row 1개 사전 삽입 (`provider_user_id` = test fixture, `provider_user_id_hash` = HMAC 적용)
    - `--with-oauth-identity none` (기본) 시 기존 동작 유지
  - 회귀 가드: `apps/backend/tests/unit/scripts/test_seed_e2e_user.py` (있으면) 또는 integration smoke
  - `apps/frontend/tests/_harness/seed.ts` 의 `seedE2eUser(...)` 옵션에 `withOAuthIdentity?: 'github' | 'google'` 전달 path
  - `apps/frontend/tests/e2e/auth_and_profile.spec.ts` 의 2 fixme 제거:
    - `S2-3) Unlink last-only OAuth blocked with urn:trustedoss:problem:oauth_unlink_blocks_login`
    - `S2-4) Unlink with password fallback succeeds`

#### D2 — admin_backup manual-trigger 시나리오 활성화

- **블록 해제**: `admin_backup.spec.ts` 의 manual trigger row check 2 fixme
- **선결**: 세션 1 의 C2 머지 (worker healthy)
- **작업**:
  - `apps/frontend/tests/e2e/admin_backup.spec.ts` — `test.fixme(...)` 제거:
    - `S1) manual trigger creates new row with manual-* prefix`
    - `S2) manual-triggered backup is downloadable`
  - Celery task 완료 polling 헬퍼 (`AdminBackupHarness.waitForBackupRow(name, timeout)`):
    - `expect.poll(() => listRows().filter(name), { timeout: 30_000 }).toHaveLength(1)`
    - **waitForTimeout 금지** (test-writer.md 게이트)
  - 시나리오 cleanup: 본 spec 만 사용한 backup 행 삭제 (다른 시나리오 격리)

### 3.2 검증

```bash
# D1
docker-compose -f docker-compose.dev.yml exec backend \
  python scripts/seed_e2e_user.py --with-oauth-identity github
docker-compose -f docker-compose.dev.yml exec backend \
  python -c "
from sqlalchemy import select
from core.db import get_session
from models.oauth_identity import OAuthIdentity
async def f():
    async with get_session() as s:
        rows = (await s.execute(select(OAuthIdentity))).scalars().all()
        assert len(rows) == 1, f'expected 1, got {len(rows)}'
import asyncio; asyncio.run(f())
"

# D2 (선결 C2 머지 후)
docker-compose -f docker-compose.dev.yml ps celery-worker  # healthy 확인
cd apps/frontend && npm run test:e2e -- --grep "@manual-aligned admin backup"
# ↑ 12 시나리오 모두 PASS (이전 fixme 2건 활성화 + 정상)

# 회귀 가드
npm run test:e2e -- --grep "@manual-aligned"           # 27 + 2 (D2 fixme 해소) + 2 (D1 fixme 해소) = 31 PASS
npm run test:e2e                                       # 전체 39 + 31 = 70 PASS
```

### 3.3 PR + 머지

```bash
git checkout -b chore/manual-aligned-fixme-resolved
# (backend-developer + test-writer 협업)
git add apps/backend/scripts/seed_e2e_user.py apps/frontend/tests/_harness/ \
        apps/frontend/tests/e2e/{auth_and_profile,admin_backup}.spec.ts
git commit -m "chore(e2e): resolve Phase 5 fixme — OAuth seed + worker manual-trigger"
git push -u origin chore/manual-aligned-fixme-resolved
gh pr create --title "chore(e2e): resolve Phase 5 fixme (D bundle)" --body "..."
```

### 3.4 세션 종료 시

`docs/sessions/2026-05-XX-stabilization-d-bundle.md` 핸드오프. backlog Manual Walkthrough 섹션의 fixme 4건 ~~취소선~~ + PR # + commit sha.

---

## 5. 자율 실행 프로토콜 (모든 세션 공통)

`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜" 그대로:

```
LOOP per session:
  1. 시작 시 검증 (§2)
  2. 새 브랜치 생성 + push (-u origin)
  3. 에이전트 위임 또는 직접 구현
  4. 로컬 검증 (lint + typecheck + test + i18n:check + Docusaurus build 해당 시)
  5. PR 생성 + CI 모니터링 (background polling, 사용자 task-notification 으로 깨움)
  6. CI 실패 시 fix + push (최대 3회 retry → 초과 시 BLOCKED 표시 + 다음 세션 이월)
  7. 머지 후 chore-backlog.md 에 ~~취소선~~ + PR # + commit sha
  8. 세션 종료 핸드오프 노트 작성 → 본 prompt 의 "현재 상태" 섹션 갱신
```

### 에이전트 권장 매핑

| 세션 | 묶음 | 주 에이전트 | 보조 |
|------|------|-----------|------|
| 1 | C | devops-engineer | doc-writer (contributor-guide) |
| 2 | A | backend-developer + db-designer | security-reviewer (A1 trigger), doc-writer (매뉴얼 reverse-drift) |
| 3 | D | backend-developer | test-writer (harness verb 추가) |

### CI 게이트 동작 (PR #46 효과)

본 prompt 의 모든 PR 은 자동으로 다음 잡 통과 필요:
- `e2e (scan-flow)` — 39 시나리오 (`--grep-invert @manual-aligned`)
- `e2e (manual-aligned)` — 27 시나리오 (`--grep @manual-aligned`)
- D 묶음 머지 후: manual-aligned 가 31 시나리오로 증가

PR 분리 원칙: 한 PR = 한 묶음 (C/A/D). 시스템 버그 fix 와 매뉴얼 reverse-drift 는 같은 PR (A 묶음에 포함) — A2 의 RFC 7807 변경이 매뉴얼 약속과 직접 결부되기 때문.

## 6. 세션 종료 체크리스트 (매 세션 끝 반드시)

- [ ] 작업한 chore / fix 가 머지됐는지 (`gh pr view <num> --json mergedAt`)
- [ ] main 으로 checkout + pull 완료
- [ ] `docs/chore-backlog.md` 의 해당 항목에 ~~취소선~~ + PR # + commit sha
- [ ] `docs/sessions/2026-05-XX-stabilization-<bundle>-bundle.md` 핸드오프 노트 작성
- [ ] 본 파일의 "현재 상태" 섹션 — main HEAD + 최근 commits + 처리율 갱신
- [ ] BLOCKED 항목 발생 시 본 파일 + chore-backlog 양쪽에 BLOCKED 표시
- [ ] 다음 세션이 의존하는 항목 (예: 세션 1 의 C2 → 세션 3 의 D2) 이 BLOCKED 면 다음 세션 prompt 에 명시

## 7. 세션 끊김 시 복구 절차

새 세션 시작:
1. 본 파일을 첫 메시지로 그대로 붙여넣음
2. **§2 시작 시 검증** 실행 → main HEAD 확인
3. `cat docs/chore-backlog.md | grep -E "✅|~~Chore [CAD]"` 로 처리 완료 항목 파악
4. §3 우선순위 표에서 **첫 번째 미처리 묶음** 선택
5. 해당 세션 prompt 그대로 실행

만약 직전 세션이 **PR CI 대기 중에 끊겼다면**:
1. `gh pr list --state open --author "@me"` 로 미머지 PR 확인
2. `gh pr checks <num>` 로 잡 상태 확인
3. green 이면 머지, 실패면 logs 분석 → fix → push retry
4. retry 3회 초과 시 BLOCKED 표시 + 다음 세션으로 이월

만약 **로컬 working tree 가 dirty 상태로 끊겼다면**:
1. `git status --short` 로 변경 사항 확인
2. 의도한 변경이면 commit + push, 아니면 `git stash` 또는 `git restore` 로 정리
3. 본 prompt §1 의 단일 진실 파일들 다시 정독 후 재개

만약 **세션 1 (C 묶음) 만 머지 후 세션 2~3 시작 전 끊겼다면**:
- 세션 2 (A) 와 세션 3 (D) 는 독립적 — 어느 것부터 시작해도 무방
- 단, A2 의 매뉴얼 reverse-drift 가 D 묶음 spec 의 fixme 와 무관함을 확인

## 8. 참조 문서

- `CLAUDE.md` — 핵심 규칙 + 품질·보안 표준
- `docs/v2-execution-plan.md` — Phase 별 상세
- `docs/autonomous-execution-plan.md` — 자율 실행 프로토콜
- `docs/chore-backlog.md` — **본 세션의 단일 진실** (Manual Walkthrough Verification 섹션 + system bug list)
- `docs/sessions/2026-05-10-manual-walkthrough-complete.md` — 직전 6 세션 통합 핸드오프
- `docs/sessions/2026-05-09-user-manual-walkthrough.md` — A 묶음 시스템 버그 BUG-USR-* 원본
- `docs/sessions/2026-05-10-admin-manual-walkthrough.md` — A 묶음 시스템 버그 sys-bug-* 원본
- `docs/sessions/2026-05-09-manual-coverage-matrix.md` — Phase 1 산출물 (Roadmap 우선순위 결정 시 참고)
- `MEMORY.md` — 장기 기억

## 9. 주의사항

- **A2 매뉴얼 reverse-drift 두 방향 회수**: PR #44 가 매뉴얼을 400 으로 일치시켰던 것을 본 PR 에서 412 + URN 신설로 다시 정정. PR description 에 양방향 회수 명시 — 후속 walkthrough 가 혼동하지 않도록
- **A1 trigger DDL 운영 영향**: merge 즉시 운영 환경에 적용. 정상 코드 경로에 UPDATE/DELETE 호출 부재 확인 (security-reviewer 검증 사전 필수)
- **C2 worker rebuild 전파**: 본 PR merge 후 운영자가 `make dev-rebuild-worker` 또는 `docker-compose pull && docker-compose up -d --force-recreate` 실행 필요. PR description 에 명시
- **C3 Vite proxy 결정 — 리스크**: SPA + 하네스 + CI 동작 모두 검증. proxy 추가 후 dev/CI 환경 격리가 깨지면 `VITE_API_BASE_URL` 명시 fallback 보존
- **D2 의 worker 의존**: C2 머지 후 Phase 5 fixme 해소 가능. 순서 위반 시 D 묶음 PR 의 e2e 잡 fail
- **에이전트 한도** — 한 prompt 당 한 에이전트는 약 80~150 도구 호출. A 묶음 3 sub-task + 매뉴얼 reverse-drift + Producer-Reviewer 검증 시 timeout 위험 → sub-task 별 분할 위임 권고
- **EN / KO 동시 정정** — A1, A2, A3 모두 매뉴얼 변경 동반. 한 쪽만 고치지 말 것 (CLAUDE.md §8)
- **Memory `feedback_push_pr_authorized`** — push / `gh pr create` 자동 허용. force-push (`--force`/`--force-with-lease`) 는 사용자 명시 승인 필요 (Phase 6 PR #46 회귀 근거)
- **Memory `feedback_adversarial_input_parametrize`** — A1, A2, A3 모두 적대적 input 단위 테스트 추가 (audit row id control bytes / restore upload header CRLF / CSV row 의 NUL bytes)
- **Memory `feedback_optimistic_concurrency_pattern`** — A1 trigger 가 동시 INSERT 와 충돌하지 않는지 확인 (BEFORE UPDATE OR DELETE 만 차단, INSERT 는 통과)
- **세션 내 1 PR 원칙** — C/A/D 각 묶음별 단일 PR. sub-task 분할 시 PR 당 도메인 일관성 우선 (A 묶음의 audit + backup + audit 가 audit-log/backup-restore 두 도메인이지만 "시스템 버그 fix" 라는 공통 분류로 묶음)

## 10. 후속 (B 묶음 — v2.1 sprint 협의)

본 prompt 의 세션 1~3 모두 처리 후, B 묶음 (Roadmap (v2.x) 미구현 약속 실제 구현) 은 별도 sprint planning. 우선순위 후보:

- **B1 API Key 확장** (가장 자주 요청 가능, 1~1.5 세션) — `expires_at` + 만료 프리셋 + brute-force alert
- **B2 Excel/PDF Reports** (1.5~2 세션) — `openpyxl` + `weasyprint` 도입
- **B3 `/profile` Password identity row** (0.5 세션) — 비밀번호 변경 UI
- **B4 Project permanent Delete** (0.5 세션) — 정책 결정 선행 (audit / referential integrity)
- **B5 Scan cancel API** (0.5 세션) — 30+ 분 scan 운영자 취소
- **B6 알림 채널 × trigger matrix** (1 세션) — fine-grained ON/OFF

별도 prompt 파일 (`_next-session-prompt-v2-1-roadmap.md`) 작성 권고.

본 작업 예상 시간: 세션당 0.5~1 시간, 3 세션 전체 약 2~3 시간.
