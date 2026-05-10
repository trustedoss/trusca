# Post-Walkthrough Stabilization (C+A+D bundle) — handoff

> 시작 prompt: `docs/sessions/_next-session-prompt-post-walkthrough-stabilization.md` (deprecated 2026-05-10).
> 단일 자율 세션으로 C 묶음 (환경 안정화) → A 묶음 (시스템 버그 fix) → D 묶음 (Phase 5 fixme 해소) → 부산물 lockfile commit 까지 처리 (2026-05-10 ~04:00 ~ ~06:10 UTC, 약 2 시간 wall-clock).
> 직전 핸드오프: `docs/sessions/2026-05-10-manual-walkthrough-complete.md`.

## 한눈에

| 묶음 | PR | 머지 commit | 산출물 |
|------|-----|-------------|--------|
| C — 환경 안정화 | [#47](https://github.com/trustedoss/trustedoss-portal/pull/47) | `c8c44a0` | `scripts/dev-reset.sh` (NEW), `Makefile` (NEW), `Dockerfile.worker` (explicit pip), `vite.config.ts` (`/v1` `/auth` `/ws` proxy), 3 harness `backendBaseUrl()` 갱신, `getting-started.md` EN+KO |
| A — 시스템 버그 fix | [#48](https://github.com/trustedoss/trustedoss-portal/pull/48) | `defc7e2` | Alembic 0012 `audit_logs` immutability trigger (BEFORE UPDATE OR DELETE row + BEFORE TRUNCATE statement), restore 412 + URN, audit CSV UTF-8 BOM, 14건 integration test, 매뉴얼 reverse-drift EN+KO |
| D — Phase 5 fixme 해소 | [#49](https://github.com/trustedoss/trustedoss-portal/pull/49) | `b68b0a5` | `seed_e2e_user --with-oauth-identity`, `seedE2eUser({ withOAuthIdentity })`, `AdminBackupHarness.waitForManualBackupRow`, `auth_and_profile` test 4 활성화 |
| 부산물 — docs lockfile | [#50](https://github.com/trustedoss/trustedoss-portal/pull/50) | `4401a8a` | `docs-site/package-lock.json` 1 파일 commit. Deploy Docs CI 가 2026-05-09 부터 cache step 에서 실패하던 것을 해소 |

총 4 PR. C/A/D 는 prompt §3 권장 순서대로, lockfile 은 #48 머지 직후 GitHub Pages 자동 배포 잡이 또 실패하는 것을 보고 추가 확장.

## 핵심 정량

- **CI 통과까지 라운드트립**: C 2회 / A 4회 / D 2회 / lockfile 1회 = 9 푸시 / 4 PR (사전 합의된 자율 retry 한도 3회 안에서 모두 수렴)
- **신규 코드 + 테스트**: backend +602/-26 (PR #48), frontend +413/-28 (PR #47), e2e harness +143/-21 (PR #49)
- **신규 문서**: `dev-stack` 섹션 (EN+KO), `audit-log` schema 재기술 (EN+KO), `backup-and-restore` 412 회수 (EN+KO)
- **Producer-Reviewer**: A1 trigger DDL → security-reviewer 1회 (PASS WITH MINOR CHANGES). M1 (TRUNCATE bypass), L2 (parametrize 확장 4→8 컬럼), L3 (runbook 검증 단계) 본 PR 안에서 모두 반영. L1 (PostgreSQL role 분리) 는 Phase 7/8 hardening 으로 docstring + admin manual 에 명시.

## CI 라운드트립에서 발견된 추가 버그

본 작업의 가장 큰 부분. 1차 푸시 후 발견되어 fix 푸시로 수렴.

### PR #47 (C) — 1차 회귀
- **semgrep self-trip**: `.semgrepignore` 의 새 코멘트 블록에 `ws://backend:8000` 리터럴이 있어서 `detect-insecure-websocket` 룰이 ignore 파일 자체를 매치 → 같은 룰의 ERROR finding 으로 carry. 코멘트에서 리터럴 제거 (스킴을 산문으로만 기술).

### PR #48 (A) — 3차 회귀
- **Round 1 — trigger over-blocking**: security-reviewer I1 finding 이 빗나감. `audit_logs.actor_user_id` / `team_id` 는 `users.id` / `teams.id` 에 ON DELETE SET NULL 로 묶여 있어 User/Team 삭제 시 cascade UPDATE 가 발생. 무차별 차단 trigger 가 `test_admin_team_service` / `test_admin_team_delete_concurrency` / `test_delete_team_archives_projects_then_succeeds` 를 500 으로 변환. plpgsql 함수 재작성: content column (id/created_at/action/target_table/target_id/request_id/ip/user_agent/diff) 9개는 strict immutability, FK 컬럼은 `NEW IS NOT NULL` gate 로 SET NULL cascade 만 통과하고 두 non-NULL id 간 rotation (framing 시도) 은 거부.
- **Round 2 — BACKEND_ROOT 경로 버그**: 신규 `test_audit_log_db_immutable.py` 의 `Path(__file__).resolve().parent.parent` 는 `tests/` (alembic.ini 부재) 로 해석되어 `_migrate_once` 가 silent skip → 18 신규 테스트 모두 skip + coverage 의 source 인식 갱신이 흐트러져 86% → 34% 급락. `parent.parent.parent` (`apps/backend/`) 로 정정.
- **Round 3 — asyncpg binding**: BACKEND_ROOT fix 로 테스트가 실제 실행되자 두 건의 functional 버그 노출: (a) `created_at` parametrize 에 ISO 문자열 → asyncpg TIMESTAMPTZ codec 이 datetime 인스턴스 요구. `datetime(2020,1,1,tzinfo=UTC)` 로 변경. (b) `:diff::jsonb` → asyncpg parameter binder 가 `::` 를 named-param 구분자로 해석. `CAST(:diff AS jsonb)` 로 변경.

### PR #49 (D) — 1차 회귀
- **backup.sh worker dependency**: `scripts/backup.sh` 가 worker 컨테이너 내부에서 `docker-compose` (V1) 바이너리를 요구하는데 worker 이미지에 없음. C 묶음 worker rebuild 로 해결되지 않는 별도 이슈. test 4 fixme 를 정정된 사유 (operator-side 3 가지 해결 경로) 와 함께 복원.

## A1 trigger 재설계 (security-reviewer 검증 + CI 회귀 이후)

최종 plpgsql:
```sql
CREATE OR REPLACE FUNCTION audit_logs_prevent_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=%)', TG_OP
      USING ERRCODE = '23000';
  END IF;
  -- TG_OP = 'UPDATE'.
  IF (OLD.id, OLD.created_at, OLD.action, OLD.target_table, OLD.target_id,
      OLD.request_id, OLD.ip, OLD.user_agent, OLD.diff)
     IS DISTINCT FROM
     (NEW.id, NEW.created_at, NEW.action, NEW.target_table, NEW.target_id,
      NEW.request_id, NEW.ip, NEW.user_agent, NEW.diff)
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on content column)'
      USING ERRCODE = '23000';
  END IF;
  IF NEW.actor_user_id IS NOT NULL
     AND OLD.actor_user_id IS DISTINCT FROM NEW.actor_user_id
  THEN
    RAISE EXCEPTION '... (TG_OP=UPDATE on actor_user_id pin)' USING ERRCODE = '23000';
  END IF;
  IF NEW.team_id IS NOT NULL
     AND OLD.team_id IS DISTINCT FROM NEW.team_id
  THEN
    RAISE EXCEPTION '... (TG_OP=UPDATE on team_id pin)' USING ERRCODE = '23000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

+ 별도 `BEFORE TRUNCATE FOR EACH STATEMENT` trigger 로 row-trigger 가 잡지 못하는 single-statement table-wipe 차단.

## 다음 세션 (선택)

**선택지 A — A4/A5 chore 합본** (~1 세션):
- `chore/dt-breaker-reset-endpoint` — operator escape hatch
- `chore/last-super-admin-db-constraint` — defense-in-depth (남은 system-bug 2건)

**선택지 B — L1 PostgreSQL role 분리** (~1.5 세션):
- `trustedoss_app` (DML-only on `audit_logs`) / `trustedoss_owner` (migrations) 분리
- compose / install.sh / Dockerfile entrypoint 갱신 + Alembic GRANT/REVOKE 마이그레이션
- 결과: PR #48 의 trigger 가 runtime app 에서 진짜로 unbypassable

**선택지 C — D2 backup.sh 리팩터링** (~1.5 세션):
- `tasks.backup` 을 `DATABASE_URL` 직접 사용으로 재작성 (worker shell-back 제거)
- 또는 worker 이미지에 docker-compose 번들 (heavy + recursive Docker dep, 권장 X)
- 결과: admin_backup test 4 fixme 해소

**선택지 D — D1 잔여 fixme** (~0.5~1 세션):
- `seed_e2e_user --no-password` 또는 JWT-mint 헬퍼
- `auth_and_profile.spec.ts` test 3 (last-only OAuth blocks-login alert) 활성화

**선택지 E — v2.1 sprint planning (B 묶음)**:
- 본 prompt §10 의 6개 후보 (B1 API Key 확장 / B2 Excel·PDF Reports / B3 Profile password row / B4 Project permanent Delete / B5 Scan cancel API / B6 알림 채널×trigger matrix). 별도 sprint planning 단계 거쳐 prompt 작성 권고.

권장 순서: **A → B → D → C → E**. A 가 가장 작고 (남은 system bug 정리), B 는 보안 의미 가장 큼, D 는 단발 fixme 정리, C 는 worker shell-back 제거 (큰 리팩터링), E 는 별도 sprint.

## 참조

- `CLAUDE.md` — 핵심 규칙 + 품질·보안 표준 (변경 X)
- `docs/v2-execution-plan.md` — Phase 별 상세 (변경 X)
- `docs/autonomous-execution-plan.md` — 자율 실행 프로토콜 (변경 X)
- `docs/chore-backlog.md` — Manual Walkthrough Verification 섹션 system bug + env chore + Phase 5 fixme 갱신 (본 PR 동반 변경)
- `docs/sessions/2026-05-10-manual-walkthrough-complete.md` — 직전 6-Phase 통합 핸드오프

## Memory 업데이트 권고

세션 종료 후 다음 memory 추가/갱신 권고:

- `feedback_audit_logs_fk_cascade_set_null` (NEW) — `audit_logs.actor_user_id` / `team_id` 는 `users.id` / `teams.id` 에 `ON DELETE SET NULL` 로 묶여 있어 User/Team 삭제 시 cascade UPDATE 가 발생. defense-in-depth trigger 도입 시 content column immutability 와 FK column NULL transition 을 분리 처리 필수. PR #48 회귀 근거.
- `feedback_asyncpg_double_colon_param` (NEW) — asyncpg `text()` parameter binder 가 `::` 를 named-param 구분자로 해석. `:value::jsonb` 는 `PostgresSyntaxError`. `CAST(:value AS jsonb)` 사용. PR #48 round 3 근거.
- `feedback_semgrep_self_match` (NEW) — `.semgrepignore` 의 코멘트도 `--config=auto` 로 스캔됨. 보안 룰의 리터럴 (예: `ws://`, `eval`, `password=`) 을 코멘트에 포함하면 self-trip. 산문으로만 기술. PR #47 회귀 근거.
- `feedback_security_reviewer_blind_spot` (NEW 또는 UPDATE) — security-reviewer 의 "I1 — no legitimate UPDATE/DELETE on audit_logs exists" 판정은 ORM 레벨만 검토. Postgres FK cascade 는 별도 검증 필수. Producer-Reviewer 패턴이라도 DB 레벨 cascade 는 lint 단계 (Alembic test) 가 잡지 못하면 첫 CI run 까지 노출 X.
