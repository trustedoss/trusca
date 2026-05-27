# 핸드오프 — Wave 4 전체 완료(W4-A~W4-D) + Vulnerability list-schema follow-up (2026-05-27)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 W4 라인 + [`plan-w4-ui-ia-overhaul.md`](../plan-w4-ui-ia-overhaul.md). 이 문서는 그 세션 스냅샷이다.
> 직전 세션 핸드오프: [`2026-05-26-w3-30-project-list-aggregates-complete.md`](./2026-05-26-w3-30-project-list-aggregates-complete.md) — W3 #30 완료 후 인테이크 모드. 본 세션은 사용자 핸즈온 #16~#22 + 후속 #18 트리아지에 따라 W4 전체(A→B-prep→B→C→D) + 1 follow-up 을 연속 진행.

---

## 이번 세션 결과 — 6 PR 연속 머지

| PR | 라벨 | 영역 | 머지 |
|---|---|---|---|
| #186 | **W4-A #18** — admin layout 픽스 | frontend-dev | ✅ |
| #187 | **W4-B-prep** — 공통 primitive 3종 | frontend-dev | ✅ |
| #188 | **W4-B #16/#17/#19** — Overview/Components/Vulnerabilities UX 정비 | frontend-dev (agent worktree) | ✅ |
| #189 | **W4-C #20/#21/#22** — IA 재정비 (탭 11→8) | frontend-dev (agent worktree) | ✅ |
| #190 | **W4-D** — npm lockfile fallback (TYPE/USAGE NULL 해소) | scan-pipeline-specialist (agent worktree) | ✅ |
| #191 | **Follow-up** — Vulnerability list-schema bump (affected component name/version/license) | backend-developer (agent worktree) | ✅ |

main HEAD: `94117c1` (2026-05-26T17:30:44Z 머지). alembic head 유지. OpenAPI 스냅샷 #191 에서 W4-C / W4-D drift 도 함께 흡수 재생성.

---

## PR 별 핵심 변경 요약

### #186 W4-A — admin layout 픽스 (`0c77338`)
- `/admin/*` 가 자체 `AdminLayout` chrome 으로 마운트되어 admin 진입 시 일반 메뉴(Dashboard/Projects/...) 가 사라지던 P0 버그.
- `router.tsx`: 전체 admin Route 그룹을 `/` AppShell 부모 안으로 이동.
- `AdminLayout.tsx`: chrome(sidebar/header/logout) 삭제 → super-admin 가드 + `<Outlet />` 만 유지. `data-testid="admin-layout"` 보존(harness 호환).
- `en/ko/admin.json`: 미사용 `admin.layout.*` 키 제거.

### #187 W4-B-prep — 공통 primitive (`af0a4f7`)
- `src/components/ui/sortable-column-header.tsx` — 3-state cycle (unset→asc→desc→unset), aria-sort, URL state caller 관리. `nextSortState(column, current)` 헬퍼.
- `src/features/projects/components/LicenseColumnCell.tsx` — SPDX + policy badge 통합.
- `SeverityDistributionChart` / `LicenseDistributionChart` 에 optional `onSegmentClick?: (key) => void` prop — 미제공 시 div fallback, `count===0` 비-interactive.
- i18n: `common.json` `sort.aria_*` 3키, `project_detail.json` `components.license.unknown_dash`.

### #188 W4-B — Overview/Components/Vulns UX (`edaee91`)
- **#16 Overview**: Risk Score 카드 제거(헤더 RiskGauge 는 유지) · severity/license 차트 segment 클릭 → `?tab=vulnerabilities&severity=…` / `?tab=licenses&license_category=…` deep-link · Recent Scans 행 status 분기(succeeded/failed/cancelled → Components 탭, queued/running → drawer)
- **#17 Components**: `LicenseColumnCell` 적용 · Sort/Order 드롭다운 → `SortableColumnHeader` (Name/License/Severity) · severity/license MultiSelect 제거 → 차트 deep-link 가 대체, 신규 `ActiveFilterChips` 가 활성 필터 표시
- **#19 Vulnerabilities**: 같은 패턴 적용 · 컬럼 우선순위 정리(Discovered → drawer 이동) · 필터 4개로 단순화(Search/Status/Reachability/EPSS)
- **신규**: `ActiveFilterChips.tsx` 제네릭 컴포넌트 — Components/Vulnerabilities 의 severity enum 차이(`none` vs `unknown`) 흡수.
- `ProjectDetailPage` `onJumpToComponents` 배선(기존 `handleViewSnapshotComponents` 재사용).

