# 다음 세션 시작 지시문 — chore PR #3 (PR #11/#12/#13 누적 hardening + refactor)

> 새 세션의 첫 메시지로 아래 코드 블록을 그대로 복사해서 사용한다.
> `<PR #13 merge commit>` 자리는 PR #13 이 머지된 후의 main HEAD 으로 치환.
> 머지 commit 이 아직 없다면 새 세션 시작 직전에 GitHub 에서 머지 후 진행한다.

```
chore — PR #11/#12/#13 누적 security-reviewer follow-up + 5-탭 baseline refactor 일괄 처리.

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = <PR #13 merge commit>. 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 (deps hygiene) + chore PR #2 (worker image hardening + .trivyignore 카테고리) + Phase 3 PR #10 (Project Detail Overview + Components) + Phase 3 PR #11 (Vulnerabilities 탭) + Phase 3 PR #12 (Licenses 탭) + Phase 3 PR #13 (Obligations 탭 + NOTICE generator).

이번 세션 = chore PR #3 — Phase 3 의 read 도메인 5개 (project_service / project_detail_service / vulnerability_service / license_service / obligation_service) 가 누적한 follow-up 을 단일 PR 로 통합 처리. **새 기능 0건**, 행동 변화는 (1) 응답 size cap + (2) nosniff 헤더 + (3) /notice rate-limit 세 가지뿐. 나머지는 동작 보존 refactor.

직전 핸드오프(반드시 시작 시 읽기 — 같은 follow-up 이 3 PR 에 분산되어 있으므로 한 번에 맥락 복원 필요):
  - docs/sessions/2026-05-07-phase3-pr13-obligations.md — PR #13 의 read-only catalog + Low #1 (affected_components + obligation.text size cap) + Info #2 (nosniff) + 5 service 모듈 _authz_deny 추천.
  - docs/sessions/2026-05-06-phase3-pr12-licenses.md — PR #12 의 Info #1 (affected_components 캡) + Info #2 (_escape_like cross-module import → core/sql_safety promote 권고).
  - docs/sessions/2026-05-06-phase3-pr11-vulnerabilities.md — PR #11 의 Low #3 (_authz_deny shared helper long-form refactor — 4→5 모듈로 누적).

시작 시 검증:
  docker-compose -f docker-compose.dev.yml ps  → postgres recovery 후 5/5 healthy
  gh run list --limit 3                          → main 최신 success
  ProjectDetailPage 5 탭 모두 활성 (Overview/Components/Vulnerabilities/Licenses/Obligations) 회귀 0
  apps/backend/services/{project,project_detail,vulnerability,license,obligation}_service.py 모두 존재
  apps/backend/services/license_service.py 가 vulnerability_service._escape_like 를 import 하는지 (PR #12 의 cross-module 부채)

사전 코드 fact-check (반드시 시작 시 grep 으로 확인 후 결정 — PR #11/#12/#13 의 prompt-가정 차단 교훈):
  grep -n "_escape_like" apps/backend/services/*.py
  grep -n "for actor.role\|cross_team_attempt\|ProjectForbidden\|raise ProjectNotFound\|raise.*NotFound" apps/backend/services/{project,project_detail,vulnerability,license,obligation}_service.py
  grep -n "_load_affected_components\|affected_components" apps/backend/services/{license,obligation}_service.py
  grep -rn "TabsTrigger\|@radix-ui/react-tabs" apps/frontend/src/components/ui/tabs.tsx apps/frontend/src/features/projects/ProjectDetailPage.tsx

작업 내용 (chore PR #3 — 의존성 순서 따라 진행):

[1] core/sql_safety.py 신규 — `_escape_like` promote (PR #12 Info #2):
   - 신규 파일: apps/backend/core/sql_safety.py — `escape_like(value: str, *, esc: str = "\\") -> str` (public 이름).
   - vulnerability_service / license_service / obligation_service 의 `from services.vulnerability_service import _escape_like` 를 `from core.sql_safety import escape_like` 로 교체.
   - vulnerability_service 의 `_escape_like` 정의 자체는 유지(또는 thin re-export) — 외부 호출자가 있을 수 있음. 의도적으로 deprecation 추가 (`# DEPRECATED: import from core.sql_safety` comment) 또는 직접 삭제 후 호출자 일괄 갱신. 시작 시 grep 으로 호출자 수 확인 후 결정.
   - 단위 테스트: tests/unit/test_sql_safety.py — `%`, `_`, `\` literal escape + ESCAPE clause round-trip.

[2] core/authz.py 신규 — `_authz_deny` shared helper (PR #11 Low #3):
   - 신규 파일: apps/backend/core/authz.py — `assert_team_member(actor, team_id, *, resource: str, resource_id: str, log: structlog.BoundLogger, on_deny: type[ProjectError]) -> None`. 또는 callable raise 패턴.
   - 5개 service 모듈 (project / project_detail / vulnerability / license / obligation) 의 `_can_access_team(...)` + `log.warning("authz.cross_team_attempt", ...)` + `raise ProjectForbidden|NotFound` boilerplate 를 1 호출로 압축.
   - on_deny 인자로 list = ProjectForbidden(403), detail/notice = NotFound(404) 분기 명시 (existence-hide 정책 보존).
   - 단위 테스트 회귀: 기존 IDOR 케이스가 동일 status + log 이벤트 emit 검증.

[3] response payload size cap (PR #12 Info #1 + PR #13 Low #1 통합):
   - schemas/license_detail.py + schemas/obligation_detail.py 에 응답 envelope 확장:
     - `LicenseDetailResponse`: `affected_components_truncated: bool` + 같은 위치에 `affected_components_total: int` 추가 (응답 호환성 — 기본 false / 0).
     - `ObligationDetailResponse`: 동일.
   - services/license_service.py::_load_affected_components: `.limit(_AFFECTED_COMPONENTS_CAP=500)` + 별도 count query 로 total 산출 → 캡 도달 시 truncated=True.
   - services/obligation_service.py: 동일.
   - services/obligation_service.py::list_project_obligations + get_obligation_detail 에서 `obligation.text` 가 64 KiB 이상이면 `text[:65_536]` clamp + 응답 envelope 에 `text_truncated: bool` 필드 추가.
   - frontend: ObligationDrawer / LicenseDrawer 에 truncated 표시 — 카운트 문구 + "Showing 500 of N" 류 라벨. i18n 키 4개씩 EN/KO 추가.
   - 단위 테스트: 501-row affected_components 시 truncated=True + length=500.

[4] core/middleware.py — SecurityHeadersMiddleware (PR #13 Info #2):
   - 신규 미들웨어: 모든 응답에 `X-Content-Type-Options: nosniff` + `Referrer-Policy: no-referrer` + `X-Frame-Options: DENY` (기본 hardening 묶음) 부착. CSP 는 별도 PR (HTML 응답 surface 가 vite dev server 라 충돌 가능 → 본 PR scope 외).
   - main.py 에 추가. 순서: outermost (CORS 보다 outer 또는 inner — CORS preflight 응답에도 nosniff 부착되어야 하므로 inner 권고).
   - 단위 테스트: TestClient 로 GET /health 응답에 헤더 3종 확인.

[5] /v1/projects/{id}/notice rate-limit (PR #13 follow-up):
   - api/v1/obligations.py 의 `get_project_notice_endpoint` 에 `@limiter.limit("10/minute")` 데코레이터.
   - 무거운 read (LicenseFinding scan + array_agg + obligation join) 의 fan-out 방지.
   - 단위 테스트: 11번째 요청이 429 + Retry-After 헤더.

[6] RFC 6266 filename* (PR #13 Info #1, 우선순위 낮음 — scope 안에 들어가면 포함):
   - api/v1/obligations.py::_safe_filename_token 옆에 `_format_content_disposition(name: str, fmt: NoticeFormat) -> str` 신규.
   - ASCII 폴백 (`filename="NOTICE-<token>.<ext>"`) + UTF-8 (`filename*=UTF-8''<percent-encoded>`).
   - 단위 테스트: 한국어 프로젝트 이름 round-trip.

[7] shadcn Tabs primitive → @radix-ui/react-tabs swap (PR #12 carry-over, 5 탭 모두 활성 시점):
   - apps/frontend/src/components/ui/tabs.tsx 를 radix 기반으로 교체. shadcn 표준 패턴 그대로.
   - npm i @radix-ui/react-tabs (peer 가 이미 있으면 skip).
   - 5 탭 회귀 테스트: ProjectDetailPage e2e + project_detail unit 가 deep-link / setTab clear 분기 모두 동일하게 동작.
   - 본 PR 에 포함할지 별도 chore 로 분리할지는 시작 시 판단 — 5 탭 deep-link 회귀 위험이 높으면 별도 PR 권고.

[8] test-writer 회귀 + security-reviewer Producer-Reviewer.

핵심 라우팅:
  - backend-developer: [1]~[6] 모듈 + 미들웨어 + 데코레이터 + schema 확장. 만약 limit 도달하면 메인 세션 직접 구현 (PR #13 의 fallback 패턴 재사용).
  - db-designer: 본 PR 은 schema 변경 0 — 호출 불필요.
  - frontend-dev: [3] truncated 표시 + [7] Tabs swap.
  - i18n-specialist: [3] EN/KO 4 키씩 + glossary "Truncated" 항목 (필요 시).
  - test-writer: 단위 회귀 (sql_safety + authz helper) + integration (size cap + nosniff + rate-limit) + e2e 회귀 (Tabs swap 시 5 탭 deep-link).
  - security-reviewer: Producer-Reviewer 라운드. 누적 follow-up 종결 평결 (PASS 목표).

설계 제약:
  - 본 PR 은 **새 기능 0건**. 행동 변화는 size cap / nosniff / rate-limit 3건뿐. 나머지는 동작 보존 refactor → 회귀 위험 ↑, 단위 테스트로 차단.
  - **응답 호환성**: schema 신규 필드 (`*_truncated`, `*_total`) 는 기본값으로 추가. 기존 frontend 가 미사용해도 무관.
  - **CLAUDE.md 규칙**: PostgreSQL only / Alembic forward-only (본 PR migration 0건) / 인증 필수 / docker-compose V1 (하이픈) / `os.getenv()` 런타임 호출.
  - **read-only**: 본 PR 은 mutation endpoint 미변경. PATCH endpoints (vulnerability_service.update_status) 의 `if_match` ETag 강화는 별도 PR (PR #11 Info #1, schema migration 필요하므로 분리).
  - **scope creep 차단**: PR #13 핸드오프 §4 의 "Phase 8 audit listener INSERT-PK 버그" + "byte-stable ETag" + "PII guidance" 는 본 PR 범위 외. 의도적으로 미루어 둔다.

DoD:
  - main CI 전체 잡 success.
  - `ruff check apps/backend` clean / `mypy apps/backend` clean (변경 모듈 mypy --strict 호환).
  - `npm run lint` errors 0 / `npm run typecheck` clean.
  - 신규/변경 backend coverage ≥ 80%.
  - 5 탭 회귀: e2e `@licenses` / `@obligations` / `@vulnerabilities` 모두 green.
  - `tests/integration/test_obligations_api.py` + `test_licenses_api.py` 의 신규 truncated/nosniff/rate-limit 케이스 추가 통과.
  - security-reviewer 평결 PASS (또는 PASS-with-follow-ups, 단 follow-up 이 본 PR 에서 다룬 6 항목 외의 새 발견에 한함 — 누적 부채 종결 의도).

비주문:
  - 새 도메인 / 새 endpoint 도입 금지 (refactor + hardening 만).
  - schema migration 작성 금지 (`if_match` byte-stable ETag 는 별도 PR).
  - audit listener INSERT-PK fix 금지 (Phase 8 hardening 별도 PR).
  - cdxgen / ORT scan task 의 obligation 자동 채움 금지 (옵션 C 별도 PR).

세션 종료 시 docs/sessions/2026-05-XX-chore-pr3-hardening.md 를 docs/v2-execution-plan.md §7 양식으로 작성 — 다음 세션은 옵션 B (Phase 4 — 알림 시스템) 또는 옵션 C (scan pipeline obligation 자동 채움) 중 선택할 수 있도록 §5 양식으로 두 옵션 모두 등재.
```
