# Session Handoff — 2026-05-06 — Phase 3 PR #11 — Vulnerabilities Tab (CVE list + drawer + status workflow)

## 1. 무엇을 했나

- **Phase 3 PR #11 commit 완료** — feature 브랜치 `feature/phase3-pr11-vulnerabilities`. commit `481cb2a` (32 files, +7,177 lines / −38). 4-wave 구조 (db-designer + backend / frontend / i18n + test-writer / security-reviewer) + 1건 reviewer-finding fix를 같은 PR에 포함.
- **commit 트리** (1 commit, push 대기 중):
  - `481cb2a` — Phase 3 PR #11 main work. 32 files. Backend 9 + Frontend 19 + i18n 2 + glossary 1 + tests 인입. 머지된 후 main 머지 commit + 본 핸드오프 commit 추가 예정.
- **Wave 1 — backend-developer + db-designer 병렬**:
  - **db-designer** (verification-only): 신규 마이그레이션 불필요 판정. 3개 신규 query 패턴 모두 기존 인덱스로 충분.
    - Q1 (list): `ix_vuln_findings_scan_status (scan_id, status)` 가 `WHERE scan_id = X AND status = ANY(...)` 를 BitmapIndex Scan 으로 처리. ≤10k findings/scan 에서 p95 < 200ms.
    - Q2 (detail): PK joins + `(scan_id, vuln_id)` 는 unique constraint 의 leading prefix `scan_id` + `ix_vuln_findings_vulnerability_id` BitmapAnd.
    - Q3 (audit_logs status history): 현재 audit_logs 테이블이 거의 비어있어 seq scan 이 충분하지만, **borderline** — 50k+ rows 도달 시 `(target_table, target_id, created_at)` 복합 인덱스 추가 권고. **Phase 3+ follow-up backlog 등재**.
  - **backend-developer**:
    - **schema 결정**: 기존 `vuln_finding_status` ENUM (CycloneDX VEX) 재사용. 직전 핸드오프의 `pending/under_review/approved/rejected/dismissed` 가정은 잘못된 것이었음 — 실제 enum 은 `("new", "analyzing", "exploitable", "not_affected", "false_positive", "suppressed", "fixed")`. denormalization 신규 도입 0건.
    - 3 신규 endpoints (`/v1/projects/{id}/vulnerabilities`, `/v1/vulnerability_findings/{id}`, `PATCH /v1/vulnerability_findings/{id}/status`).
    - 모든 endpoint `Depends(require_role("developer"))` + 서비스 레이어 `_can_access_team` IDOR 가드.
    - cross-team detail/PATCH = 404 (existence-hide), list = 403. 모든 cross-team 시도에 `log.warning("authz.cross_team_attempt", ...)` 사전 emit (PR #10 backlog Low #3 fix on new surface + 본 PR 의 list endpoint).
    - 422 응답이 `allowed_to: list[str]` 를 RFC 7807 extension 으로 carry. UI 가 round-trip 없이 disable 처리 가능.
    - search ILIKE `%`/`_` escape (PR #10 backlog Low #5 fix).
    - 1286 신규 LOC. ruff + mypy --strict clean.
- **Wave 2 — frontend-dev**:
  - `VulnerabilitiesTab` (415 LOC) — `react-virtuoso` `TableVirtuoso`, sticky header, URL search-param 동기화, 인라인 toolbar, skeleton/empty/error states.
  - `VulnerabilityDrawer` (703 LOC) — Sheet 우측 슬라이드, summary/details/references (URL scheme 화이트리스트)/affected components/justification textarea/status action buttons (역할 gating; `developer` 가 `→ suppressed` 시도 시 disable + tooltip)/status history timeline.
  - `VulnerabilityStatusBadge` — 7-state VEX 매핑.
  - 3 hooks (`useVulnerabilities`, `useVulnerability`, `useUpdateVulnerabilityStatus`). mutation 은 optimistic update + rollback on error + invalidate on success.
  - **date-fns 미설치 발견** — `apps/frontend/src/lib/relativeTime.ts` (80 LOC) 신규 작성. `Intl.RelativeTimeFormat` 사용, 신규 deps 없음.
  - 3개 design carry-over 준수: shadcn Tabs primitive 자체 구현 유지 (radix 미도입), recharts 미사용, `dangerouslySetInnerHTML` 0건.
  - `ProjectDetailPage` 의 Vulnerabilities `TabsTrigger` `disabled` 제거. 이제 Vulnerabilities + Components 활성, Licenses 만 disabled (PR #12).
  - `SeverityBadge` prop을 `ComponentSeverity | "unknown"` 로 widening (VEX 의 `unknown` 수용).
- **Wave 3 — i18n-specialist + test-writer 병렬**:
  - **i18n-specialist**: `vulnerabilities.*` namespace 74 키 EN + KO 양쪽 완성. **157/157 키 parity, 0 placeholder mismatches** (Python 한 줄 verifier로 검증). PR #10 의 severity terminology (`치명/높음/중간/낮음/정보/알 수 없음`) 와 정합. 7개 VEX state 신규 KO 용어 (`신규/분석 중/악용 가능/해당 없음/오탐/억제됨/수정됨`) `docs/glossary.md` 등재. action button label 은 imperative (`악용 가능으로 표시`, `분석 시작` 등) 와 badge label (`신규`, `분석 중` 등) 분리.
  - **test-writer**:
    - Backend pure-unit 63 (7×7 transition matrix grid + role policy + reflexive reject — 모두 통과).
    - Backend service-DB 35 + HTTP integration 25 collected (로컬 Postgres recovery loop 으로 미실행 — Docker 볼륨 100% 가득. **CI 가 검증 채널**).
    - Frontend vitest 71 신규 + 205 total green. Full-suite coverage **91.51% lines / 83.68% branches / 86.97% functions**.
    - PortalPage 하네스에 7 verbs 추가 (3 user-requested + 4 ergonomic), 모두 event-driven wait — `page.waitForTimeout` 0건.
    - `vulnerabilities.spec.ts` 5 시나리오 (`@vulnerabilities` 태그). Compile-verified via `playwright test --list`.
    - seed CLI 확장: `--vulnerability-count`, `--vulnerability-severity-mix`. 기본 mix `critical:2,high:5,medium:10,low:20,info:5,unknown:2`.
    - 1건 mypy fix 적용 (test_vulnerability_service.py:454, list invariance error).
- **Wave 4 — security-reviewer Producer-Reviewer 라운드**:
  - **평결: PASS-with-follow-ups**, 블로커 0, Medium 1, Low 3, Info 4.
  - **[Medium] TOCTOU on `if_match` optimistic concurrency** — SELECT-then-compare 패턴이 race window 를 만들고, ORM UPDATE 가 `WHERE id = :pk` 만 사용해 두 동시 PATCH 가 모두 통과하고 second writer wins. 본 PR 에서 **즉시 fix**: `_load_finding_with_project(...)` 에 `for_update: bool = False` 추가, `update_vulnerability_status` 가 `for_update=True` 로 호출 → `SELECT ... FOR UPDATE OF vulnerability_findings`. 행-수준 row lock 으로 (read → guard → mutate → commit) 시퀀스가 직렬화됨. audit listener 호환 유지.
  - **[Low] List endpoint missing `authz.cross_team_attempt` log** — 본 PR 에서 즉시 fix. detail/PATCH 와 동일 패턴 적용.
  - 나머지 Low / Info (justification 의 PII guidance, audit_logs lookup defense-in-depth, references server-side scheme filter, microsecond round-trip) 는 follow-up backlog.

## 2. 결정 사항 / 변경된 가정

- **schema 가정 정정 — VEX enum 사용**.
  - 직전 핸드오프 prompt 의 `pending/under_review/approved/rejected/dismissed` 는 잘못. 실제 `vuln_finding_status` ENUM 은 CycloneDX VEX (`new/analyzing/exploitable/not_affected/false_positive/suppressed/fixed`).
  - 이를 그대로 사용하면 (a) 마이그레이션 0, (b) DT 동기화 데이터와 호환, (c) CycloneDX SBOM export 가 1대1 매핑됨.
  - "Approval 워크플로우" 의도는 다음과 같이 매핑: `new → analyzing` (triage 시작) → `{exploitable, not_affected, false_positive, fixed, suppressed}` (VEX outcome). `dismissed` 의 의미는 `suppressed` 가 흡수.
- **Transition matrix 결정** (backend `STATUS_TRANSITIONS` 가 single source of truth):
  ```
  new           → {analyzing, suppressed}
  analyzing     → {exploitable, not_affected, false_positive, fixed, suppressed}
  exploitable   → {analyzing}
  not_affected  → {analyzing}
  false_positive→ {analyzing}
  fixed         → {analyzing}
  suppressed    → {analyzing}
  ```
  - Idempotent (`X → X`) = 422 (audit log 무결성 — 변경 없는 audit 이벤트 거부).
  - terminal 상태 모두 `analyzing` 로 reopen 가능 (재검토 워크플로).
- **Permission policy — `→ suppressed` 는 team_admin+ 만**.
  - `developer`: `→ suppressed` 외 모든 transition 가능.
  - `team_admin`: 전체 set. 단 **per-team role lookup** 필수 — `actor.team_roles[project.team_id]` 기준, `actor.role` (highest) 금지. team_a 의 team_admin 이 team_b 의 finding 에 접근하려 할 때 결과는 401/404 (cross-team).
- **Optimistic concurrency — row-level lock + `if_match`**.
  - `update_vulnerability_status` 가 `_load_finding_with_project(..., for_update=True)` 로 시작. `SELECT ... FOR UPDATE OF vulnerability_findings` 가 transaction 동안 행 lock.
  - 두 동시 PATCH 시 두 번째가 첫 번째 commit 까지 SELECT 에서 block → 첫 번째가 `updated_at` 갱신 → 두 번째의 if_match comparison fail → 409.
  - 비용: PATCH 당 row lock 1건. PATCH 빈도 낮으므로 무시 가능.
- **`if_match` semantics**: ISO8601 datetime echo. 클라이언트가 GET 의 `updated_at` 을 그대로 echo. 마이크로초 round-trip 정합 필요 (Info finding — JS `Date.toISOString()` 가 ms 로 truncate 시 spurious 409). 현 구현은 직접 비교; FE 가 detail.updated_at 을 verbatim echo 해서 우연히 잘 동작. byte-for-byte stable token (e.g. 별도 row-version) 으로 강화하는 것이 Phase 3+ 권고.
- **422 응답에 `allowed_to` extension carry** — RFC 7807 extension. FE 가 `extractAllowedTo(error)` 헬퍼로 narrow, 잘못된 transition 버튼을 즉시 disable. round-trip 절감.
- **PATCH 응답 = full detail** — FE 의 `useUpdateVulnerabilityStatus` 가 GET round-trip 을 생략하고 cache 직접 갱신.
- **search escape 도입** — `_` 와 `%` 가 backslash escape 되어 ILIKE pattern 으로 안전 전달. PR #10 의 backlog Low #5 fix on this PR's surface.
- **SeverityBadge widening — fork 회피**. `ComponentSeverity` 에 `none` 이 있고 `unknown` 이 없음 (vulnerability 도메인 전용). `SeverityVariant = ComponentSeverity | "unknown"` 으로 prop 확장. 기존 caller 모두 structurally 호환.
- **`Intl.RelativeTimeFormat` 사용 — date-fns 미도입**. 80 LOC `apps/frontend/src/lib/relativeTime.ts` 자체 작성. universally 지원되는 browser API, 신규 deps 0.
- **shadcn Tabs primitive 유지** — PR #11 도 radix 미도입. 본 PR 의 tabs 사용은 PR #10 의 4-tab 구조 그대로 (Vulnerabilities 가 disabled → 활성으로 변경된 것뿐). PR #12 (Licenses) 진입 시점에 radix swap 검토.
- **recharts 미사용 유지** — 본 PR 의 drawer history 는 vertical timeline list, 차트 0.
- **i18n action button vs badge label 분리** — `vulnerabilities.status.*` (badge, 짧은 noun: `분석 중`) vs `vulnerabilities.drawer.action.*` (button, imperative: `분석 시작`). 다른 namespace 로 의도적 분리. 한국어 UI 관행.
- **MEMORY.md 갱신 후보**:
  - "VEX enum 사용 / 'pending/...' 가정 금지" — 향후 Phase 3+ PR 들이 vulnerability status 를 다룰 때 같은 함정 재현 방지.
  - "if_match optimistic concurrency = SELECT FOR UPDATE 패턴" — 다른 mutation endpoints 도입 시 선례.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), #6 (55e67bd), #7 (d7bc929 + 93c41a4 merge), #8 (502f02f + ebb9c53 merge), #9 (f55e70d + 9da6b3c merge), **chore PR #1 (6366b62)**, **chore PR #2 (38236e2)**, **Phase 3 PR #10 (7d6f66d)** + chore CI fix 4건.
- **진행 중 PR**: **#11 — feature/phase3-pr11-vulnerabilities**. commit `481cb2a` push 대기 중 (사용자 정책: push/rm/destructive 는 사용자가 `!` 프리픽스로 직접 실행).
- **GitHub origin/main**: `7d6f66d` (Phase 3 PR #10 머지). + `a00bca4` (PR #10 핸드오프 commit).
- **변경 규모 (PR #11)**: 32 files, +7,177 / −38 lines.
  - Backend: 5 신규 + 3 수정. service 827 + schemas 202 + router 257 + main/init 4 + seed 50 + 신규 tests 1800.
  - Frontend: 13 신규 + 4 수정. components ~1,400 LOC + hooks 252 + lib 148 + relativeTime 80 + 신규 tests ~1,300 + e2e 253 + harness 추가 ~280.
  - i18n: 2 (EN+KO) 수정, 74 키씩.
  - Glossary: 9 라인 추가 (7 VEX states + meta).
  - 신규 테스트 274 (123 backend collected, 71 frontend, 5 e2e, 75 harness verbs).
- **통과 검증**:
  - `ruff check apps/backend` clean.
  - `mypy apps/backend` (CI 모드, tests override 적용) clean.
  - `npm run lint` 0 errors (9 pre-existing warnings).
  - `npm run typecheck` clean.
  - `pytest -q tests/unit/test_vulnerability_service.py` 63 pass (pure-unit half).
  - **로컬 Postgres recovery loop** (Docker 볼륨 가득) 으로 service-DB / HTTP integration tests 미실행. CI 가 검증 채널.
- **i18n**: EN + KO 양쪽 74 키씩 완성, parity 검증 통과.
- **CI 미실행** — 브랜치 push 대기.

## 4. 후속 backlog

### Phase 3 후속 PR (Licenses / Obligations)
- **PR #12 — Licenses 탭**: 현재 disabled placeholder. 작업 범위:
  - Backend: `GET /v1/projects/{id}/licenses` (라이선스 분포 doughnut, allow/conditional/forbidden 그룹).
  - Frontend: LicensesTab + ORT 룰 정책 표시 + obligation 추적 UI 시작점.
  - 본격 obligation 추적은 PR #13 (Obligations 탭, NOTICE 파일 자동 생성) 으로 분리 검토.
- **PR #13 — Obligations 탭**: NOTICE 파일 자동 생성, 의무사항 추적.

### security-reviewer follow-up (별도 PR)
- **(우선순위 ↑) Phase 3+ — `if_match` byte-stable ETag** — Info #1. JS `Date.toISOString()` ms 절단 회귀 가능성. 별도 row-version (BIGINT) 컬럼 또는 hash 토큰 도입 검토. Schema migration 필요 (Phase 8 hardening 일환).
- **(우선순위 ↑) Phase 8 hardening — `analysis_justification` PII guidance** — Low #4.
  - Doc: OpenAPI description + 사용자 가이드에 "secrets / tokens 입력 금지, 영구 audit 기록됨" 명시 (doc-writer).
  - Code (선택, 별도 PR): regex 기반 secret 패턴 reject (AKIA, ghp_, eyJ...). 마스킹 금지 (audit log legal 무결성).
- **별도 PR — service-layer IDOR 시도 일괄 로깅 helper** — Low #3 의 long-form. `_authz_deny(actor, target_team_id, resource, resource_id)` shared helper 를 `project_service`, `project_detail_service`, `vulnerability_service` 가 공통 사용하도록 refactor. 본 PR 에서 list endpoint 만 fix; 다른 entry point 들은 미정합.
- **별도 PR — server-side references URL scheme allow-list** — Info #2. `_build_detail_payload` 에서 `references` 필터링. FE 가 이미 mitigate 하지만 defense-in-depth.
- **별도 PR — `audit_logs` lookup defense-in-depth team filter** — Info #3. `_load_status_history` 에 `team_id` 인자 추가, `WHERE team_id = :team_id` 로 future caller 의 mis-wiring 방지.
- **별도 PR — bandit / semgrep / gitleaks / pip-audit GitHub Actions 잡 추가** — PR #10 carry-over Info. CI 자동 정적 스캔 도입. **이 PR 의 review 도 venv 부재로 dynamic tool 미실행** — 도입 가치 ↑.

### v1 carry-over backlog (PR #10 + earlier)
- **PR #10 의 security-reviewer Medium #1 (raw_data redaction)** — `mask_pii` 헬퍼를 `_size_guard` 와 같은 위치에 추가. Phase 8 hardening 우선순위.
- **PR #10 backlog Low #1 (severity / license_category enum router-level 검증)** — 본 PR 의 vulnerabilities 도메인은 service layer 화이트리스트로 mitigation 했지만, project_detail 도메인은 미해결.
- **PR #9 follow-up backlog 7개 (L-1~L-4 / I-1~I-3)** — 직전 chore PR #1 carry-over. L-1 / L-2 / L-4 (DoS 류) 우선.
- **PR #8 follow-up backlog 6개 (L-1·L-2·L-3·L-4·I-2·I-3)** — scan-pipeline-specialist 주도.
- **python-jose → PyJWT 마이그레이션** — chore PR #1 의 security-reviewer Low 권고.
- **야간 Trivy soft-fail 잡** — chore PR #1 의 Medium 권고. 미해결 carry-over.

### Phase 8 hardening 통합 backlog
- (chore PR #2 에서 등록) cdxgen-plugins-bin 카브 (image build 시 `RUN rm -rf .../cdxgen-plugins-bin*`).
- (chore PR #2 에서 등록) Dockerfile.worker base digest pin (`python:3.12.7-slim@sha256:...`).
- (chore PR #2 에서 등록) Worker container `USER` 지시문 — 현재 root 으로 celery 실행.
- (chore PR #2 에서 등록) NodeSource `curl | bash` → signed-by deb 패턴.
- (chore PR #2 에서 등록) cdxgen install 시 `npm audit signatures` 검증.

### DB 인덱스 follow-up (db-designer)
- **`audit_logs (target_table, target_id, created_at)` 복합 인덱스** — audit_logs 가 50k+ rows 도달 시. status_history endpoint 의 p95 가속.
- **`vulnerability_findings (scan_id, vulnerability_id)` 복합 인덱스** — affected_count self-join 이 hot path 가 될 때만 (현재 BitmapAnd 로 충분).
- **`vulnerability_findings (scan_id, severity_rank, cvss_score DESC)` partial index** — 정렬이 hot path 일 때만. 측정 후 결정.

### 운영 / 환경
- **사용자 환경 Docker Desktop VM 디스크 100% 가득** — `trustedoss-portal_postgres-data` volume 회복 불가능. `docker system prune -a -f --volumes` 또는 디스크 확장 필요. 본 세션도 로컬 docker 미사용 (CI 가 검증 채널) — 다음 세션 시작 시 `docker-compose -f docker-compose.dev.yml ps` 가 5/5 healthy 인지 재확인 필요.

## 5. 다음 세션 시작 지시문

### 옵션 A — Phase 3 PR #12: Licenses 탭

```
Phase 3 PR #12 — Licenses 탭 (라이선스 분포 + 정책 표시 + obligation 시작점).

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = <PR #11 merge commit>. 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 (deps hygiene) + chore PR #2 (worker image hardening + .trivyignore 카테고리 (3) 도입) + Phase 3 PR #10 (Project Detail Overview + Components) + Phase 3 PR #11 (Vulnerabilities 탭).

이번 세션 = Phase 3 PR #12 — Licenses 탭.
docs/v2-execution-plan.md §3 Licenses 항목 산출.

시작 시 검증:
  docker-compose -f docker-compose.dev.yml ps  → 5/5 healthy (디스크 정리 후)
  gh run list --limit 3                          → main 최신 success
  curl http://localhost:8000/v1/projects/<id>/vulnerabilities  → 200 (PR #11 endpoint)
  ProjectDetailPage 의 "Licenses" 탭 trigger 가 disabled 상태 확인

작업 내용 (Phase 3 PR #12):

1. backend (api/v1/licenses.py 또는 projects.py 확장):
   - GET /v1/projects/{id}/licenses — 라이선스 분포, 그룹별 (forbidden/conditional/allowed/unknown), 페이지네이션, search.
   - GET /v1/license_findings/{id} — 드로어 상세 (라이선스 정보, 영향 컴포넌트, ORT rule 매칭).
   - 별도 status workflow 없음 — license 는 "수용/거부" 정책 기반, 상태 변경 endpoint 미필요.

2. db-designer (필요 시):
   - license_findings 의 인덱스 검증 (PR #10 패턴 재사용).
   - audit_log 의 license_finding 변경 이벤트 인덱스 (마이그레이션 가능성 ↓).

3. frontend (features/projects/components/LicensesTab.tsx):
   - PR #11 의 disabled placeholder 활성화.
   - LicenseDistributionChart 재사용 (PR #10 의 OverviewTab 에 이미 있음 — 분리 검토).
   - 인라인 toolbar (search, category multi-filter, sort).
   - 드로어 — license 상세 + ORT rule + 영향 컴포넌트 리스트.

4. i18n-specialist:
   - EN/KO 번역 (license_detail 또는 project_detail 확장).
   - 기존 project_detail.licenses / common.license_category 와 정합.

5. test-writer:
   - 단위 (필터, IDOR, search escape).
   - e2e 5 시나리오 (탭 진입 / 필터 / 드로어 / sort / 빈 상태).
   - PortalPage 하네스 verbs 추가 (selectLicensesTab, openLicenseDrawer).

6. security-reviewer (Producer-Reviewer):
   - IDOR 가드 + cross-team 로깅.
   - search ILIKE escape (PR #11 패턴 carry-over).
   - 라이선스 정책 escalation 가능성 (미래 PR #13 obligation 진입 전 정책 검토).

핵심 라우팅:
  - backend-developer: API 확장.
  - db-designer: 인덱스 검증.
  - frontend-dev: LicensesTab + 드로어.
  - i18n-specialist: 번역.
  - test-writer: 단위 + e2e.
  - security-reviewer: Producer-Reviewer.

DoD:
  - main CI 전체 잡 success.
  - 신규/변경 backend + frontend coverage ≥ 80%.
  - e2e 5 시나리오 green.
  - security-reviewer 평결 PASS 또는 PASS-with-follow-ups.

주의:
  - 사용자 정책: rm/push/docker prune 거부 — 사용자가 ! 프리픽스로.
  - CLAUDE.md 규칙 (PostgreSQL only, Alembic forward-only, 인증 필수).
  - PR #10/#11 의 query-time aggregation 패턴 재사용 — denormalization 신규 도입 금지.
  - PR #11 의 _authz_deny shared helper refactor 는 본 PR 에서 진행할지 검토 (별도 chore PR 분리 권고).

세션 종료 시 docs/sessions/2026-05-XX-phase3-pr12-licenses.md 를 §7 양식으로 작성.
```

### 옵션 B — Phase 8 hardening (early): TOCTOU follow-up + raw_data redaction 일괄 처리

```
Phase 8 hardening (early) — `if_match` ETag 강화 + raw_data redaction.

main HEAD = <PR #11 merge commit>. PR #10 의 security-reviewer Medium #1 + PR #11 의 security-reviewer Info #1 카르 follow-up.

이번 세션 작업:
  1. byte-stable ETag for vulnerability_findings — row-version (BIGINT) 컬럼 또는 secure hash.
     forward-only Alembic 마이그레이션 (column 추가 + trigger).
     `if_match` semantics 갱신 (datetime → opaque token).
  2. `mask_pii` 헬퍼를 `_size_guard` 와 같은 위치에서 raw_data 적용 (PR #10 Medium #1).
  3. tasks/scan_source.py / scan_container.py 의 persistence 직전에 적용.
  4. service-layer IDOR 시도 일괄 로깅 helper (`_authz_deny`) — PR #11 Low #3 long-form refactor.
  5. 단위 테스트 회귀 점검.
  6. security-reviewer Producer-Reviewer 라운드.

DoD: 머지된 후 GET /v1/components/{id} 응답의 raw_data 에 시크릿 부재 + ETag 가 ms 절단에 견고.
```

### 옵션 C — CI 정적 스캔 잡 추가 (별도 chore PR)

```
chore — bandit / semgrep / gitleaks / pip-audit GitHub Actions 잡 추가.

main HEAD = <PR #11 merge commit>. PR #10/#11 의 security-reviewer Info: dynamic tool 미실행 일관 회귀.

이번 세션 = `.github/workflows/security-scan.yml` 신규.

작업:
  1. bandit (Python AST static analysis) — `apps/backend/`.
  2. semgrep `p/owasp-top-ten` ruleset — backend + frontend.
  3. gitleaks (secret scanner) — full repo.
  4. pip-audit — apps/backend/requirements*.txt.
  5. nightly schedule + PR trigger.
  6. 결과를 PR comment 로 게시.

DoD: 첫 번째 PR 부터 자동 정적 스캔 결과가 게시되어 future security-reviewer 라운드의 dynamic tool 단계가 항상 채워짐.
```