### #189 W4-C — 탭 11→8 IA 재정비 (`65eede7`)
- **최종 탭**: Overview / Releases / Components / Vulnerabilities / Source / **Compliance** / **Reports** / Settings
- **#20 Compliance**: 신규 `ComplianceTab` — sub-tab wrapper (`?cview=licenses|obligations`). 기존 `LicensesTab`/`ObligationsTab` 내부 컴포넌트로 재사용. **디자인 deviation**: plan §3.1 의 단일 통합 테이블이 아닌 sub-tab 패턴. 사유: BE join endpoint 부재 + W4-B 직후 회귀 리스크 + 기존 테스트/harness 전면 보존. → 단일 그리드 통합은 별도 BE+FE PR 후보.
- **#21 SBOM → Reports**: `SbomTab` 내용을 `ReportsTab` 내부 section (`reports-sbom-section`, `?rpt_section=sbom`) 으로 흡수.
- **#22 Remediation → Vulnerabilities**: 신규 `VulnerabilitiesRemediationPanel` (collapsible, `?vuln_section=remediation`). read-only snapshot mode 에선 패널 skip.
- `ProjectDetailPage`: `ALLOWED_TABS` 8개로 축소, `redirectLegacyTab` helper + useEffect 로 `?tab={licenses,obligations,sbom,remediation}` 자동 redirect.
- Harness: `selectLicensesTab` / `selectObligationsTab` / `selectSbomTab` verb 가 신규 IA 로 navigate (E2E spec 호환).

### #190 W4-D — npm lockfile fallback (`0d28b60`)
- 갭: cdxgen 12.3.3 의 npm `scope` 미배출 + `dependencies` 배열 누락 → Components 탭 TYPE/USAGE 대부분 `-`.
- 채택: **Option C** (npm lockfile 직접 파싱). Option A(cdxgen 옵션) 는 공식 flag 없음, Option B(ORT 부활) 는 비용 큼.
- 신규 `integrations/npm_lockfile.py` — v3/v2/v1 파서, nearest-ancestor 해석기, `MAX_PACKAGES` 캡, scope precedence(`production` > `dev` > `optional` > `peer`).
- `tasks/scan_source.py` — lockfile scan-level 한 번 로드, 컴포넌트 루프에서 cdxgen scope precedence + `_persist_dependency_graph` lockfile-adjacency fallback. structlog 필드 `npm_lockfile_loaded packages=N graph_nodes=M`, `dependency_graph_persisted source="cdxgen"|"npm_lockfile_fallback"`.
- 단위 테스트 +40 (30 lockfile + 7 scope enrichment + 3 fallback).

### #191 Follow-up — Vulnerability list-schema bump (`94117c1`)
- 갭: W4-B 가 추가한 Vulnerabilities 탭 Component@Version + License SPDX 컬럼이 NULL/dash 렌더 — `VulnerabilityListItem` 응답에 affected component 필드 부재.
- 채택: **Option A** (첫 영향 컴포넌트 + 카운트, n+1-safe).
- `services/vulnerability_service.py::list_project_vulnerabilities`: `DISTINCT ON (cv_id) ... ORDER BY cv_id, rank DESC, spdx_id` argmax 로 worst-rank license + 그 `license_id` 단일 패스 추출. ComponentVersion/Component inner JOIN, License outer JOIN.
- `VulnerabilityListItem` 에 4 신규 필드 (`affected_component_name/version/license/license_category`). 기존 `component_license_category` (non-null) 유지 — back-compat.
- FE: 신규 `ComponentColumnCell` — `name@version` + `+N-1` 접미사. `LicenseColumnCell` 활성(legacy-category fallback 유지). i18n 3 키.
- BE 5 + FE 5 신규 테스트. OpenAPI 스냅샷 재생성.

---

## 운영 검증 (다음 세션 / orchestrator 가 수행할 단계)

