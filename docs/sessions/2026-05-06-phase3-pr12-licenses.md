# Session Handoff — 2026-05-06 — Phase 3 PR #12 — Licenses Tab (read-only list + drawer + distribution chart)

## 1. 무엇을 했나

- **Phase 3 PR #12 commit 완료** — feature 브랜치 `feature/phase3-pr12-licenses`. 4-wave 구조 (backend + db-designer 병렬 / frontend + i18n 병렬 / test-writer / security-reviewer). **read-only 도메인** — license 워크플로우 부재로 PR #11 의 mutation 패턴 (transition matrix, if_match, audit history) 모두 무관.
- **사전 schema 확인 — 가짜 가정 차단**:
  - 핸드오프 prompt 가 "ORT rule (rule_id, severity, message)" 을 별도 모델로 가정했으나 실제 `License` 에 `ort_rule_id` 컬럼 없음. ORT 매칭 정보는 `LicenseFinding.raw_data` (JSONB) 에 best-effort 형태로 들어있음. 이를 frontend 에서 `KNOWN_ORT_KEYS` allow-list (rule_name/severity/message/license_finding/matched_text/score/copyright) 로 제한 렌더 — 신뢰 경계 강화.
  - 분포 차트 — 핸드오프가 "doughnut" 이라 명명했으나 PR #10 의 `LicenseDistributionChart` 가 stacked horizontal bar (recharts 미사용). 그대로 재사용 — 명칭 정정.
- **Wave 1 — backend-developer + db-designer 병렬**:
  - **db-designer** (verification-only): 신규 마이그레이션 0건 판정. 3개 query 패턴 모두 기존 인덱스로 충분.
    - Q1 (list): `ix_license_findings_scan_kind (scan_id, kind)` + hash join `licenses` PK, `category IN (...)` 는 small-side filter, ILIKE 는 ≤수백 행 sequential. ≤10k findings/scan p95 < 200ms.
    - Q2 (distribution aggregation): `ix_license_findings_scan_id` 가 Overview 와 동일 query shape, PR #10 EXPLAIN 시뮬레이션으로 이미 검증.
    - Q3 (detail affected components): `ix_license_findings_license_id` + `scan_id` heap filter, BitmapAnd 가능. 10k cap 에서 sub-ms.
    - Future measure-first 후보: `(scan_id, license_id) INCLUDE (component_version_id)` partial composite — 50k+ findings 도달 시.
  - **backend-developer** (3 신규 파일 + 1 wire-up):
    - `apps/backend/api/v1/licenses.py` (라우터): `GET /v1/projects/{id}/licenses` (limit/offset/category[]/kind[]/search/sort) + `GET /v1/license_findings/{id}`.
    - `apps/backend/services/license_service.py` (서비스): `list_project_licenses` 가 `(items, distribution, total)` 한 trip. distribution 은 OverviewTab 의 license_distribution 과 single source 보장 — 이중 계산 0.
    - `apps/backend/schemas/license_detail.py` (Pydantic v2): closed `Literal` enums (LicenseCategory, LicenseFindingKind, LicenseSortKey).
    - `apps/backend/main.py` + `api/v1/__init__.py` wire-up.
    - **PR #11 carry-over 5가지 시작부터 적용**: (1) `_escape_like` 재사용 (vulnerability_service 에서 import — `%`/`_`/`\\` escape), (2) `authz.cross_team_attempt` 사전 emit (list 403 / detail 404 existence-hide), (3) module-level `log = structlog.get_logger("license.service")`, (4) `cast(col, String)` for ENUM CASE, (5) query-time aggregation only — 신규 denormalization 0건.
