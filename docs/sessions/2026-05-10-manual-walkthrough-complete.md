# Manual Walkthrough Verification — 6-phase complete summary

> 시작 prompt: `docs/sessions/_next-session-prompt-manual-walkthrough.md` (deprecated 2026-05-10)
> 단일 자율 세션으로 Phase 1 ~ 6 모두 처리 (2026-05-09 14:30 UTC ~ 2026-05-10 01:30 UTC, 약 11 시간 wall-clock).
> 직전 핸드오프: `docs/sessions/2026-05-09-post-ga-cleanup-complete.md` (post-GA 6-chore 통합).

## 한눈에

| Phase | PR | 머지 commit | 산출물 |
|-------|-----|-------------|--------|
| 1 — Coverage matrix | [#40](https://github.com/trustedoss/trustedoss-portal/pull/40) | `42a2eeb` | `docs/sessions/2026-05-09-manual-coverage-matrix.md` (370 단계 / 67 KB) |
| 2 — User walkthrough | [#41](https://github.com/trustedoss/trustedoss-portal/pull/41) | `b4c0224` | `docs/sessions/2026-05-09-user-manual-walkthrough.md` (74/207 검증) |
| 3 — Admin walkthrough | [#42](https://github.com/trustedoss/trustedoss-portal/pull/42) | `f16f819` | `docs/sessions/2026-05-10-admin-manual-walkthrough.md` (66/163 검증) |
| 4a — User drift fixes | [#43](https://github.com/trustedoss/trustedoss-portal/pull/43) | `aadee42` | docs-site EN+KO 9 페이지 × 2 = 18 파일 (+402/−384) |
| 4b — Admin drift fixes | [#44](https://github.com/trustedoss/trustedoss-portal/pull/44) | `ad9436e` | docs-site EN+KO 6 페이지 × 2 = 12 파일 (+367/−279) |
| 5 — Manual-aligned E2E | [#45](https://github.com/trustedoss/trustedoss-portal/pull/45) | TBD | 신규 하네스 3 + spec 3 = 27 시나리오 (`@manual-aligned`) |
| 6 — CI matrix gate | [#46](https://github.com/trustedoss/trustedoss-portal/pull/46) | TBD | `.github/workflows/ci.yml` matrix `[scan-flow, manual-aligned]` |

총 7 PR (Phase 4가 영역별 2 분리). 매뉴얼 fix와 시스템 버그 fix는 절대 같은 PR에 묶지 않음 (prompt §4 정합).

## 핵심 정량

- **분석한 매뉴얼**: 사용자 9 페이지 + 관리자 6 페이지 = 15 페이지 / 2,356 라인
- **분해한 단계**: 370 (사용자 207 + 관리자 163)
- **검증한 단계**: 140 (37.8%) — 우선순위 P0 ⚠ + P1 자동화 가드 부재 + P2 샘플링
- **분류 결과**: ✅ 54 (38.6%) / 📝 57 (40.7%) / 🐛 6 (4.3%) / ⏭ 23 (16.4%)
- **매뉴얼 정정**: 59 drift × EN+KO 미러 = 118 변경 (Docusaurus EN+KO build 양쪽 SUCCESS)
- **신규 E2E**: 27 시나리오 (adversarial parametrize 포함) + 3 신규 하네스 + 1 확장
- **CI matrix**: scan-flow (39 시나리오) + manual-aligned (27 시나리오) 병렬 실행

## Headline finding (전체 작업 결정적 배경)

**v2.0.0 매뉴얼은 코드보다 큰 폭 앞선 비전을 기술하고 있었다.** 사용자 매뉴얼 31 drift / 관리자 매뉴얼 26 drift 는 단순 라벨 차이가 아니라:

- **5 페이지에 걸친 curl `/api/v1/...` 예제** — 실제 prefix `/v1/...` (Phase 4 PR #43 일괄 정정)
- **Excel/PDF Reports 메뉴 + 3 endpoint** — 매뉴얼 약속만, 코드 자체 미존재 (Roadmap 으로 이전)
- **WebSocket path + message shape** — 실제 코드와 완전히 다름. 클라이언트 작성자가 매뉴얼만 보면 작동 X
- **`disk-and-health` 페이지** — 거의 전체 drift (8→7 component, healthy→ok, env 명, threshold, 2 gauge → 4 카드)
- **`api-keys` scope 모델** — 매뉴얼이 effective_role + allowed_actions taxonomy 약속, 실제는 단순 org/team/project resource scope
- **`users-and-teams`** — invite-user / delete-user / team archive / pending-status 모두 약속만, 코드 없음

이 drift 들은 v2.0.0 GA를 받은 사용자가 **매뉴얼만 따라하면 절반 이상의 기능에서 막힌다**는 의미다. Phase 4 정정 PR 2개로 기존 매뉴얼은 코드와 일치하게 됐고, 미구현 약속은 페이지별 `## Roadmap (v2.x)` 섹션으로 이전됐다.

## 시스템 버그 (사용자 정책상 fix PR 보류 — 모두 Low/Medium)

**User persona** (PR #41 발견, 2건):
- BUG-USR-001 (Medium) — Project 영구 Delete 부재 (DELETE = soft archive). 의도 불명 → 매뉴얼 정정으로 처리됨
- BUG-USR-002 (Low) — Scan cancel API 부재. 매뉴얼 정정으로 처리됨

**Admin persona** (PR #42 발견, 4건 + 1건):
- sys-bug-u&t-1 (Low) — last super_admin DB-level CHECK constraint 부재. 매뉴얼 정정 (Roadmap)
- sys-bug-dt-1 (Low) — DT breaker reset endpoint 부재. 매뉴얼 정정 (Roadmap)
- sys-bug-audit-1 (Low) — `audit_logs` immutability constraint 부재. 매뉴얼 정정 (Roadmap)
- sys-bug-bkp-1 (Low) — restore missing X-Confirm-Restore → 400 (RFC 7807 상 412 가 정확). 매뉴얼 400 으로 일치
- sys-bug-audit-2 (Low) — CSV export UTF-8 BOM 부재. 매뉴얼 정정 (Roadmap)

**추후 fix PR 후보** (별도 chore 등재 권고 — `docs/chore-backlog.md` 참조):
- `fix/audit-immutability` — DB-level trigger 추가 (Alembic forward-only)
- `fix/backup-restore-confirm-412` — 400 → 412 status + RFC 7807 problem
- `fix/audit-csv-utf8-bom` — Excel-friendly BOM
- `chore/dt-breaker-reset-endpoint` — operator escape hatch
- `chore/last-super-admin-db-constraint` — defense-in-depth

## 환경 chore 후보 (Walkthrough 부산물)

본 작업 진행 중 발견된 dev/CI 환경 issues:

1. **postgres dev volume disk-full** — Phase 2/3 Tier 2 동적 검증을 차단. 별도 chore: `docker volume prune` + dev volume size cap 권고
2. **celery-worker stale image** — `aiosmtplib` ModuleNotFoundError 로 restart loop. PR #39 (Chore P) 직후에도 stale. 별도 chore: dev-stack 재빌드 + Dockerfile.worker `pip install -r requirements-dev.txt` step verification
3. **Vite no `/v1/*` proxy** — Phase 5 신규 하네스가 backend URL 로 `localhost:5173` (Vite) fallback 했다가 404. 본 PR (b5ca996) 에서 해결. 추후 Vite proxy 추가 또는 `BACKEND_BASE_URL` 명시 강제 결정 필요

## 자율 실행 프로토콜 적용 결과

`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜" 그대로 적용:

- **에이전트 사용**: doc-writer × 3 (matrix / user-drift / admin-drift), frontend-dev × 2 (user/admin walkthrough), test-writer × 1 (Phase 5 E2E). 직접 작업 = Phase 6 (CI workflow YAML 27 라인 변경).
- **에이전트 timeout 1건**: admin-drift agent (5 분 28 초) — 12 파일 중 11 파일 완료, KO `api-keys.md` 1 파일 미완. 직접 마무리 + Docusaurus build 검증.
- **CI 1차 실패 1건**: PR #45 첫 푸시 — manual-aligned 4 시나리오 fail (3 × 404 from Vite no-proxy + 1 × Switch data-state vs aria-checked). harness 3 파일 fix → 두 번째 푸시 통과.
- **destructive flow**: dev compose 만 사용. backup snapshot 안전망은 disk-full 환경상 생략 (Tier 1 정적 검증 위주).
- **screenshots**: 자동 캡처 deferred (별도 chore 후속 처리 권고 — 매뉴얼 placeholder PNG 교체).

## Phase 6 CI 매트릭스 동작 검증

PR #46 (이번 PR) 머지 후 main 의 모든 PR 은:
- `e2e (scan-flow)` 잡 — 9 spec / 39 시나리오 (`--grep-invert @manual-aligned`)
- `e2e (manual-aligned)` 잡 — 3 spec / 27 시나리오 (`--grep @manual-aligned`)

두 잡 병렬 실행으로 wall-time ~6 분 cap 유지. 잡 fail 격리 → manual-aligned 회귀가 core 회귀 보고를 가리지 않음.

## 다음 세션 (선택)

**선택지 A — 시스템 버그 fix PR 묶음**:
- `fix/audit-immutability` + `fix/backup-restore-confirm-412` + `fix/audit-csv-utf8-bom` 한 번에
- 예상 0.5~1 세션. 모두 Low severity 라 우선순위 낮음

**선택지 B — Roadmap (v2.x) 미구현 약속 실제 구현 시작**:
- API Key expiry / scope 확장 (가장 자주 요청될 가능성)
- Excel/PDF Reports endpoint
- `/profile` 의 Password identity row UI
- 예상 다음 sprint 단위 작업 (Phase 8 또는 v2.1)

**선택지 C — 환경 chore (dev stack 안정화)**:
- postgres dev volume cap + prune script
- celery-worker 이미지 재빌드 자동화 (Makefile 또는 docker-compose hook)
- Vite proxy 정합성 결정 (proxy 추가 vs `VITE_API_BASE_URL` 강제)

**선택지 D — Manual-aligned 추가 시나리오 (Phase 5 fixme 4건 해소)**:
- `seed_e2e_user --with-oauth-identity` 플래그 (auth_and_profile 2 fixme)
- celery worker 활성화 후 admin_backup manual-trigger 시나리오 활성화 (2 fixme)

권장 순서: **C → A → D → B**. 환경 안정화가 모든 후속 작업의 가속기.

## 참조

- `CLAUDE.md` — 핵심 규칙 + 품질·보안 표준 (변경 X)
- `docs/v2-execution-plan.md` — Phase 별 상세 (변경 X)
- `docs/autonomous-execution-plan.md` — 자율 실행 프로토콜 (변경 X)
- `docs/chore-backlog.md` — Manual Walkthrough Phase 1~6 등재 + system bug list 추가
- `docs/sessions/2026-05-09-manual-coverage-matrix.md` — Phase 1 산출물 (단일 진실)
- `docs/sessions/2026-05-09-user-manual-walkthrough.md` — Phase 2 결과
- `docs/sessions/2026-05-10-admin-manual-walkthrough.md` — Phase 3 결과

## Memory 업데이트 권고

세션 종료 후 다음 memory 추가/갱신 권고:

- `feedback_e2e_backend_url_fallback` (NEW) — 신규 Playwright 하네스의 direct-API verb 는 `BACKEND_BASE_URL → VITE_API_BASE_URL → http://localhost:8000` fallback 적용. Vite 가 `/v1/*` proxy 없는 현재 구조에서 baseURL fallback 시 404. 본 세션 PR #45 첫 푸시 회귀 근거.
- `feedback_shadcn_switch_state_attr` (NEW) — Custom shadcn Switch (input + label) 에서 `data-state` 는 `<label>` 에, testid 는 `<input>` 에. 테스트 assertion 은 `aria-checked` 사용 권고. 본 세션 PR #45 회귀 근거.
- `project_v2_manual_state` (UPDATE 또는 NEW) — v2.0.0 매뉴얼은 PR #43, #44 정정 후 코드와 일치. 미구현 약속은 페이지별 `## Roadmap (v2.x)` 섹션으로 이전. 외부에 v2.0.0 매뉴얼 인용 시 `## Roadmap` 섹션은 약속이 아닌 비전 명시.