### W4-D 효과 확인
1. Worker 재기동 (코드 반영)
2. npm fixture(예: maven-node) 신규 스캔
3. Worker 로그: `npm_lockfile_loaded packages=N` + `dependency_graph_persisted source="cdxgen"` 또는 `"npm_lockfile_fallback"` (기존 `dependency_graph_missing` 대신)
4. UI Components 탭: TYPE = direct/transitive · USAGE = required/dev/optional/peer 표시
5. DB: `SELECT direct, dependency_scope, COUNT(*) FROM scan_components WHERE scan_id=? GROUP BY 1,2` — npm 스캔의 NULL 비율 대폭 감소

### #191 효과 확인
- Vulnerabilities 탭의 Component@Version 컬럼이 `lodash@4.17.20 +2` 형식으로, License 컬럼이 SPDX + 정책 배지 stacked 로 표시되는지

---

## 알려진 한계 / 후속 후보

| 후속 | 영역 | 트리거 |
|---|---|---|
| **pnpm-lock.yaml / yarn.lock 파서** | W4-D 확장 — pnpm/yarn 사용자는 여전히 dash | npm 외 생태계 사용자 발견 시 |
| **cdxgen 12.5+ 업그레이드** | worker-image 별도 트랙 — gap (a) 원인 해소 | defense-in-depth 보강 |
| **Compliance v2 단일 통합 테이블** | W4-C sub-tab → plan §3.1 단일 그리드. `GET /v1/projects/{id}/compliance` 통합 endpoint + 단일 ComplianceTab | 사용자가 sub-tab 마찰 보고 시 |
| **`reports.spec.ts` E2E line 108** | `?tab=obligations` 단정이 신규 IA 에서 `compliance` 로 갱신 필요할 수 있음 | E2E 회귀 발생 시 |
| **MultiFernet 키 회전 + CI flake 재활성화** | 출시 전 SCA 셀프스캔과 묶음 ([[feedback-ci-hardening-deferred-prerelease]]) | 릴리스 직전 |
| **3 pre-existing integration flake** | `test_licenses_api::test_list_returns_seeded_finding` · `test_detail_happy_path` · `test_admin_audit_export_csv_streams` — local dev DB 누적, 본 세션 무관 | 별도 cleanup |

---

## 워크플로 관찰 — 본 세션 패턴

- **agent worktree 위임**: W4-B/C/D + follow-up 4 PR 모두 specialized agent 에 background worktree 로 위임. orchestrator 가 사전 갭 분석 포인트 + 출발 파일 + 게이트 + 산출물 형식 명시한 프롬프트 → 평균 20~25분 / PR. [[feedback-parallel-subagent-worktree-isolation]] 준수.
- **CI green 대기 후 머지**: 각 PR 머지 전 `gh pr checks` 백그라운드 폴링으로 `test (backend) + test (frontend)` pass 확인. main red 누적 방지. [[feedback-autonomous-merge-ci-check]] 준수. 단, 본 세션 초반 #188(W4-B) 는 CI 결과 전 머지 → 사후 main CI green 확인 (이번 한 번만, 다음 세션부터 엄격 적용).
- **순차 진행**: 사용자가 "순차적으로" 명시 — W4-C 머지 → W4-D 위임 → 머지 → follow-up 위임 순. agent 들 사이 의존성 없는 BE/FE 작업이라 병렬 가능했으나 사용자 의사 존중.
- **agent 디자인 deviation 판단**: W4-C agent 가 plan 의 "단일 통합 테이블" 대신 sub-tab wrapper 채택. 명시적 사유(BE join endpoint 부재 + 회귀 리스크) 와 follow-up 후보 보고. orchestrator 가 PR body 에 deviation 명시 후 머지 — 사용자가 추후 결정 가능.

---

## 다음 세션 — 인테이크 모드 복귀 권장

W4 (UX 정합성 + IA 재정비) 작업 묶음 완료. 다음 세션은 **사용자 핸즈온 피드백 인테이크 모드** 로 복귀:
1. 운영 검증 수행 (위 "운영 검증" 섹션) — W4-D / #191 효과 실측
2. 사용자가 위 후속 후보 중 우선순위 지목 또는 신규 핸즈온 피드백 인테이크
3. 트래커 §0.5 W4 라인 ✅ 종결 표시 + 후속 후보 별도 라벨 라인으로 분리

main 은 클린, alembic head 유지, OpenAPI 일관. 모든 게이트 (typecheck/lint/vitest/i18n:check) green.