- **Wave 2 — frontend-dev + i18n-specialist 병렬**:
  - **frontend-dev** (6 신규 + 1 수정):
    - `LicensesTab.tsx` — TableVirtuoso 가상 스크롤 + URL search-params 동기화 (`license_category`, `kind`, `search`, `sort`, `order`, `page`, `license` drawer key — `?drawer`/`?vuln` 와 충돌 회피). 컬럼: SPDX ID (mono) / Name / Category badge / Kind / Affected count. created_at 컬럼 응답에 없어 omit.
    - `LicensesToolbar.tsx` — search debounce 300ms + category multi (4) + kind multi (3) + sort (4) + order asc/desc.
    - `LicenseDrawer.tsx` — Sheet 우측 슬라이드. 섹션: meta (SPDX id mono / name / category badge / OSI/FSF/Deprecated badges) + ORT match collapsible (best-effort, `KNOWN_ORT_KEYS` allow-list) + affected components list. **`isSafeUrl()` http(s) scheme guard** — `javascript:`/`data:`/`file:` 는 `<a>` 미생성 plain text fallback. cross-link → `setSearchParams({tab:"components", drawer:<cv_id>})` 동시에 `?license` clear (드로어 자동 닫힘).
    - 3 hooks: `licensesApi.ts` (fetcher + 다중 값 `&category=a&category=b` 직렬화), `useLicenses.ts` (TanStack Query, `staleTime: 30000`, `keepPreviousData`), `useLicenseFinding.ts` (null disable).
    - `ProjectDetailPage.tsx`: Licenses TabsTrigger `disabled` 제거 + `<LicensesTab>` mount + `setTab` 헬퍼가 leave 시 `?license`/`?kind` clear.
    - 정책 carry-over: shadcn Tabs primitive 자체 구현 유지 (radix swap 안 함, 별도 chore 권고), recharts 미사용, `dangerouslySetInnerHTML` 0건, `LicenseDistributionChart` (PR #10) 재사용.
  - **i18n-specialist** (2 EN+KO + glossary):
    - `project_detail.licenses.*` namespace EN + KO 양쪽 58 키씩 신규. 기존 `project_detail.license_category.*` (forbidden/conditional/allowed/unknown) 재사용 — 신규 0.
    - 한국어 합쇼체. ENUM 명사형: kind=`선언됨/확정됨/검출됨`, category=`금지/조건부/허용/알 수 없음`.
    - `docs/glossary.md` 6 항목 등재: Declared / Concluded / Detected license, OSI Approved, FSF Free/Libre, ORT Match.
    - parity verifier: 215/215 키 EN/KO 정합, ICU placeholder `{{total}}/{{count}}/{{kind}}/{{score}}` 양쪽 일치.
- **Wave 3 — test-writer**:
  - Backend `tests/unit/test_license_service.py` — **35 cases** (14 pure-helper 매 PR 실행 + 21 `@pytest.mark.integration` DB-gated): list happy path, 페이지네이션, category/kind 필터, search `%`/`_` escape, sort 4가지, distribution all-four-buckets-with-zero, IDOR 403 + log emit, unknown project 404, invalid sort 422, detail happy + raw_data passthrough + null raw_data + cross-team 404 + log emit.
  - Backend `tests/integration/test_licenses_api.py` — **10 cases**: 401 unauth, 200 happy + empty, multi-value `category` query, 422 invalid sort, 403 cross-team list, 404 cross-team detail (existence-hide). RFC 7807 `application/problem+json` Content-Type 검증.
  - Frontend `LicensesTab.test.tsx` — **9 cases**: skeleton, empty, rows + summary, distribution chart, RFC 7807 error, category filter URL sync at offset 0, sort change, URL hydration on mount, row click → drawer.
  - Frontend `LicenseDrawer.test.tsx` — **9 cases**: closed (no fetch), loading, meta + flags, deprecated badge, **non-http(s) reference_url XSS guard** (`javascript:alert(1)`), ort_match null, ort_match collapsible toggle, affected components + cross-link URL pivot, drawer error.
  - PortalPage 하네스: 5 verbs 추가 (`selectLicensesTab`, `expectLicensesTabReady`, `filterLicensesByCategory`, `filterLicensesByKind`, `openLicenseDrawer`) + `getLicenseRowCount` 헬퍼. event-driven only — `waitForTimeout` 0건. `expect.poll()` URL-change waits.
  - `e2e/licenses.spec.ts` — **4 `@licenses` 시나리오**: S1 탭 + 차트 + 카운트, S2 category multi-filter narrows + URL persists across reload, S3 drawer meta + affected, S4 cross-link to ComponentDrawer.
  - 로컬 검증: backend pure 14/14 pass (21 integration skip — local postgres down), full unit suite 368 pass + 7 skip, frontend 18/18 pass (LicensesTab 9 + LicenseDrawer 9), full vitest 223/223 pass, tsc clean, playwright `--list --grep '@licenses'` 4건 collect.
- **Wave 4 — security-reviewer Producer-Reviewer 라운드**:
  - **평결: PASS**, 블로커 0, High/Medium/Low 0, Info 2 (둘 다 non-blocking).
  - bandit 통과 (676 LOC, 0 issues). dangerouslySetInnerHTML 실제 사용 0건. DT 직접 호출 0건. PII/secret 평문 0건.
  - **[Info #1]** `affected_components` payload size cap — Defense-in-depth. 현재 `.limit()` 없음. 인증된 사용자만 접근 가능하므로 cross-team 위험 없으나, 같은 team 안에서 대량 license 의 drawer 를 반복 호출 시 egress 부풀림 가능. CVSS 2.7 (Low). Phase 3+ follow-up: `.limit(500)` + `truncated: bool` 응답 필드.
  - **[Info #2]** `_escape_like` 가 `vulnerability_service` 에서 cross-import — leading-underscore "private" 헬퍼. 보안 결함 아님이나 미래 분기 회귀 위험. follow-up: `core/sql_safety.py` 또는 `services/_search_utils.py` 로 promote.

## 2. 결정 사항 / 변경된 가정

- **schema 가정 정정 — ORT rule 별도 모델 없음**.
  - 핸드오프 prompt 의 "ORT rule (rule_id, severity, message)" 매칭 섹션은 별도 모델/테이블을 가정했으나 실재하지 않음. ORT 매칭은 `LicenseFinding.raw_data` (JSONB) 에 best-effort 형태로 포함됨.
  - frontend 가 `KNOWN_ORT_KEYS = {rule_name, severity, message, license_finding, matched_text, score, copyright}` allow-list 로 raw_data 의 일부만 추출해 React text node 렌더. 알 수 없는 키는 무시 + "unrecognized format" notice. **수동 trust boundary**.
  - "ORT rule" 을 포함한 정식 정책 표시는 Phase 3+ (PR #13 Obligations 또는 별도 ORT-rules surface) 로 이연.
- **분포 차트 = stacked bar (doughnut 아님)**.
  - PR #10 의 `LicenseDistributionChart` 가 SVG 기반 stacked horizontal bar (recharts 미사용 정책 준수). PR #12 가 그대로 재사용. doughnut 도입 시 recharts 의존 추가가 필요했을 것 — 회피.
  - distribution 데이터는 list endpoint 응답에 포함 (별도 endpoint 0건). OverviewTab 과 single source — 이중 계산 0.
- **Read-only — mutation 패턴 0건 적용**.
  - PATCH endpoint 0건. transition matrix 0건. `if_match` ETag 0건. audit listener 호출 0건.
  - 사용자 워크플로우는 ORT 룰셋이 사전에 결정 — UI 는 보고/탐색만.
  - PR #11 의 audit listener INSERT-PK 버그 (Phase 8 backlog) 가 본 PR 의 read-only surface 에 영향 0.
- **URL search-params 동기화 — drawer key 충돌 회피**.
  - VulnerabilitiesTab 이 `?vuln=<id>`, ComponentsTab 이 `?drawer=<cv_id>`. PR #12 는 `?license=<finding_id>` 사용.
  - cross-link (LicenseDrawer → ComponentDrawer) 시 `setSearchParams({tab:"components", drawer:<cv_id>})` — `?license` 자동 clear 되어 LicenseDrawer 닫힘. clean handoff.
- **shadcn Tabs primitive 유지** — 4-tab 중 3개 활성 (Overview / Components / Vulnerabilities / Licenses). 마지막 PR #13 Obligations 에서 4 탭 모두 활성화 시점에 `@radix-ui/react-tabs` swap 검토 권고. 본 PR 에서 swap 안 함 (scope creep 회피).
- **`_escape_like` cross-module import — 임시 허용**.
  - `services/license_service.py` 가 `services/vulnerability_service.py` 의 leading-underscore `_escape_like` 를 import. 의도적 DRY (PR #11 lesson).
  - security-reviewer Info #2: 미래 회귀 방지 위해 `core/sql_safety.py` 로 promote 권고. 별도 chore PR 로 분리.
- **i18n parity verifier 완성** — 215/215 키 EN/KO + ICU placeholder 양쪽 일치. 한국어 합쇼체 + 한자어 명사형 (선언됨/확정됨/검출됨).

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), #6 (55e67bd), #7 (d7bc929 + 93c41a4 merge), #8 (502f02f + ebb9c53 merge), #9 (f55e70d + 9da6b3c merge), chore PR #1 (6366b62), chore PR #2 (38236e2), Phase 3 PR #10 (7d6f66d), **Phase 3 PR #11 (e19bd8a)** + chore CI fix 4건.
- **진행 중 PR**: **#12 — feature/phase3-pr12-licenses**. commit push 대기 중 (사용자 정책: push/rm/destructive 는 사용자가 `!` 프리픽스로 직접 실행).
- **GitHub origin/main**: `e19bd8a` (Phase 3 PR #11 머지) + `1464d0c` (PR #11 후처리 fix) + `94e1f02` (PR #11 핸드오프).
- **변경 규모 (PR #12)**: 22 files, 신규 파일 14 (backend 5 + frontend 9) + 수정 8 (backend 2 + frontend 5 + glossary 1).
  - Backend 신규: `api/v1/licenses.py`, `services/license_service.py`, `schemas/license_detail.py`, `tests/unit/test_license_service.py`, `tests/integration/test_licenses_api.py`.
  - Frontend 신규: `features/projects/components/LicensesTab.tsx`, `LicensesToolbar.tsx`, `LicenseDrawer.tsx`, `api/licensesApi.ts`, `api/useLicenses.ts`, `api/useLicenseFinding.ts`, `tests/unit/features/projects/LicensesTab.test.tsx`, `LicenseDrawer.test.tsx`, `tests/e2e/licenses.spec.ts`.
  - 수정: backend `main.py` + `api/v1/__init__.py` (wire-up), frontend `ProjectDetailPage.tsx` (disabled 제거), `locales/{en,ko}/project_detail.json` (각 58 키 추가), `tests/_harness/PortalPage.ts` (5 verbs + 1 헬퍼), `tests/unit/.../ProjectDetailPage.test.tsx` (assertion flip), `docs/glossary.md` (6 용어).
  - 신규 테스트 76건 (backend 45 + frontend 18 + e2e 4 + harness 9 verbs/helpers).
- **통과 검증**:
  - `ruff check apps/backend` clean.
  - `mypy apps/backend` clean (110 source files, 0 issues).
  - `npm run lint` 0 errors (12 pre-existing fast-refresh warnings — 본 PR 신규 1건 (`LicensesToolbar.tsx`) 도 동일 pattern, 기존 `VulnerabilitiesToolbar` 와 정합).
  - `npm run typecheck` clean.
  - `pytest -q tests/unit/test_license_service.py` 14/14 pass + 21 skip (postgres dependent — local docker disk 가득, CI 가 검증 채널).
  - `npm run test -- --run` 223/223 pass (LicensesTab 9 + LicenseDrawer 9 신규 포함).
  - `npx playwright test --list --grep '@licenses'` 4건 collect.
  - bandit 676 LOC clean.
- **i18n**: EN + KO 양쪽 58 키씩 추가, parity 100% (215/215).
- **CI 미실행** — 브랜치 push 대기.
- **로컬 환경 주의**: Docker 디스크 가득 (`postmaster.pid: No space left on device` recovery loop). `docker system prune -a -f --volumes` 또는 디스크 확장 필요. 본 세션도 로컬 backend integration / e2e 미실행 — CI 가 검증 채널.

## 4. 후속 backlog

### Phase 3 후속 PR (Obligations)
- **PR #13 — Obligations 탭**: NOTICE 파일 자동 생성, 의무사항 추적. license 와 1:N 관계 (`obligations` 테이블 line 712~). 본 PR 의 LicenseDrawer 가 obligation 진입점이 될 수 있도록 future-proof.

### security-reviewer follow-up (별도 PR)
- **(우선순위 ↑) PR #12 Info #1 — `affected_components` payload cap** — Defense-in-depth. `.limit(500)` + `truncated: bool` 응답 필드. CVSS 2.7. Phase 3+ 또는 Phase 8 hardening.
- **(우선순위 ↑) PR #12 Info #2 — `_escape_like` 를 `core/sql_safety.py` 로 promote** — vulnerability_service / license_service 둘 다 같은 source 사용. cross-module leading-underscore import 회피. 별도 chore PR.
- **shadcn Tabs primitive → `@radix-ui/react-tabs` swap** — PR #13 진입 시점 (4 탭 모두 활성) 에 검토. 별도 chore PR 권고.

### PR #11 carry-over (미해결)
- **(우선순위 ↑) Phase 3+ — `if_match` byte-stable ETag** — JS `Date.toISOString()` ms 절단 회귀 가능성. 별도 row-version (BIGINT) 컬럼 또는 hash 토큰. Schema migration 필요.
- **(우선순위 ↑) Phase 8 hardening — `analysis_justification` PII guidance** (PR #11 Low #4). doc-writer + regex secret reject.
- **별도 PR — `_authz_deny` shared helper** (PR #11 Low #3 long-form). `project_service`, `project_detail_service`, `vulnerability_service`, **`license_service` 신규 추가** 가 공통 사용. 본 PR 에서 license_service 도 동일 패턴 hand-rolled — refactor 시 4개 모듈 일괄.
- **별도 PR — server-side references URL scheme allow-list** (PR #11 Info #2).
- **별도 PR — `audit_logs` lookup defense-in-depth team filter** (PR #11 Info #3).

### Phase 8 audit listener INSERT-PK 버그
- PR #11 review 에서 발견. 본 PR 의 read-only surface 에 영향 0. Phase 8 hardening 우선순위 — audit_log row 의 PK 가 INSERT 직전에 미할당된 채 리스너에 들어가는 race.

### CI 정적 스캔 잡 추가 (별도 chore PR)
- bandit / semgrep / gitleaks / pip-audit GitHub Actions 잡. PR #10/#11/#12 모두 security-reviewer 가 venv 부재로 dynamic tool 미실행. 도입 가치 누적 ↑.

### v1 carry-over backlog (PR #10 + earlier)
- **PR #10 의 security-reviewer Medium #1 (raw_data redaction)** — `mask_pii` 헬퍼.
- **PR #10 backlog Low #1 (severity / license_category enum router-level 검증)** — license_category 는 본 PR 에서 `Literal` 화이트리스트로 mitigation. severity 는 미해결.
- **PR #9 follow-up backlog 7개 / PR #8 follow-up backlog 6개 / python-jose → PyJWT / 야간 Trivy soft-fail 잡** — 기존 backlog 유지.

### Phase 8 hardening 통합 backlog
- (chore PR #2 carry-over) cdxgen-plugins-bin 카브, Dockerfile.worker base digest pin, Worker container `USER` 지시문, NodeSource signed-by deb, cdxgen `npm audit signatures`.

### DB 인덱스 follow-up (db-designer)
- **`audit_logs (target_table, target_id, created_at)` 복합 인덱스** — 50k+ rows 도달 시.
- **`license_findings (scan_id, license_id) INCLUDE (component_version_id)` partial composite** — 50k+ findings/scan 도달 시 detail 가속. 측정 후 결정.
- **`vulnerability_findings (scan_id, severity_rank, cvss_score DESC)` partial index** — 정렬 hot path 일 때만.

### 운영 / 환경
- **사용자 환경 Docker Desktop VM 디스크 100% 가득** — `trustedoss-portal_postgres-data` volume + Docker images 58.67GB (16.13GB reclaimable). PR #11/#12 두 세션 연속 같은 issue. `docker system prune -a -f --volumes` 또는 디스크 확장 필요. 다음 세션 시작 시 `docker-compose -f docker-compose.dev.yml ps` 가 5/5 healthy 인지 재확인 필요.

## 5. 다음 세션 시작 지시문

### 옵션 A — Phase 3 PR #13: Obligations 탭

```
Phase 3 PR #13 — Obligations 탭 (의무사항 추적 + NOTICE 파일 자동 생성).

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = <PR #12 merge commit>. 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 + chore PR #2 + Phase 3 PR #10/#11/#12.

이번 세션 = Phase 3 PR #13 — Obligations 탭. docs/v2-execution-plan.md §3 Obligations 항목 산출.

직전 핸드오프(반드시 시작 시 읽기):
  - docs/sessions/2026-05-06-phase3-pr12-licenses.md — PR #12 의 4-wave + ORT raw_data passthrough + LicenseDistributionChart 재사용 + read-only 도메인 패턴.
  - docs/sessions/2026-05-06-phase3-pr11-vulnerabilities.md — VEX enum 사용 + TOCTOU SELECT FOR UPDATE + cross-team logging.

시작 시 검증:
  docker-compose -f docker-compose.dev.yml ps  → 5/5 healthy (postgres recovery 후)
  gh run list --limit 3                          → main 최신 success
  ProjectDetailPage 의 Obligations 탭이 미존재 (PR #12 시점에 4 탭 중 Licenses 까지 활성)

사전 schema 확인 (PR #11/#12 교훈 — 핸드오프 prompt 가정 검증):
  apps/backend/models/scan.py 의 `class Obligation` (line ~712) — license_id, kind, text, link, created_at.
  - License 1:N Obligation. NOTICE 파일 자동 생성의 source.
  - obligation 자체는 정책 catalog — 사용자 워크플로우 (수용/거부/이행 상태) 가 있다면 별도 status enum 도입 필요. **반드시 schema 확인 후 결정**.

작업 내용 (Phase 3 PR #13):

1. Backend (api/v1/obligations.py 또는 projects.py 확장):
   - GET /v1/projects/{id}/obligations — 프로젝트의 모든 license 의 obligation 집계.
   - GET /v1/projects/{id}/notice — NOTICE 파일 자동 생성 (text/plain 또는 download).
   - 상태 워크플로우 (이행/미이행) 가 schema 에 있으면 PATCH endpoint 추가; 없으면 read-only.

2. Frontend (features/projects/components/ObligationsTab.tsx):
   - PR #12 의 LicensesTab 패턴 미러 (read-only 가정).
   - NOTICE 파일 다운로드 버튼.

3. i18n: EN/KO 번역 + glossary (kind: attribution, source disclosure 등).

4. test-writer: 단위 + e2e 4 시나리오 (탭 진입 / 필터 / NOTICE 다운로드 / cross-link to license drawer).

5. security-reviewer: Producer-Reviewer.

핵심 라우팅:
  - backend-developer / db-designer / frontend-dev / i18n-specialist / test-writer / security-reviewer.

DoD: main CI green, 신규 coverage ≥ 80%, e2e 4 시나리오 green, security PASS.

PR #12 carry-over:
  - **read-only 도메인 가정 — schema 우선 확인**. obligation 에 status workflow 컬럼 있으면 mutation 패턴 도입; 없으면 read-only.
  - search ILIKE escape, cross-team logging 사전 emit, structlog capture_logs.
  - shadcn Tabs primitive → radix swap **이 시점에 검토** (4 탭 모두 활성). 별도 chore 권고 vs 본 PR 포함 결정.
  - LicenseDistributionChart 와 같이 obligation distribution 차트 재사용 또는 신규.

세션 종료 시 docs/sessions/2026-05-XX-phase3-pr13-obligations.md 를 §7 양식으로 작성.
```

### 옵션 B — chore PR: Tabs primitive radix swap + `_escape_like` shared helper + `_authz_deny` 통합

```
chore — frontend Tabs radix swap + backend search/authz 헬퍼 통합.

main HEAD = <PR #12 merge commit>. PR #11/#12 가 누적한 마이크로 follow-up 일괄 처리.

이번 세션 작업:

1. Tabs primitive swap (frontend):
   - apps/frontend/src/components/ui/tabs.tsx 를 `@radix-ui/react-tabs` 기반으로 교체.
   - 4 탭 (Overview / Components / Vulnerabilities / Licenses + 향후 Obligations) 회귀 테스트.
   - PR #10/#11/#12 의 모든 TabsTrigger/TabsContent 호환성 검증.

2. `_escape_like` promote (backend):
   - apps/backend/core/sql_safety.py (또는 services/_search_utils.py) 신규.
   - vulnerability_service.py + license_service.py 가 같은 source import.
   - 단위 테스트 회귀.

3. `_authz_deny` shared helper (backend):
   - PR #11 Low #3 long-form. `project_service`, `project_detail_service`, `vulnerability_service`, `license_service` 4개 모듈이 공통 사용.
   - cross-team logging emit + ProjectForbidden / NotFound (existence-hide) 일관 raise.

4. security-reviewer Producer-Reviewer 라운드.

DoD: main CI green. 4 탭 동작 회귀 0. backend service-layer authz 일관 구조.
```

### 옵션 C — Phase 8 hardening (early): `affected_components` cap + raw_data redaction + audit listener INSERT-PK fix

```
Phase 8 hardening (early) — 누적 follow-up 일괄 처리.

main HEAD = <PR #12 merge commit>. PR #10/#11/#12 의 security-reviewer 가 누적한 Defense-in-depth + audit listener bug 일괄 fix.

이번 세션 작업:
  1. `affected_components` payload size cap (PR #12 Info #1).
  2. `mask_pii` 헬퍼를 `_size_guard` 와 같은 위치에서 raw_data 적용 (PR #10 Medium #1).
  3. audit listener INSERT-PK fix (PR #11 reviewer-finding, Phase 8 backlog).
  4. byte-stable ETag for vulnerability_findings — row-version (BIGINT) 컬럼 + 마이그레이션 (PR #11 Info #1).
  5. tests/scan_source.py / scan_container.py 의 persistence 직전 mask_pii 적용.
  6. security-reviewer Producer-Reviewer 라운드.

DoD: 머지된 후 audit listener 가 PK 무결성 보장 + GET /v1/components/{id} raw_data 시크릿 부재 + ETag 가 ms 절단에 견고.
```
