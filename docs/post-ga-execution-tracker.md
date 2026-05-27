# TrustedOSS Portal — v2.1~v2.3 실행 트래커 (Execution Tracker)

> 작성일: 2026-05-24 | 기준: main HEAD `#144` (17ea353)
> **이 문서는 v2.1~v2.3의 "어떻게/언제/지금 어디" 단일 진실(single source of truth)이다.**
> "무엇을/왜"의 전략 근거는 [`post-ga-roadmap.md`](./post-ga-roadmap.md)에 있다(상위 문서).
> 본 문서는 그 로드맵을 **PR 단위로 분해 + 진행 상태를 살아있게(living) 추적**한다.
>
> **운영 규칙: 모든 PR 머지 직후 이 문서의 해당 체크박스를 갱신한다. 갱신 안 된 진행은 진행이 아니다.**

---

## 0. 진행 대시보드 (한눈에)

| 마일스톤 | 트랙 | PR 수 | 완료 | 상태 |
|---|---|---|---|---|
| v2.1 | A. VEX 소비 (트리아지) | 3 | 3 | ✅ 완료 (#145,#148,#150) |
| v2.1 | B. 평가·배포 경로 | 5 | 5 | ✅ 완료 (#146,#147,#149,#151,#152) |
| v2.2 | 리메디에이션 + 정책 | 10 | 10 | ✅ 완료 (#153,154,156,157,158,159,160,161,162,163,164) — b/c 전 트랙 + b3-UI |
| v2.3 | 무결성 + 우선순위화 | 6 | 5 | 🟢 마일스톤 충족 (s1·r1·s2·r2·s3 머지; r3=선택·종료조건 초과) |
| §0.5 | Wave 1~3 (BD 정합·발견성) | 7 | 7 | ✅ 완료 (W1 #29·#34·#35 · W2 #31·#33 · W3 #30·#32) |
| §0.5 | Wave 4 (UX 정합·IA 재정비) | 6 | 6 | ✅ 완료 (#186 W4-A · #187 prep · #188 W4-B · #189 W4-C · #190 W4-D · #191 follow-up) |
| §0.5 | **Wave 6 (DT 제거 + Trivy 교체)** | 7(+선택1) | 3 | 🟦 진행중 — #45 ✅ → #40 ✅(PR #196) + chore L1/L2/L4 ✅(PR #199) → #41 ✅(PR #200) → #42 🟦 → #43a~e → #44 (v2.3.1 prereq 스킵, shadow 게이트 스킵, v2.4.0이 첫 공개 릴리스) |
| §0.5 | **Wave 7 (Docs parity)** | 5(+조사1) | 0 | ⏳ 백로그 — W6 종료 후 착수 검토. DT 문서 비교 갭 5종(Triage 통합·Analysis Types·Best Practices·FAQ·Change Log) |
| — | 운영 레인 (외부 블로커) | 4 | 0 | ⬜ 대기 (O1·O3는 v2.4.0 첫 공개 릴리스에 통합) |

범례: ⬜ 대기 · 🟦 진행중 · ✅ 완료 · ⛔ 블로킹 · ⏳ 백로그

**현재 상태(2026-05-27 후속 결정):** v2.1·v2.2·v2.3 + §0.5 Wave 1~4 모두 종결. **W6 진행 중**: #45(PR #192) · #40(PR #196) · L1/L2/L4 chore(PR #199) · #41(PR #200) · #42(PR #203) · chore-seed(PR #204) · **#43a BE DT 제거(PR #205)** 모두 머지 완료 — **5/7**. **Repo는 W6 진행 동안 private 전환**, v2.4.0이 DT-free 첫 공개 릴리스가 되며 **v2.3.1 동결 태그/Release/SECURITY.md backport 정책 prereq는 스킵**(외부 사용자 0이라 "Final DT Release" 공개 마커 무의미). 운영 레인 O1/O3은 v2.4.0 GA에 통합 수행. 다음 PR: **W6-#43b 프론트엔드 DT 제거**.

---

## 0.5 마일스톤 후 — 수동 테스트 발견 + Black Duck 정합 (Wave 1~3)

> 출처: 2026-05-25~26 사용자 핸즈온 테스트(실제 github repo 스캔) + Black Duck 6화면 UX 갭분석.
> 태스크 #29~#35가 PR-단위 SoT. **버전 엔티티는 보류** — "릴리스 = 성공한 스캔" 모델 유지(사용자 "릴리스마다 해야해" 확정), #28(스냅샷 조회+diff)로 충족, 발견성은 #30로 보완.

**핵심 통찰:** #29·#35·#34는 한 화면 여정에서 연쇄로 터진 **"스캔 결과 신뢰 루프"** 붕괴다 — 스캔 시작 → 상태를 다시 못 봄(#29) → 결과 취약점 0(#35) → 그런데 Risk 100(#34). 표준 기능(#30~#33)보다 신뢰 복구가 먼저.

| Wave | 태스크 | 내용 | 상태 |
|---|---|---|---|
| **W1 신뢰 복구** | **#29** | 스캔 트리거 후 상태 추적: `recent_scans` 항상 노출(스냅샷 게이팅 제거) + 헤더 영속 "진행 중" 칩으로 드로어 재오픈 | ✅ 완료 (2026-05-26) |
| W1 | **#35** | DT 무경고(silent zero): (운영) `nvd.api.enabled=true`+재시작 → NVD 미러 가동(0→43k+, 352k 목표). (Surface A) admin/DT vuln-DB 카운트 + 0건 경고 Alert. (Surface B) 스캔 시점 DT vuln-DB 크기를 `scan_metadata`에 저장 → Overview에 `vuln_data_available` 노출, Security 0·DB비었음 시 "데이터 미적재" 캐비엇 | ✅ 완료 (2026-05-26, A·B·운영) |
| W1 | **#34** | 리스크 점수 2축 재설계: Security/License 분리 + 비포화 점수(`services/risk_score.py` 단일 소스, conditional 단독 "Critical" 제거). 밴드=최악 등급, 밴드 내 `n/(n+4)`. `risk_score`=max(축) back-compat | ✅ 완료 (2026-05-26) |
| **W2 BD 정합** | **#31** | Components 탭 Direct/Transitive + Usage 노출. (BE) `ComponentSummary`/`ComponentDetailResponse`에 `dependency_scope`(req>opt 집계, NULL→`—`) 추가, `?direct=true|false`·`?dependency_scope=required\|optional\|unspecified`(미지값 drop, no 422) 필터, `_SCOPE_RANK`/`_normalize_scope_filter` 미러. (FE) `DependencyTypeBadge`/`DependencyScopeBadge` 신규, Components 테이블 컬럼·툴바(Type 3-state segment + Usage MultiSelect) + 드로어 meta, EN/KO 22키. 게이트: ruff/mypy clean·pytest 54·typecheck·lint·i18n:check·vitest 908(+21). | ✅ 완료 (2026-05-26) |
| W2 | **#33** | (정정) "조치신호(Exploitable/Solution)+CVSS 벡터"는 **이미 구현됨** — Exploitable=`status='exploitable'` 7-state enum + 드로어 status 배지, Solution=v2.2-a3 `upgrade_recommendation` + `DrawerUpgradeSection`, CVSS 벡터=`Vulnerability.cvss_vector` + 드로어 `cvss_vector_label`. **실제 남은 갭**: (a) 목록 License 리스크축 ✅ 완료(2026-05-26). (b) **Bulk actions** ✅ 완료(2026-05-26): `POST /v1/projects/{id}/vulnerabilities:bulk-transition` 엔드포인트(per-row 결과 배열·단일 페이지·200 cap, D-bulk). BE: `bulk_transition_status` 서비스(`FOR UPDATE` 행락 + 정렬 ID로 데드락 방지, 단일 커밋·per-row 매트릭스+role 게이트, before_flush 리스너로 자동 audit) + Pydantic 스키마 + 라우터(envelope 200, 영역 422는 빈/캡초과/미지 enum, cross-team은 envelope 404 existence-hide). FE: `VulnerabilityBulkActionBar` + 행/헤더 체크박스(tri-state indeterminate selectAll, 단일 페이지 cap=200) + `useBulkTransitionVulnerabilities` invalidate (선택 변경 시 자동 클리어), EN/KO 신규 `vulnerabilities.bulk.*` 14키. 게이트: ruff/mypy clean·pytest 164(+20)·vitest 918(+5)·typecheck clean·lint 0 errors·i18n:check OK·openapi snapshot regen 1줄. | ✅ 완료(2026-05-26) |
| **W3 통합/발견성** | #32 | Reports 센터 탭 — **다운로드/익스포트 이력 통합 + 4영역 진입점 deeplink**(생성 UI는 도메인 탭 유지; 2026-05-26 사전 갭 분석으로 좁힘, [[feedback-tracker-text-may-overstate-gaps]]). **BE ✅ `dbd8c31`**: `report_downloads` 테이블(0025, append-only, ENUM `report_type_enum`, FK 4개 CASCADE/SET NULL, 3 compound 인덱스) + `record_report_download`(best-effort emit, 실패 swallow, UA `mask_pii`+512자, XFF-aware IP) + 4 read-only 엔드포인트 emit(NOTICE/SBOM/Vuln-PDF/VEX export; VEX import는 audit_log 자연 흡수) + `GET /v1/projects/{id}/reports/history`(404 existence-hide, OUTER JOIN user, 페이지 1..200 기본 50). 게이트 ruff/mypy(417) clean·신규 unit 21+integration 9·OpenAPI 1엔트리 추가·alembic head 0025. **FE ✅ `689baa4`**: 신규 `Reports` 탭(sbom↔source 사이, `?tab=reports` URL 거울) — 좌 4 generate 카드(NOTICE/SBOM/Vuln-PDF/VEX) `setSearchParams({tab})`로 도메인 탭 deeplink + `?scan=` 보존, 우 이력 테이블(When/Who/Type/Format/Scan/Size + type MultiSelect 필터 + URL state `?rpt_type=`/`?rpt_page=` + Prev/Next 페이저). 404 일반화("Reports unavailable" — existence-hide), 빈 상태·스켈레톤·429 처리. `reportHistoryApi.ts`+`useReportHistory.ts`(`paramsSerializer: {indexes:null}`로 `?type=a&type=b` 직렬화, `keepPreviousData`). i18n EN/KO 미러(plural 미사용). 하네스 3 verb 추가(`selectReportsTab`/`expectReportsTabReady`/`clickReportsGenerateCard`). 게이트 typecheck clean·lint 0 errors·i18n:check OK·vitest 926(+8). Playwright `reports.spec.ts` 1 시나리오(spec만, 실행 후속). **부속(분리)**: Vuln PDF `scan_id` pin 미지원 → #32c 또는 #30과 묶음. | ✅ 완료(2026-05-26) |
| W3 | #30 | 프로젝트 목록 행에 릴리스/스캔 수 표시(발견성). 2026-05-26 사전 갭 분석으로 BE+FE 양쪽 진짜 갭 확인(트래커 본문 정확). **BE ✅ `6255700`**: `project_list_enrichment._scan_counts_map` 신규(단일 GROUP BY로 `scan_count`/`release_count`/`MAX(created_at)` 동시 산출, `ix_scans_project_created_at` 활용·N+1 없음) + `enrich_project_rows` 3-튜플 반환 + `ProjectPublic`에 `scan_count`/`release_count: int = 0`·`last_scan_at: datetime|None = None` 추가(list endpoint에서만 채움, detail은 default). 게이트 ruff/mypy(417) clean·pytest 38(+5)·alembic 0025 유지·OpenAPI 스냅샷 무영향. **FE ✅ `971af25`**: `ProjectListPage.tsx` `<ScanMetadataSummary>` 컴포넌트(severity 뒤·status badge 앞 인라인) — `Rel 12 · Scn 47 · 2h ago` (font-mono text-xs `text-muted-foreground`, abs ISO `title` tooltip, never-scanned 행은 미렌더). `formatRelativeToNow` 헬퍼 재사용. i18n EN/KO 4키(`row.releases_abbrev`·`row.scans_abbrev`·`row.never_scanned`·`row.scan_meta_aria`, plural 미사용). 정렬 정정(`compareByLatestScan` updated_at→last_scan_at)은 scope 밖 후속. 게이트 typecheck clean·lint 0 errors·i18n:check OK·vitest 929(+3). | ✅ 완료(2026-05-26) |

**W4 — UX 정합성 + 정보 구조 재정비 (2026-05-26 사용자 핸즈온 #16~#22)**

| 항목 | 내용 | 상태 |
|---|---|---|
| W4-A #18 | Admin 메뉴 진입 시 일반 메뉴(Dashboard 등) 사라지는 layout 버그 — P0 단독 PR. `/admin/*` 를 `AppShell` 하위로 nest, `AdminLayout`은 super-admin 가드 + `<Outlet/>`로 축소(자체 sidebar/header 제거; AppShell이 admin 섹션 nav 이미 렌더 중). `admin.layout.*` i18n 키 2개 제거(EN/KO). 단위 테스트 갱신(`admin-sidebar`/`admin-nav-*`/`admin-logout` 어서션 삭제, 가드 3 케이스 유지). 게이트 typecheck clean·lint 0 errors·vitest 930·i18n:check OK. **PR #186 머지** `0c77338`. | ✅ 완료(2026-05-26) |
| W4-B-prep | 공통 primitive 3종 사전 분리: `SortableColumnHeader`(3-state cycle, `nextSortState`), `LicenseColumnCell`(SPDX + 정책 badge 스택), 차트 `onSegmentClick` prop(SeverityDistributionChart·LicenseDistributionChart). 본 PR이 import해 소비; 재구현 금지. 게이트 typecheck clean·vitest 통과. **머지** `07a8c9f`. | ✅ 완료(2026-05-26) |
| W4-B #16/#17/#19 | UX 정합성 단일 PR. **#16 Overview**: Risk Score 카드 제거(헤더 RiskGauge 유지) + 차트 segment deep-link(`?tab=vulnerabilities&severity=` / `?tab=licenses&license_category=`) + Recent Scans 행 status별 분기(succeeded/failed/cancelled→Components pin, queued/running→progress drawer). **#17 Components**: License 컬럼 SPDX+badge 분리(`LicenseColumnCell`), Severity/License MultiSelect 제거(차트 deep-link가 대체) + `ActiveFilterChips` 가시화·clear, Sort/Order `<select>` 제거→컬럼 헤더 클릭(unset→asc→desc→unset). **#19 Vulnerabilities**: License 컬럼 분리(SPDX 필드는 BE list schema 미존재로 null 렌더 = follow-up), Severity/License/Sort/Order toolbar 컨트롤 제거, 컬럼 순서 재정렬(CVE/Severity/CVSS/EPSS/Reachability/License/Title/Affected/Status; Discovered 드로어로 이동), Component@Version 컬럼은 BE schema 갭으로 보류(`affected_component_count`만 유지). 신규 i18n `active_filters.*` 6키 EN/KO, 토글 키 제거(EN/KO 동시). 단위테스트 갱신 + 신규(OverviewTab 11, ComponentsTab 16, VulnerabilitiesTab 44). 게이트 typecheck clean·lint 0 errors·vitest 958·i18n:check OK. | 🟡 PR 대기 |
| W4-C #20/#21/#22 | IA 재정비 — Licenses+Obligations→Compliance, SBOM→Reports, Remediation→Vulnerabilities 흡수. 탭 11개 → 8개. **#20**: 신규 `ComplianceTab.tsx`(sub-tab wrapper, `?cview=licenses|obligations`, 기본=licenses) — 기존 `LicensesTab`/`ObligationsTab` 컴포넌트 본문 재사용(드로어·toolbar·virtual list 동일). 단위 테스트 5케이스. **#21**: `ReportsTab.tsx`에 `<SbomTab>` 인라인 section 추가(`reports-sbom-section`, `?rpt_section=sbom` 이펙트로 자동 scroll). SBOM 카드는 deeplink 대신 in-page scroll 핸들로 변경, NOTICE 카드는 `compliance` 타깃. **#22**: 신규 `VulnerabilitiesRemediationPanel.tsx`(collapsible, `?vuln_section=remediation`) — `RemediationTab`을 그대로 임베드, 본체 비대화 회피. 읽기전용 스냅샷에선 미렌더. **PDP**: `ALLOWED_TABS` 8개로 축소, `redirectLegacyTab(raw)` 헬퍼 + `useEffect`로 `?tab={licenses,obligations,sbom,remediation}` → 새 IA URL 자동 rewrite(replace). `setTab` 드롭 로직: `cview`/`rpt_section`/`vuln_section` 정리. **OverviewTab**: 차트 deeplink가 `?tab=compliance&cview=licenses&license_category=…`로 변경. **하네스**: `selectLicensesTab`/`selectObligationsTab`/`selectSbomTab` 3 verb를 새 IA로 리다이렉트(test 호환). **i18n**: EN/KO 동시 — `tabs.compliance` 추가, `tabs.{licenses,obligations,sbom,remediation}` 제거, `compliance.subtab.*` 2키, `reports.sbom.heading/subheading` 2키, `vulnerabilities.remediation.{heading,subheading,expand,collapse}` 4키, `reports.cards.notice.action`/`reports.cards.sbom.action` 카피 갱신. plural 미사용. **게이트**: typecheck clean·lint 0 errors·vitest 967(+9: ComplianceTab 5 + PDP 4 신규)·i18n:check OK. | 🟡 PR 대기 |
| W4-D | TYPE/USAGE 본격 픽스 (cdxgen `dependencies` 누락 / npm scope 미배출, P3 #183 진단 후속). **Option C 채택**: 신규 모듈 `apps/backend/integrations/npm_lockfile.py`가 `package-lock.json`(v3/v2/v1)을 파싱 → `{scope_by_purl, adjacency}` 산출. `_persist_components`가 `source_dir`로 lockfile 1회 로드 후, npm 컴포넌트 한정 scope NULL → lockfile scope 폴백(`required`/`dev`/`optional`/`peer`, strongest-wins). `_persist_dependency_graph`는 cdxgen `dependencies` 빈 배열일 때만 lockfile adjacency를 `parse_dependency_graph` 트러스트 경계 통해 재사용(동일 cycle/dangling/MAX_DEPTH 보장). Maven/Gradle/Cargo/Go/.NET 무영향(cdxgen가 scope/graph 신뢰 emit). 로그: `npm_lockfile_loaded`·`dependency_graph_lockfile_fallback`·`dependency_graph_persisted source={"cdxgen","npm_lockfile_fallback"}`. **단위 53 신규**(npm_lockfile 30, dependency_graph 3, scope enrichment 7, parallel_dt_upload 시그니처 픽스 1). 게이트 ruff·mypy clean·신규 line cov 89%·기존 unit 2673 pass(reachability fluke 1건 사전 존재). 운영 검증=머지 후 orchestrator. **PR 대기** `feat/w4-d-type-usage-fix`. | 🟡 PR 대기 |
| W4-B follow-up: vuln-list affected fields | W4-B #19 의 BE 갭 해소. `VulnerabilityListItem` 응답에 4 신규 필드 추가 — `affected_component_name`·`affected_component_version` (FK-pinned cv 의 식별자, n+1 회피 위해 단일 cv 만), `affected_component_license` (SPDX, worst-rank `DISTINCT ON` argmax), `affected_component_license_category` (새 null-bearing; 기존 non-null `component_license_category` 도 back-compat 으로 유지). FE: `VulnerabilitiesTab` 의 옛 standalone "Affected" 카운트 컬럼을 신규 `ComponentColumnCell` 로 흡수 (`name@version` + `+N-1` 접미사, 카운트 > 1 일 때), `LicenseColumnCell` 에 SPDX 와이어업, 스테일 캐시 row 가 새 필드 미보유 시 legacy category 로 fallback. 새 i18n 키 EN/KO 동시: `vulnerabilities.column.component`·`affected_more_count`·`affected_more_count_tooltip`. 단위 5 신규(BE: name/version, SPDX happy, worst-rank pick, LicenseRef-* null SPDX, multi-cv count + per-row pinned cv) + 5 신규(FE: Component@Version 렌더, `+N-1` 표시, null fallback dash, SPDX wireup, legacy category fallback). 게이트 ruff·mypy clean·BE unit 121 pass·FE typecheck clean·lint 0 errors·vitest 972·i18n:check OK·OpenAPI 스냅샷 갱신. **PR 대기** `feat/vuln-list-affected-fields`. | 🟡 PR 대기 |

→ 상세 계획·갭 분석·출발 파일은 **`docs/plan-w4-ui-ia-overhaul.md`** 단일 진실. 진행 전 반드시 참조.

---

**W5 — DT 운영 견고화 (2026-05-27 사용자 핸즈온 발견) — ⛔ W6로 무효화**

> 출처: 2026-05-27 사용자 핸즈온 — DT 내장 H2 MVStore 손상 사고(`org.h2.mvstore.MVStoreException: Chunk 113620 not found`)로 DT 미부팅 → circuit breaker가 포털 다운은 막았으나 ① 새 스캔이 approvals 직후 즉시 fail ② DT 데이터 복구 경로 없음 ③ 새 DT 인스턴스 부트스트랩이 수동(OSV 활성화) ④ 운영 compose도 embedded H2라 동일 사고 재발 가능 — 네 갭이 동시 발견됨.
>
> **⛔ 2026-05-27 결정**: W6(Trivy 단일 교체)로 DT를 통째로 제거하기로 결정 → W5 4개 항목 모두 **무효화(superseded by W6)**. DT 코드/인프라가 사라지므로 별도 견고화 PR 불필요. [[project_dt_removal_decision]] 참조.

| 항목 | 내용 | 상태 |
|---|---|---|
| ~~W5-#36~~ | ~~DT 데이터 정기 백업 + 복구 자동화~~ | ⛔ W6로 무효화 |
| ~~W5-#37~~ | ~~DT 다운 중 도착한 스캔 자동 재시도 큐~~ | ⛔ W6로 무효화 |
| ~~W5-#38~~ | ~~DT 부트스트랩 완전 자동화 (OSV 8 ecosystem 활성화)~~ | ⛔ W6로 무효화 |
| ~~W5-#39~~ | ~~운영 compose DT를 외부 PostgreSQL 모드로 분리~~ | ⛔ W6로 무효화 |

---

**W6 — DT 제거 + Trivy 단일 교체 (2026-05-27 결정, 계획 v2)**

> 출처: W5 사고 분석 + Explore 면밀 평가(2026-05-27). 우리 코드의 DT 활용도가 표면적인 것보다 훨씬 낮음 — 정책엔진/VEX/UI/승인은 모두 자체 구현, DT는 사실상 "CycloneDX → CVE 매칭" 단일 기능. DT의 킬러 기능(새 NVD → 기존 SBOM 자동 재매칭)은 `dt_resync.py`가 카탈로그만 폴링하고 실제 재분석은 안 트리거해 **현재 미활용**. 그 한 기능 위해 4GB JVM + embedded H2 손상 위험 + 부트스트랩 수동 + 백업 갭을 떠안고 있음. SBOM 생성기(cdxgen)는 검토 후 **유지** — Trivy fs로 통합 시 dependency graph·scope·evidence 손실, W4-D 자산 폐기 발생.
>
> **결정 근거**: Trivy는 (a) 이미 worker 이미지에 설치 (b) `trivy sbom` 명령으로 CycloneDX 직접 입력 (c) NVD+OSV+GHSA+EPSS+KEV 통합 (d) ~500MB(vs DT 4GB) → Apple Silicon Colima 4CPU/8GB dev 안정성 큰 폭 상승 (e) 우리 `integrations/trivy.py` 패턴과 동일.
>
> **현실적 일정**: **코드 8~10일 + 캘린더 7일 shadow** = 약 2~2.5주 마일스톤. 트래커 초안의 "3~5일"은 fix-first/벤치 튜닝/마이그레이션 검증을 누락한 과소 추정이었음 → 정정.

| 항목 | 내용 | 상태 |
|---|---|---|
| ~~W6-prereq~~ | ~~DT 시대 동결 marker (v2.3.1 annotated tag + GitHub Release + `:v2.3-dt` 별칭 + SECURITY.md backport 정책 + 운영 레인 O1·O3 통합 수행)~~ | ⛔ **스킵 (2026-05-27 후속 결정)** — repo private 전환 + 외부 사용자 0이라 "Final DT Release" 공개 마커 무의미. v2.4.0이 DT-free 첫 공개 릴리스가 됨. O1/O3은 v2.4.0 GA에 통합. ADR-0001 amendment 절 참조. |
| W6-#45 | **ADR + 전략 문서 동기화 (선행)**. `docs/decisions/0001-replace-dt-with-trivy.md` 신규(Context/Decision/Consequences, 메모리 본문 기반, cdxgen 유지 부록). `CLAUDE.md` 규칙 4(DT Circuit Breaker) 제거·"DT 연동 전략" 절 → "취약점 매칭(Trivy)"로 재작성·디렉토리/환경변수/에이전트 설명 11곳 갱신. `docs/post-ga-roadmap.md` v2.1 회고 + v2.4 W6 절 신설 + "비범위: 자체 vuln DB" 정정 14곳. 검증: docusaurus build · `grep -nE "Dependency.?Track\|DT " CLAUDE.md docs/post-ga-roadmap.md` = 0. **dep: 없음**. | ✅ 완료 (PR #192, 2026-05-27, commit f68d688) |
| W6-#40 | **Trivy SBOM 통합 어댑터**. `integrations/trivy.py`에 `run_trivy_sbom(sbom_path, output_dir) -> TrivyResult` 추가(`trivy sbom --format json --output ...` subprocess, 기존 `run_trivy_image`와 동일 에러 처리·timeout·SecretEncryptionError 패턴). cdxgen 산출 CycloneDX JSON 입력 → JSON 결과 산출. 단위 cov ≥85%. **adversarial parametrize**(untrusted Trivy JSON: 비정상 severity·중첩 깊이·인코딩 깨짐·oversized) 필수 [[feedback-adversarial-input-parametrize]]. **dep: W6-#45**. | ✅ 완료 (PR #196, 2026-05-27, commit b6ff071). 36 tests · mypy/ruff green · cov 83%. security-reviewer M1+L3 fix 포함 (`scrubbed_env_for_trivy` + `--scanners vuln`). L1·L2·L4 chore PR #199(commit 7a4117e) — 14 tests · cov 94% · `_ensure_inside_workspace` + 256MB cap + `safe_detail` redaction. |
| W6-#41 | **Trivy 결과 → `vulnerability_findings` persist + 정보용 벤치 측정** (ADR-0001 Amendment 2 — shadow gate 폐기). `_persist_findings` 확장 또는 신규 `services/vulnerability_matching.py`에 Trivy JSON parser 추가 — `external_id`/`severity`/`cvss_score`/`epss_score`/`fixed_version`/`reference_urls`/`summary` 매핑(현 DT finding shape와 동등). `scan_source.py:535`의 `dt_findings` 스테이지를 `_poll_trivy_findings`로 교체(**stage 이름 유지** — WS frame/E2E harness 호환, rename은 #43f). 멱등 upsert. **shadow 분기 없음**: DT 호출 제거하고 Trivy만 호출 (회복 비용 0 — git revert로 분 단위). **벤치 스크립트** `scripts/benchmark_dt_vs_trivy.py` — ADR-0002의 6개 cohort SBOM에 DT/Trivy 양쪽 호출 → 차이의 종류(DT-only/Trivy-only/version-mismatch) 분류 보고서. **게이트 아님 — 정보용**(미래 개선 백로그 인풋). **종료: persister e2e green + 벤치 보고서 1회 산출**. **dep: W6-#40**. **rev: security-reviewer**(untrusted JSON 파싱). | ✅ 완료 (PR #200, 2026-05-27, commit a2f4a07). 65 unit + 3 integration tests · `vulnerability_matching` cov 89% · mypy/ruff green · net diff −2499. Vulnerability autocreate(source='trivy') 추가. `integrations/dt/` 모듈 보존(#43a까지). 벤치 manual 1회 실행은 별도(DT API key + trivy + 3시간 소요). |
| ~~W6-shadow~~ | ~~7일 shadow 운영 게이트~~ | ⛔ **스킵 (2026-05-27 후속 결정 #3, ADR-0001 Amendment 2)** — Jaccard metric이 잘못된 측정 + DT가 절대 기준 아님 + 회복 비용 0 (외부 사용자 0 + repo private). |
| W6-#42 | **자동 재매칭 — Celery beat 주기 재스캔**. DT가 못 하던(우리 코드 기준) "새 NVD 발견 시 기존 SBOM 재매칭"을 신규 기능으로 구현. 신규 task `tasks/vulnerability_rematch.py` — N시간(기본 6h)마다 succeeded 스캔들의 보존 SBOM을 `run_trivy_sbom`으로 재실행 → 새 finding만 추가/severity 변경 감지 → 알림 발행(기존 notification 인프라 재사용). Beat 시간대 분산 설계(다른 beat와 worker 큐 충돌 방지). 종료: 어제 깨끗했던 스캔이 새 NVD 데이터로 finding 추가되는 시나리오 e2e green. **dep: W6-#41 (#43a 전에 머지)**. | ✅ 완료 (PR #203, 2026-05-27, commit 03639d0). **scope 확장**: source_preservation_service에 SBOM 폴드(`.trustedoss/cdxgen.cdx.json`)+`extract_preserved_sbom`/`preserved_tarball_has_sbom`/`PreservedSbomMissing` 추가(휘발 workspace 문제 해결). 마이그 0026 + 부분 인덱스. 3 env(`VULN_REMATCH_INTERVAL_HOURS/_BATCH_SIZE/_LOCK_SKEW_SECONDS`). Beat `crontab(minute=15, hour="*/6")`. **테스트**: 33 신규 단위 + 3 통합, 54 green, ruff/mypy clean. **security-reviewer (a276e00c4b6f944a0)** Verdict CHANGES REQUESTED → **본 PR fix 5건**: H-1(tarfile symlink 우회 strict isreg)·H-2(silent zero-yield 가드 — prior>0+malformed report 거부)·M-1(email 채널 drop, Slack/Teams만)·M-3(per-scan cap 10 + summary descriptor)·L-3(temp dir WORKSPACE_HOST_PATH/rematch/ 이동). **후속 4건 → W6-chore-#42-followup** (M-2/M-4/L-1/L-2). |
| W6-#43a | **백엔드 DT 제거 (비가역, #41+#42 머지 후)**. `apps/backend/integrations/dt/` 전체 삭제(`__init__`·`breaker`·`client`·`health`), `tasks/dt_{resync,orphan_cleaner,orphan_cleanup,health}.py` 4개 삭제, `api/v1/admin/dt.py` + admin service 삭제, `core/config.py:434-465` 8 getter 제거, `tasks/celery_app.py:39-81` import + beat schedule 정리, `scan_source.py`에서 `build_client`/`breaker`/`dt_executor`/`_dt_upload`/`_poll_dt_findings_with_retry` 잔재 제거(stage 이름은 #43f까지 유지). `.env.example` DT 섹션 제거. `docker-compose.dt.yml` → #43d로 이관(release notes와 함께 발표). 백엔드 DT 테스트 8개 삭제. `audit_log` 의 DT 액션 타입은 **보존**(역사 사실). **종료**: `grep -rIn "from .*dt\|DT_" apps/backend/ \| grep -v trivy \| grep -v audit_log` = 0. **회복**: 회귀 발생 시 본 PR git revert (외부 사용자 0). **dep: W6-#41 + W6-#42**. **rev: security-reviewer**(권한 우회/잔여 endpoint 검증). | ✅ 완료 (PR #205, 2026-05-27, commit 189b2ff). 11 source 파일 + 4 test 디렉토리/파일 삭제 + 7 코멘트/테스트/스키마 수정. mypy 424→**405** source files. **security-reviewer (a3cf57b0051879c0c)** Verdict CHANGES REQUESTED → **본 PR fix 5건**: H-1(HealthComponent.name Literal "dt" 제거 + openapi regen)·H-2(upgrade.sh `celery purge --task-names=trustedoss.dt_*` 추가, in-flight 메시지 NACK loop 차단)·M-1(죽은 conftest fixtures 제거 — fakeredis_client/make_breaker/make_dt_client zero consumers + services/conftest.py 전체 삭제)·M-2(integrations/__init__.py docstring dt/ 제거)·M-3(main.py FastAPI description 정리). **후속 7건 → W6-chore-#43a-doc-drift** (L-4/5/6/7 + Info-1: docstring 정리). |
| W6-#43b | **프론트엔드 DT 제거**. `features/admin/dt/` 디렉토리 삭제(`AdminDTPage`·`adminDTApi`·`useAdminDT`). `router.tsx` `/admin/dt` 라우트 제거 → 404 처리. `AppShell.tsx` admin nav DT 항목 제거. EN/KO 번역 키 정리 + `npm run i18n:check` 통과. Playwright `admin/dt` 시나리오 → 404 redirect 시나리오로 교체. visual-regression DT 스크린샷 baseline 삭제. **dep: W6-#43a**. | ⏳ 백로그 |
| W6-#43c | **사용자/관리자 문서 교체 (EN/KO 동시, 1.5d)**. 신규 `docs-site/docs/admin-guide/vulnerability-data.md` (Trivy DB 운영 가이드: 동기화·**air-gapped 운영 매뉴얼**·트러블슈팅). **신규 `docs-site/docs/reference/data-sources.md`** (출처 reference: NVD/OSV/GHSA/EPSS/KEV — 갱신 주기·커버리지 매트릭스. DT 문서 비교 갭 분석 결과 추가, 운영/reference 분리는 DT IA 정합). `admin-guide/dt-connector.md` 삭제 + sidebars.ts 정리. 핵심 4개 교체: `reference/{architecture,env-variables}.md`·`installation/{docker-compose,helm}.md`. 멘션 9개 정리: `intro`·`comparison`·`user-guide/{scans,vulnerabilities}`·`admin-guide/{disk-and-health,oncall-runbook}`·`ci-integration/github-actions`·`reference/glossary`·`contributor-guide/agent-team`. `release-notes/v2.0.0.md`는 **보존**(역사). 신규 `release-notes/v2.4.0.md` 초안(Breaking changes + Migration). 검증: docusaurus build EN/KO green · 깨진 링크 0 · i18n:check OK. **dep: W6-#43b**. **owner: doc-writer + i18n-specialist**. | ⏳ 백로그 |
| W6-#43d | **배포·Helm·운영자 마이그레이션**. `scripts/install.sh:400` DT_API_KEY 안내 제거 + Trivy DB 안내 추가. `scripts/upgrade.sh` 신규 v2.3→v2.4 절: ① **Celery 큐 drain 대기**(인플라이트 스캔 보호) ② `dtrack-api` 컨테이너 정리(`docker rm`) ③ DT 볼륨 archive 안내(데이터 삭제는 사용자 confirm) ④ `.env` DT_* 자동 주석처리 ⑤ **부팅 후 1-click "전체 재매칭" admin UI 트리거 + 진행 표시**. `docker-compose.yml` DT 코멘트 6곳 제거. `docker-compose.dt.yml` 삭제. `charts/trustedoss/` 0.2.x → **0.3.0**: values·configmap-env·secret·deployment-beat·_helpers·README 6개 정리. `release-notes/v2.4.0.md` 최종화 + GitHub Release body draft. 검증: helm lint/template green · 시뮬레이션 upgrade(seed_demo v2.3.1→v2.4.0) 다운타임 ≤5분·데이터 유실 0. **dep: W6-#43c**. **owner: devops-engineer**. | ⏳ 백로그 |
| W6-#43e | **admin/health Trivy DB 상태 패널 신설**. DT 패널이 사라진 자리에 Trivy DB 상태(last update timestamp·vuln count·DB version·다음 refresh 예정) 노출. BE: 신규 `GET /v1/admin/trivy/health`. FE: admin/health에 `TrivyDBPanel` 추가. EN/KO. **dep: W6-#43b (FE) + W6-#44 (DB 라이프사이클 데이터 소스)**. | ⏳ 백로그 |
| **W6-#44** | **Trivy DB 라이프사이클 관리 (필수로 승격)**. worker 부팅 시 `trivy --download-db-only` 1회 + weekly 주기 refresh(beat). **air-gapped 사용자용 `TRIVY_DB_REPOSITORY` 미러 설정 + 별도 PV 마운트 매뉴얼**(#43c air-gapped 절과 정합). `trivy image`(컨테이너 스캔)와 `trivy sbom`(SCA 매칭)이 동일 DB 공유 → 동시성 lock 테스트. 종료: 오프라인 부팅 시에도 마지막 캐시로 매칭 가능 + 동시 호출 안전. **dep: W6-#40 (Trivy 어댑터 패턴 재사용)**. | ⏳ 백로그 |
| W6-#43f (선택) | **stage rename — 장기 부채 청산**. `scan_source.py` stage 키 `dt_upload`/`dt_findings` → `sbom_upload`/`vuln_match`. WS frame · E2E harness · 진행 표시 i18n 동시 갱신. **별도 minor 릴리스(v2.4.1)에서**. 단기엔 가독성 부채 감수. **dep: W6-#43a 머지 후 임의 시점**. | ⏳ 백로그 |
| W6-chore-#43a-doc-drift | **W6-#43a security-reviewer 후속 (Low/Info 7건)**. 본 PR에서 H-1(HealthComponent.name Literal)·H-2(upgrade.sh queue purge)·M-1(죽은 conftest fixtures)·M-2(integrations/__init__.py docstring)·M-3(main.py FastAPI description) fix 완료. 후속 doc-drift 7건: (L-4) test_admin_health_service.py docstring + "seven_components" 함수명, (L-5) tasks/{backup,notify,source_archive_cleaner,scan_reachability}.py `:mod:` cross-ref, (L-6) core/config.py:465/831 "DT polling" 멘션, (L-7) tasks/scan_container.py "DT may cross-reference" 반사실, (Info-1) models/scan.py + schemas/{project_detail,vulnerability_detail}.py "from DT" provenance 멘션. **dep: W6-#43a 머지 후, #43e 전에 처리 권고**. **예상**: 0.5d. | ⏳ 백로그 |
| W6-chore-#42-followup | **W6-#42 security-reviewer 후속 (M-2/M-4/L-1/L-2)**. 본 PR에서 H-1/H-2/M-1/M-3/L-3는 처리됨; M-2(scan_artifact 분리 kind/컬럼으로 SBOM-present flag DB promote — O(N) tarball open 제거), M-4(`scan.rematch_failure_count` 컬럼 + worker hard-kill poison-pill 가드), L-1(diff key `(external_id, cv_id)` → `(vulnerability_id, cv_id)` 변경, 알림 빌드 시점에서 external_id resolve), L-2(critical→unknown silent downgrade 가시화 — admin-only NotificationKind 또는 metrics counter). 각 작업이 마이그/정책 결정 필요. **dep: W6-#42 머지 후, #43a 후나 v2.4.1 어디든 가능**. **예상**: 1d (각 4 항목 0.25d). | ⏳ 백로그 |
| W6-chore-seed | **데모 시드 fragility 청산 (W6-#42 후 chore)**. 현 `scripts/seed_demo.py`는 `DEMO_SUPER_ADMIN_PASSWORD` env 없으면 random 12자 생성 후 stdout에 1회만 출력 → 분실 시 데모 계정 영구 로그인 불가, 재시드 반복 사이클. 본 세션에서도 발생. 작업: ① **A** — `seed_demo`가 `APP_ENV=dev\|demo`이면 env 없을 때 강제 디폴트 `DemoTest2026!` 적용. ② **B** — `.env` 자동 sync 헬퍼 — `install.sh`/`upgrade.sh`/`dev-reset.sh`가 `.env.example` 신규 키를 사용자 `.env`에 append-only 동기화(overwrite X). ③ **C(보조)** — `seed_demo`가 idempotent password 재계산. | ✅ 완료 (PR #204, 2026-05-27). A+B 동시 머지: `scripts/lib/env_sync.sh` 공통 헬퍼 + 6 bash 테스트(9 asserts) + seed_demo random → `DemoTest2026!` 디폴트 + 5 신규 pytest. shellcheck/ruff/mypy clean. C는 본 PR scope 외(idempotency는 hash_password 호출 자체로 충족). |

→ **현실적 일정**: prereq 0.5d + #45 0.5d + #40 1d + #41 1.5d + (shadow 캘린더 7d 병렬) + #42 1d + #43a 0.5d + #43b 0.5d + #43c 1.5d + #43d 0.5d + #43e 0.5d + #44 0.5d = **코드 8.5d + 캘린더 7d shadow** ≈ **2~2.5주 마일스톤**. 종료 시 W5 4개 항목 자동 해소.

**롤백 비가역 지점**: shadow 게이트 통과 후 #43a 머지 순간부터 비가역. 그 전엔 Trivy 결과만 비교용으로 유지 → DT 코드 그대로라 언제든 회귀 가능.

**사용자 확인 필요**: (1) 벤치 코호트 GitHub repos 5~10개 제공 가능 여부 — 없으면 OSS 인기 repo로 자체 구성 (2) air-gapped 사용자 존재 여부 — 있으면 #44 필수 강도 상향, 없으면 후순위 (3) shadow 7일 캘린더 수용.

---

**W7 — Docs parity wave (백로그, W6 종료 후 착수 검토)**

> 출처: 2026-05-27 DT 공식 문서(docs.dependencytrack.org) 구조 비교(12 카테고리 vs 우리 7 카테고리). 우위(거버넌스/기여자/다국어/comparison)는 유지하되, 명백한 갭 5종을 별도 트랙으로 좁힘. **W6 완료 후 실제 docs 상태 보고 우선순위·범위 재조정**([[feedback_tracker_text_may_overstate_gaps]] 방지).

| 항목 | 내용 | 상태 |
|---|---|---|
| W7-PR-A | **Triage Results 통합 가이드**. 신규 `user-guide/triage.md` — VEX 7-state + 컴포넌트 승인 워크플로우 + 빌드 게이트 한 페이지에 통합(현재 `vulnerabilities.md`·`approvals.md` 분산). DT의 Triage Results 카테고리 격. EN/KO. | ⏳ 백로그 |
| W7-PR-B | **Analysis Types 단일 페이지**. 신규 `reference/analysis-types.md` — source SBOM scan(cdxgen→Trivy) · container scan(Trivy) · reachability(govulncheck) · policy gate를 한 매트릭스. DT의 Analysis Types 격. 도입 사용자 1차 진입점. EN/KO. | ⏳ 백로그 |
| W7-PR-C | **Best Practices 카테고리 신설**. 신규 `best-practices/{scan-frequency,policy-design,team-structure,upgrade-cadence}.md` 4페이지 + sidebars 카테고리 추가. 운영자 첫 의사결정 가이드(현 `oncall-runbook.md`이 부분 대체 중). EN/KO. | ⏳ 백로그 |
| W7-PR-D | **FAQ 신설**. 신규 `reference/faq.md` (또는 별도 top-level) — 30~50개 첫 도입 질문. GitHub Discussions에서 자주 받는 패턴 우선. 사용자 도입 1차 검색 통로. EN/KO. | ⏳ 백로그 |
| W7-PR-E | **Change Log 누적 보강**. 신규 `release-notes/v2.{1,2,3}.0.md` 소급 작성(코드는 머지 완료, 문서만 누락). v2.4.0과 같은 양식. EN/KO. | ⏳ 백로그 |
| W7-PR-F (조사 후 판단) | **DefectDojo/ThreadFix 통합 가이드** — DT 우위 영역, SCA 생태계 통합. 조사 후 의도·범위 확정 → 등재 또는 보류 결정. 지금은 라벨만 보존([[feedback_tracker_clear_intent_only]] — 의도 명확화 전엔 미등재). | ⏳ 조사 대기 |

→ 작업량 추정 **PR-A~E 합쳐 3~4d** (각 0.5~1d). PR-F는 별도 판단. W6 종료 후 실제 docs 상태 본 뒤 진짜 갭만 PR scope로 ([[feedback_tracker_text_may_overstate_gaps]]).

---

**#29 구현 요지(완료):** `services/project_detail_service.py::get_project_overview` — `recent_stmt`를 `if aggregate_scan_id is not None` 블록 밖으로 빼 **성공 스냅샷 없어도(첫 스캔 queued/running) recent_scans를 항상 조회**. 분포 집계만 스냅샷에 의존. 프론트 `ProjectDetailPage` 헤더에 queued/running 스캔용 영속 칩(`project-detail-active-scan`) 추가 → 클릭 시 진행 드로어 재오픈. 가드: `test_latest_succeeded_scan_anchoring.py`(running-only overview), `tests/unit/ProjectDetailPage.test.tsx`(칩 3케이스).

---

## 1. 흐지부지 방지 메커니즘 (Anti-Fizzle Backbone)

> 이 마일스톤이 "중간 이후 흐지부지"되는 전형적 원인과 그 차단 장치. **이 절이 본 계획의 핵심이다.**

| # | 실패 원인 | 차단 장치 |
|---|---|---|
| 1 | "지금 어디였지?" 컨텍스트 유실 | **이 트래커가 유일 SoT.** 매 PR 머지 시 체크박스+한 줄 갱신. 세션 종료 시 `docs/sessions/` 핸드오프. |
| 2 | 거대 PR이 리뷰에서 정체 | **PR 단위 분해.** 각 PR은 독립 머지 가능 + green (CLAUDE.md 규칙 6 "미완성 WIP 없음"). |
| 3 | "나중에 할 일" 백로그 누적 | **PR마다 완성도 동시** (규칙 7): lint+typecheck+test+coverage≥80%+EN/KO+Docusaurus 동시. DoD 미충족 = 미머지. |
| 4 | 진행 중 의존성 뒤늦게 발견 | **각 PR에 의존(`dep:`) 명시.** 선행 PR 미완 시 착수 금지. §6 의존성 그래프 참조. |
| 5 | 외부 블로커가 코드까지 정지시킴 | **운영 레인 분리**(§5). 이미지 게시·클라우드 배포 등 사용자 작업은 코드 트랙과 비동기. |
| 6 | 보안 결함이 머지 후 발견 | **Producer-Reviewer 게이트.** untrusted-input·외부쓰기·서명 키 PR은 `security-reviewer` 통과 후 머지. |
| 7 | 가정 오류로 헛작업 (Explore 오탐 전력 있음) | **착수 전 실코드 검증.** frontend-dev/backend가 심볼 존재 확인 후 구현. → [[feedback-explore-gap-false-positives]] |
| 8 | 초반 의욕 소진 | **각 트랙은 최소·저위험·독립가치 PR로 시작**(VEX export A1 / `/health/ready` B1)해 조기 승리로 모멘텀 확보. |
| 9 | main이 조용히 red로 누적 | **자율 머지여도 main CI(integration/brittle) 확인.** → [[feedback-autonomous-merge-ci-check]] |

**머지 1건 = 완결된 거래(transaction):** 머지 → 트래커 갱신 → (트랙 마지막이면) 마일스톤 종료조건 점검 → 다음 PR. 이 사이클을 깨지 않는다.

---

## 2. 실행 모델 (확정)

- **v2.1 = 병렬 2트랙.** Track A(VEX, `backend-developer`+`security-reviewer`)와 Track B(배포/평가, `devops-engineer`+`doc-writer`+`backend-developer`)는 담당·파일 영역이 겹치지 않아 병렬. (A: `services/vulnerability_service.py`·`api/v1/vulnerabilities.py`·VEX UI / B: `charts/`·`docker-compose*.yml`·`docs-site/`·`terraform/`·`core/`health.)
- **v2.2, v2.3 = 순차.** v2.1 종료조건 충족 후 v2.2, v2.2 후 v2.3.
- **데모 호스팅 = 기존 GCP terraform 재사용** (이미 Cloud Run scale-to-0 + Cloud SQL + Redis 구축됨, idle ~$46/mo). 신규 IaC 불필요 — 일일 리셋 + read-only 모드만 추가.

---

## 3. 검증된 현재 상태 (2026-05-24, 실코드 기준)

> 로드맵의 "미구현" 주장을 실코드로 재확인하며 발견한 **정정 사항 포함**. 계획은 이 사실 위에 선다.

**v2.1**
- EPSS ✅ 완전 구현 — 모범 템플릿: `alembic/versions/0015_vulnerability_epss.py`, `tasks/dt_resync.py`(`_coerce_epss`), `services/vulnerability_service.py`(sort=`epss`/`min_epss`), `services/policy_gate.py`(`GATE_EPSS_THRESHOLD`), 프론트 `features/projects/lib/epss.ts`+`VulnerabilitiesTab.tsx`, 테스트 양쪽. **신규 컬럼 추가 패턴은 이걸 그대로 따른다.**
- VEX: **⚠️ 정정** — `vulnerabilities.py:221`은 export가 **아니라** status 전이 PATCH다. 실제로는 (a) 내부 finding별 VEX status(7-state enum `models/scan.py:88-96`, 전이행렬 `vulnerability_service.py:148-158`, `analysis_state`가 CycloneDX `analysis.state` 미러 `vulnerability_service.py:856-857`, 감사로그 자동)만 있고, (b) **VEX 문서 export 없음**, (c) **VEX 문서 import(소비) 없음**. → A 트랙은 export+import **둘 다** 만들어야 왕복 테스트 성립.
- `vulnerability_findings` 컬럼: `status`/`analysis_state`/`analysis_justification`/`analysis_response`(JSONB)/`analyst_user_id`/`analyzed_at` 보유. `fixed_version`·VEX 출처 컬럼 없음.
- 감사로그: SQLAlchemy `before_flush` 리스너 자동(`core/audit.py`) + Celery용 명시 `_emit_audit`. 민감/PII 마스킹 내장.

**배포/평가**
- Helm `charts/trustedoss` 0.1.0: backend/worker/beat Deployment·configmap·service·SA·HPA만. **Ingress/TLS·postgres·redis·frontend·migration Job 없음.** 이미지 게시는 `release.yml`(멀티아치 ghcr)로 **이미 됨**.
- compose: `profiles:` 미사용(`-f` 오버레이 관례). **eval 경량 프로파일 없음.** DT 4GB 힙은 `docker-compose.dt.yml`.
- health: `/health`(liveness, `main.py:207`)·`/v1/admin/health`(super admin 전체)만. **`/health/ready` 없음.**
- AUTO_MIGRATE: `docker-entrypoint.sh` + `alembic/env.py`의 `pg_advisory_xact_lock`(키 `TOSSMIGR`). owner/app 역할 분리(`DATABASE_URL_OWNER`/`_APP`).
- seed: `scripts/seed_demo.py`(멱등, APP_ENV=dev/demo 가드, org/3팀/5유저/5프로젝트/10 CVE). **1커맨드 평가 시드·일일 리셋·read-only 데모모드 없음.**

**v2.2/v2.3**
- `fixed_version`: **고스트 필드** — 스키마엔 있으나 서비스가 항상 `None` 반환(`vulnerability_service.py`). 채우는 로직 없음.
- 의존성 그래프: `ScanComponent.direct`(bool)·`dependency_path`·`dependency_scope`·`raw_data`만. **depth/`dependsOn` 그래프 없음.**
- GitHub: webhook 수신(`api/v1/webhooks/github.py`) + PR 코멘트 쓰기(`services/sca_comment.py`, 토큰은 env `GITHUB_TOKEN`만, DB 미저장). **GitHub App·PR 자동생성 없음.**
- 라이선스 정책: 정적 카탈로그(`tasks/scan_source.py:1637-1673`) + SPDX expression 평가(`_classify_license_category`). **per-team/org 정책 모델·편집 UI 없음.**
- Policy Gate: 완전 구현(critical CVE·forbidden 라이선스·EPSS 3조건, `services/policy_gate.py`).
- SBOM: 생성/export 완전(cyclonedx json·xml / spdx json·tv, `api/v1/sbom.py`·`services/sbom_export.py`·`ScanArtifact`). **cosign/in-toto/SLSA 서명 전무.**
- Reachability(취약점 도달성): **전무**(govulncheck/call-graph 없음).

**문서/데모 인프라**
- Docusaurus v3.6.3, OpenAPI 플러그인 **없음**, `reference/api-overview.md`는 수기. **OpenAPI drift 게이트는 있음**(`tests/unit/test_openapi_contract.py`). 정적 스펙 export 스크립트 없음.
- terraform GCP IaC **완비**(Cloud Run scale-to-0 + Cloud SQL 17 + Memorystore). **일일 리셋·read-only 데모모드 없음.**

---

## 4. PR 단위 작업 분해

> 표기: `dep:` 선행 PR · `rev:` security-reviewer 필수 · `owner:` 주담당 에이전트.
> 모든 PR 공통 DoD(반복 생략): lint+typecheck+test green · 신규코드 line coverage ≥80% · 핵심 시나리오 Playwright green · EN/KO 동시 · Docusaurus 동시 · Alembic forward-only(expand→contract).

### v2.1 — Track A: VEX 소비 (트리아지 신뢰성)

- [x] **A1 — VEX 문서 export** ✅ #145 (머지 `2c959ca`) `owner: backend-developer`
  - `GET /v1/projects/{id}/vex?format=openvex|cyclonedx` — 현재 finding status로부터 VEX 문서 생성.
  - 내부 7-state → VEX 상태 매핑표 확정·문서화 (OpenVEX: `not_affected`/`affected`/`fixed`/`under_investigation` · CycloneDX `analysis.state`: `resolved`/`exploitable`/`in_triage`/`false_positive`/`not_affected`). justification 보존.
  - 바이트 안정성(byte-stability) 테스트 — SBOM 선례 [[project-qa-followup-marathon]] BUG-006 동일 기법(정렬·canonical JSON).
  - **저위험·읽기전용 → 트랙 모멘텀 시작점.** 모델 변경 없음.
- [x] **A2 — VEX 문서 import (소비)** ✅ #148 (머지 `a6ea314`) — security-reviewer Critical/High 0, Medium+Low2 fix-first 후 회귀 PASS. 마이그레이션 0016(provenance). `dep: A1` · `rev: ✅` `owner: backend-developer → security-reviewer`
  - `POST /v1/projects/{id}/vex:import` (OpenVEX/CycloneDX VEX 업로드) → 문장을 finding(vuln id + purl/component)에 매칭 → `not_affected`/`suppressed`/`fixed` 자동 전이 + justification·출처 보존.
  - 기존 `STATUS_TRANSITIONS` + 권한 게이팅 준수(`suppressed`는 team_admin↑). import는 team_admin 게이팅 액션으로 결정.
  - 출처 추적 컬럼(`vex_source`/`analysis_source`) 추가 — `db-designer` expand 마이그레이션(EPSS 0015 패턴).
  - 멱등성: 동일 VEX 재import = no-op (EPSS upsert 패턴).
  - **adversarial 입력 테스트 필수**(untrusted-input 규칙 [[feedback-adversarial-input-parametrize]]): 깨진 VEX, 충돌 문장, 미지 vuln/purl, oversized, justification 인젝션, 중복 문장, enum 외 상태.
  - **왕복 일관성 테스트**: export→import→export 안정.
- [x] **A3 — VEX 소비 UI + 필터 + i18n** ✅ #150 (머지 `1a796fb`) — import 다이얼로그·export 메뉴·"VEX 억제" 필터·드로어 출처 배지. XSS-inert(React 기본 이스케이프, `dangerouslySetInnerHTML` 0건) 테스트 포함. **Track A 완결.** `dep: A2` `owner: frontend-dev → i18n-specialist`
  - `VulnerabilitiesTab.tsx`(EPSS 컬럼 위치): VEX import 버튼(team_admin), "VEX로 억제됨" 필터, 드로어에 출처 배지.
  - EN/KO 키 + `npm run i18n:check` 통과(복수형 금지 [[feedback-frontend-i18n-no-plural-check]]).
  - E2E: VEX 업로드 → finding suppressed → 필터 노출.
  - **종료조건:** 외부 VEX로 노이즈 억제 가능 · 왕복 안정 · adversarial green · security-reviewer green.
  - **보안 후속(A2 리뷰 Info 핸드오프):** `analysis_justification`·`vex_origin.*`(author/id 등)는 의도적으로 미이스케이프 저장됨 → A3 UI는 반드시 React 기본 텍스트 이스케이프로 렌더(`dangerouslySetInnerHTML` 금지) + `<script>` justification이 inert하게 렌더되는지 테스트 추가.

### v2.1 — Track B: 평가·배포 경로

- [x] **B1 — `/health/ready`** ✅ #146 (머지 `aefc610`) `owner: backend-developer`
  - `GET /health/ready`: alembic 마이그레이션 HEAD vs `alembic_version` 비교 → at-head면 200, 아니면 503. (AUTO_MIGRATE follow-up, 로드맵 §3 security-reviewer M2.)
  - compose/Helm의 worker·beat `depends_on` 게이트를 `service_healthy`(liveness) → readiness 기준으로 전환.
  - 테스트: head보다 뒤처지면 503. **저위험·독립 → 트랙 시작점, Helm migration Job 설계 선행.**
- [x] **B2 — 평가용 경량 compose 프로파일 + 1커맨드 시드** ✅ #147 (머지 `c745fbc`) — `docker-compose.eval.yml`(2vCPU/4GB, DT-less) + `scripts/eval-up.sh`. `dep: B1` `owner: devops-engineer + backend-developer`
  - `docker-compose.eval.yml` 오버레이(관례상 `-f`): DT 외부연결 또는 축소힙/비활성(circuit breaker+PG 캐시로 DT-less 동작), 목표 2vCPU/4GB.
  - `seed_demo.py`를 1커맨드 경로로 연결(`scripts/eval-up.sh` 또는 install.sh `--seed`).
  - **종료조건:** 2vCPU/4GB 호스트에서 eval 기동 + 시드 1커맨드.
- [x] **B3 — Helm chart 프로덕션화** ✅ #149 (머지 `7216b41`) — bundled/external PG+Redis·frontend·ingress TLS(cert-manager)·migration Job(pre-install/upgrade, owner role)·AUTO_MIGRATE=false·OCI `chart-release.yml`+ArtifactHub 메타. Chart 0.2.0. `dep: B1` `owner: devops-engineer`
  - 신규 템플릿: postgres(StatefulSet, 번들/외부 토글)·redis·frontend Deployment+Service·Ingress+cert-manager TLS·**migration Job**(`pre-install`/`pre-upgrade` 훅, `alembic upgrade head`, owner 역할, 1회).
  - backend 파드 `AUTO_MIGRATE=false`(로드맵 §3: advisory lock은 안전망일 뿐). values: 번들/외부 DB·Redis, ingress host/TLS, 이미지 태그 `release.yml` 동기.
  - OCI(ghcr) 차트 게시 + ArtifactHub 등록(등록은 운영 레인 O3).
  - **종료조건:** `helm install`로 단일 네임스페이스 기동, lint/template green.
- [x] **B4 — API 레퍼런스 호스팅** ✅ #151 (머지 `05e7961`) — redocusaurus `/reference/api` + `scripts/dump_openapi.py`. docs.yml가 빌드 시 스펙 재생성(버전-안정), backend 변경에도 재배포. (브리틀했던 ci.yml byte-exact 게이트는 제거.) `dep: 없음` `owner: doc-writer + backend-developer`
  - `scripts/dump_openapi.py`: FastAPI `app.openapi()` → 정적 스펙 파일. 기존 drift 게이트(`test_openapi_contract.py`)를 진실원으로 재사용.
  - Docusaurus에 OpenAPI 통합 — **redocusaurus 권장**(단일 스펙 임베드, 저유지보수) / docusaurus-openapi-docs(엔드포인트별 MDX, "try-it"·고유지보수)는 대안. `reference/api-overview.md` 연결.
  - `docs.yml`이 빌드, EN/KO.
  - **종료조건:** 공개 문서에서 전체 API 탐색 가능.
- [x] **B5 — 라이브 데모 (GCP terraform 재사용)** ✅ #152 (머지 `9d87763`) — DemoReadOnlyMiddleware(allowlist deny-by-default, 우회 하드닝, security-reviewer "우회 없음")·OAuth 데모 차단(M-1)·비번 평문로그 제거(M-2)·membership-scoped reset(L-3)·terraform demo_reset(Cloud Scheduler→Job)·프론트 배너/게이팅. 실제 배포는 O2. **Track B 완결.** `dep: B2` · `rev: ✅` `owner: devops-engineer + backend-developer + frontend-dev`
  - 잔여 Info(비머지): demo-read-only problem `type` URI가 미들웨어(`urn:`)와 OAuth 핸들러(`https:`)로 갈림 — 차후 상수 통일(선택).
  - **read-only 데모모드(신규 백엔드):** `DEMO_READ_ONLY` 런타임 가드(`os.getenv` 규칙 11) 미들웨어로 쓰기 차단(또는 demo 역할). 프론트는 플래그 시 쓰기 액션 숨김/비활성.
  - **일일 자동 리셋:** Cloud Scheduler → Cloud Run Job(seed_demo drop+reseed) 또는 Actions cron.
  - terraform 재사용(Cloud Run scale-to-0). README/랜딩에서 연결.
  - 실제 GCP 배포는 운영 레인 O2(사용자 클라우드 자격).
  - **종료조건:** 설치 없이 핵심 화면 체험, 데모 격리·일일 리셋 검증.
  - **v2.1 마일스톤 종료조건:** Track A+B 전 PR 머지 green · 데모 도달 가능 · EN/KO · 문서 동기 → §7 v2.1 게이트 통과 후 v2.2 착수.

### v2.2 — 리메디에이션 & 정책 (순차)

> 선행: v2.1 종료. **2.2-b 착수 전 GitHub 쓰기 통합 방식 결정(§8 D1).**

- [x] **2.2-a1 — `fixed_version` 실데이터화** ✅ #153 (머지 `979f459`) — DT findings patched 버전 추출 → `vulnerability_findings.fixed_version`(마이그 0017), adversarial 30케이스. `owner: scan-pipeline-specialist`
  - 고스트 필드 제거: DT/OSV 피드에서 fix 버전 수집 → `vulnerability_findings`/`vulnerabilities`에 저장(필요 시 expand 마이그레이션). 드로어 노출.
- [x] **2.2-a2 — 의존성 그래프 수집** ✅ #154 (머지 `8b1799e`) — `component_dependency_edges` 테이블 + `scan_components.depth`(마이그 0018), cycle-safe BFS, adversarial 전수. `dep: 2.2-a1` `owner: scan-pipeline-specialist`
  - cdxgen `dependsOn` 파싱 → depth/그래프 저장(신규 테이블 또는 `raw_data` 구조화). 직접/전이 depth 산출.
- [x] **2.2-a3 — 업그레이드 추천 엔진 + UI** ✅ #156 (머지 `69c4b96`) — 컴포넌트별 최소 안전 업그레이드(semver 최대), 드로어·게이트 코멘트 노출, 우선순위 신호(direct/severity/EPSS), adversarial 99%. **a-트랙(리메디에이션 추천) 완결.** `dep: 2.2-a2` `owner: backend-developer + frontend-dev`
  - `fixed_version`+그래프+severity/EPSS → "최소 안전 업그레이드" 계산. 취약점 드로어·게이트 코멘트에 권장 버전.
- [x] **2.2-b1 — GitHub 쓰기 자격 모델** ✅ #157 (머지 `96fdeae`) — `github_app_credentials`+`github_app_installations`(마이그 0019), `core/crypto.py` Fernet 암호화저장(prod fail-closed), 설치별 단기 RS256 App JWT→installation token(`mint_installation_token`), per-project 옵트인, per-team RBAC. security-reviewer PASS-WITH-FOLLOWUPS(Crit/High 0)→Medium 3(redirect-disable·GITHUB_API_URL allowlist·installation_id 재검증)+Low 2 fix-first(`70eb877`). `dep: 2.2-a3` · `rev: ✅` `owner: backend-developer → security-reviewer`
  - GitHub App/토큰 자격 DB 저장(암호화) — 현 env-only 확장. 최소 권한·옵트인 per-project.
  - **후속(비머지, b3 전):** MultiFernet 롤링 키회전(현재 단일키 회전 시 기존 자격 brick) — 트래커 task #8.
- [x] **2.2-b2 — 생태계 어댑터(npm 우선) + dry-run** ✅ #159 (머지 `4b40533`) — 순수 npm 어댑터(operator-preserving semver 재작성·format-preserving·lockfile 재생성 플래그), `compute_npm_dry_run`(a3 추천 + preserved-source 타르볼/override manifest), `POST /v1/projects/{id}/remediation/npm/dry-run`(member-gated). compute-only(마이그 없음·GitHub write 없음=b3). package.json adversarial 하드닝. `dep: 2.2-b1` `owner: scan-pipeline-specialist`
  - manifest 수정 어댑터(npm→pip→maven 순). dry-run 기본.
- [x] **2.2-b3 — 자동 PR 생성(옵트인) + UI + 감사** ✅ 백엔드+보안리뷰 #160 (`5c75a4a`) + UI #163 (`f27cb15`, 프로젝트 상세 Remediation 탭: dry-run 미리보기·PR 생성(team_admin·opt-in)·PR 목록, 신규 `remediation` i18n ns, vitest 10신규) — `remediation_pull_requests`(마이그 0021), `create_npm_remediation_pr`(opt-in: 타깃 repo는 저장된 설치링크에서만 도출·caller 지정 불가, b1 토큰→branch/commit/PR, change_fingerprint idempotency), `POST .../remediation/npm/pull-request`+`GET .../pull-requests`. security-reviewer CHANGES REQUESTED(High base_branch 인젝션+Medium 3)→fix-first(`967900a`). **UI(b3-frontend) 미착수.** `dep: 2.2-b2` · `rev: ✅` `owner: backend-developer + frontend-dev → security-reviewer`
  - 브랜치→PR 자동생성, 옵트인, 감사로그. **종료조건: 최소 1개 생태계 PR 생성 + 보안리뷰 통과.** (백엔드 종료조건 충족; UI는 완성도 후속.)
- [x] **2.2-c1 — 동적 라이선스 정책 모델** ✅ #158 (머지 `2453501`) — `license_policies` 테이블(마이그 0020, org/team 스코프, `category_overrides`/`license_exceptions`(시한부 waiver)/`unknown_license_category` posture/`compound_operator_strategy`/`enabled`), CRUD API `/v1/license-policies`, `get_effective_policy` 우선순위 resolver(team>org>static) + 단일-id `effective_category` 헬퍼, adversarial 스키마검증. b1과 병렬개발→통합 시 마이그 0019→0020 재번호. `policy_gate` 미수정(c2). `dep: 2.2-a3 (병렬 가능)` `owner: db-designer + backend-developer`
  - per-team/org 정책(허용/조건부/금지 + 예외 + SPDX expression 룰) 모델 + 마이그레이션.
- [x] **2.2-c2 — Policy Gate 동적 룰 평가** ✅ #161 (머지 `e155ee7`) — `services/license_expression.py` 하드닝 compound-SPDX 평가기(길이4096/깊이64/토큰1024 bound, 선형 lexer+depth-guarded recursive descent, un-parseable→unknown posture+warning, never hang/raise/500), `policy_gate` 정책-aware 전환(정책 시 동적 재분류·무정책 시 byte-identical, golden 25 통과, batched+memoised no-N+1). 마이그/엔드포인트 없음. `dep: 2.2-c1` `owner: backend-developer`
  - 게이트가 정적 lookup 대신 동적 룰 평가(정적 카탈로그는 기본값으로 유지). SPDX expression **adversarial 테스트**([[feedback-adversarial-input-parametrize]] normalize_spdx_id 재귀 DoS 선례).
- [x] **2.2-c3 — 정책 편집 Admin UI** ✅ #162 (머지 `1e2d135`) — `/policies` 라우트+사이드바, category_overrides/license_exceptions(시한부 waiver)/unknown posture/compound 전략/enabled 편집기, scope-aware(super_admin=org+팀, 비-team_admin=graceful read-only), TanStack Query 저장/리셋·422 RFC7807 surfacing, 신규 `policies` i18n ns(EN/KO, i18n:check OK), vitest 26 신규. `dep: 2.2-c2` `owner: frontend-dev + i18n-specialist`
- [x] **2.2-c4 — 라이선스 텍스트/의무 카탈로그 보강** ✅ #164 (머지 `fdea868`) — `services/obligation_catalog.py`(32개 카탈로그 라이선스의 구조화 의무: attribution/text/copyright/state-changes/source-disclosure none·library·network/patent/same-license/notice-file), `sync_catalog_obligations` 멱등 upsert(read-path, 기존 obligations 테이블, 마이그 없음) — **실제 스캔이 License는 만들되 Obligation 0개였던 갭 해소**. link은 reference_url 없으면 canonical SPDX URL fallback(통합 시 CI clean-DB 모순 테스트 수정). `dep: 2.2-c1` `owner: backend-developer + doc-writer`
  - **v2.2 마일스톤 종료조건:** ≥1 생태계 자동 PR + 보안리뷰 · 코드 변경 없이 팀이 정책 편집 · adversarial SPDX green. **→ ✅ 전부 충족 (2026-05-25).**

### v2.3 — 공급망 무결성 & 우선순위화 (순차)

> 선행: v2.2 종료.

- [x] **2.3-s1 — SBOM 서명 인프라(cosign)** ✅ #166 (머지 `044ac6f`) — `integrations/cosign.py`(`sign_blob`/`verify_blob`, 고정 argv·`--` 센티넬·blob symlink 거부·best-effort skip), cdxgen SBOM persist 직후 `sign` 스테이지(30%) → ScanArtifact 새 kind `sbom_cyclonedx_sig`/(keyless 시)`sbom_cyclonedx_cert`(**마이그 없음**, 기존 `kind`+미사용 `sha256` 재사용). **D2 ✅ key-based 기본 + keyless 옵션**: private key password Fernet(`core.crypto`) 암호화 → `COSIGN_PASSWORD` subprocess env로만(argv/로그 금지, prod fail-closed). `Dockerfile.worker` cosign 2.4.1 + **per-arch SHA256 in-repo ARG 핀**. `scrubbed_env_for_cosign`(Sigstore 엔드포인트만). security-reviewer **PASS**(Crit/High 0)→fix-first(SHA256 in-repo 핀·prod fail-closed `SecretEncryptionError` 흡수·stderr 시크릿 스크럽·blob symlink 거부). `rev: ✅` `owner: scan-pipeline-specialist → security-reviewer`
  - worker 이미지에 cosign. SBOM 생성 시 서명(key-based 기본/keyless 옵션, §8 D2 결정). 키 취급은 보안리뷰.
- [x] **2.3-s2 — in-toto attestation + SLSA provenance** ✅ #167 (머지 `d803a29`) — `integrations/attestation.py`(SLSA provenance v1 predicate/statement 순수 빌더 + `cisa_minimum_elements_present`), `integrations/cosign.py` `attest_blob`/`AttestResult`(s1 헬퍼 재사용, key-based 기본+keyless 옵션). `sign` 스테이지에서 서명 성공 시에만 `_attest_sbom` → ScanArtifact 새 kind `sbom_attestation`/(keyless)`sbom_attest_cert`(**마이그 없음**). predicate=opaque scan/project UUID+builder+timestamp만(git URL/경로/시크릿 없음). CISA generation-context(component hash·tool name/version·context) 강제, NTIA 7요소는 SBOM 본문. security-reviewer **PASS**(Crit/High 0)→fix-first(keyless cert 누락 시 skip[s1 `_sign_keyless`도]·로그 마스킹 일관·subject.name trust 주석). `dep: 2.3-s1` `owner: scan-pipeline-specialist → security-reviewer`
  - attestation 생성. CISA 2025(component hash·tool/generation context)·NTIA 7요소 점검.
- [x] **2.3-s3 — 서명 다운로드 UX + 검증 문서** ✅ (be #170 + fe #171 + doc #172) `dep: 2.3-s2` `owner: frontend-dev + doc-writer`
  - **s3-be ✅ #170 (머지 `0d33c1d`)** — 기존 sbom 라우터 확장 5+1 엔드포인트(`/sbom/signature`·`/certificate`·`/attestation`·`/attestation-certificate`·`/public-key`·`/signature-bundle` zip[SBOM+.sig+cert|pubkey+attestation(+attest cert)+`VERIFY.md`]), 최신 succeeded 스캔 기준(export와 일치). security-reviewer **PASS**(Crit/High 0, Medium 1 fix-first): public-key PEM 헤더 가드(private key→logged 404)·크기 캡 413·경로 traversal 봉인(`is_relative_to`)·IDOR 404·keyless attest cert 번들 포함. `services/sbom_signature.py` 95% cov.
  - **s3-fe ✅ #171 (머지 `03405e5`)** — SBOM 탭 "Signature & Verification" 섹션, 주 버튼 signature-bundle(.zip, self-contained) + 보조 개별, 404 graceful(미서명/keyless-전용), 검증가이드 링크(`/docs/reference/sbom-signature-verification`), EN/KO, vitest 7 신규.
  - **s3-doc ✅ #172 (머지 `52c9d83`)** — `cosign verify-blob` 외부 검증 가이드 EN/KO(`docs-site/docs/reference/sbom-signature-verification.md` + i18n/ko), sidebars 등록, glossary, build green. 엔드포인트/파일명/명령을 백엔드 소스와 대조.
  - 다운로드 시 서명 동봉. **종료조건: `cosign verify` 외부 검증 가능 ✅** (다운로드 엔드포인트 + 번들 + UI + EN/KO 가이드).
- [x] **2.3-r1 — Reachability 스캔 태스크(Go govulncheck 우선)** ✅ #165 (머지 `b3a7045`) — `integrations/govulncheck.py`(subprocess 어댑터 + 스트리밍 JSON 파서, 적대적 출력 방어), `tasks/scan_reachability.py` Celery 태스크(규칙 3: 동기 금지, 보존 소스 tarball 사용, best-effort skip). **마이그 0022**(expand): `vulnerability_findings`에 nullable `reachable`(tri-state)·`reachability_source`·`reachability_analyzed_at`(백필/NOT-NULL 없음). 소스 스캔 성공 후 비블로킹 chain(`enqueue_reachability`, `REACHABILITY_ENABLED` 게이트). GO-id+CVE/GHSA alias→`pkg:golang/%` finding per-pk UPDATE(멱등). `govulncheck@v1.1.4` 핀(Go 1.25.10 기존). `owner: scan-pipeline-specialist`
  - Celery 태스크(규칙 3: 동기 금지). finding에 reachability 신호 저장(expand 마이그 0022). 베스트에포트 라벨.
- [x] **2.3-r2 — reachability 정렬·게이트·UI 배지** ✅ (be #168 + fe #169) `dep: 2.3-r1` `owner: backend-developer + frontend-dev`
  - **r2-be ✅ #168 (머지 `51a0577`)** — **마이그 0023**(partial index `WHERE reachable IS TRUE`), `?reachable=true|false|unknown` 필터 + `sort=reachable`(reachable→NULL→false, 버킷 내 severity desc), 응답 `reachable`/`reachability_source`/`reachability_analyzed_at` 노출, 게이트 `reachable_critical_cve_count`+`reachable_gate_enforced`. 게이트 완화 플래그 `GATE_REACHABLE_CRITICAL_ONLY`(opt-in, 기본 off). security-reviewer **PASS**(Crit/High 0, Medium 2 fix-first): **safe-by-default fallback**(`analysed>0`일 때만 완화, `blocking=total-unreachable`로 `reachable IS FALSE`만 제외·**NULL 보수적 차단 유지** → non-Go silent-disable 제거) + 완화 발동 WARNING 로그 + .env 문서 + SCA 코멘트 advisory. tri-state/IDOR/인젝션 안전 확인.
  - **r2-fe ✅ #169 (머지 `4c7c735`)** — `ReachabilityBadge`(tri-state: reachable 강조/unreachable muted/null list생략·drawer명시), `?reachable=true|false|unknown` 인라인 필터·`sort=reachable` 정렬·URL 미러링, EN/KO i18n(복수형 미사용), vitest 14 신규. **v2.3 종료조건 "≥1 언어 reachability 구분 노출" ✅ 충족(Go).**
- [ ] **2.3-r3 — 차기 언어 확대(베스트에포트)** (선택 · 종료조건 ≥1 언어 초과분) `dep: 2.3-r2` `owner: scan-pipeline-specialist`
  - Go 외 언어 reachability(예 Python/JS — 도구·call-graph 별도). v2.3 게이트는 Go(r1)로 이미 충족 → follow-up.
  - **v2.3 마일스톤 종료조건:** 서명 SBOM 외부 검증 ✅ · ≥1 언어 reachable/unreachable 구분 노출 ✅ (Go).

---

## 5. 운영 레인 (외부 블로커 — 코드와 비동기)

> 사용자(GitHub/클라우드 admin) 작업. **코드 PR을 막지 않는다.** 준비되면 처리.

- [ ] **O1 — v2.0.1 이미지 게시** — 첫 `v2.0.1` 태그 cut → `trustedoss` org Actions `packages:write` + ghcr public → `install-uat.yml`의 `published-image-pull` `continue-on-error` 제거. **공개 차단점.**
- [ ] **O2 — 데모 GCP 배포** — terraform apply(사용자 GCP 자격). B5 코드 선행.
- [ ] **O3 — Helm 차트 ArtifactHub 등록** — B3 완료(`chart-release.yml` 추가됨). 남은 운영 작업: `chart-vX.Y.Z` 태그로 OCI 게시 → ghcr `charts/trustedoss` 패키지 public → ArtifactHub 등록(`artifacthub-repo.yml`의 repositoryID 기입) + `docs/static/img/logo.png` 추가(Chart icon 참조).
- [ ] **O4 — 데모/문서 스크린샷 갱신** — VEX·정책편집·서명 UI 반영(EN/KO). 각 UI PR 후속.

---

## 6. 의존성 그래프 (착수 순서)

```
v2.1 (병렬 2트랙)
 ├─ Track A:  A1(export) ─→ A2(import, rev) ─→ A3(UI)
 └─ Track B:  B1(/health/ready) ─→ B2(eval+seed) ─→ B5(데모, rev)
              B1 ─→ B3(Helm)
              B4(API문서, 독립) ──────────────────────────────────┐
   v2.1 게이트(§7) ◀── A3 · B3 · B4 · B5 ───────────────────────┘
        │
        ▼
v2.2 (순차)  a1→a2→a3 ─→ b1(rev)→b2→b3(rev)
                       └→ c1→c2→c3 ;  c1→c4     (c트랙은 a3 후 b와 병렬 가능)
   v2.2 게이트(§7)
        │
        ▼
v2.3 (순차)  s1(rev)→s2→s3   ‖   r1→r2→r3
   v2.3 게이트(§7)
        │
        ▼
W6 (DT → Trivy)
  prereq(v2.3.1 tag/Release, O1+O3 통합)
        │
        ▼
  #45(ADR/CLAUDE.md) ─→ #40(어댑터, rev) ─→ #41(persist+벤치, rev)
                                                  │
                                                  ▼
                                        shadow 7d (≥95% 게이트)
                                                  │
                                                  ▼
                                       #42(재매칭) → #43a(BE 제거, rev) → #43b(FE)
                                                                              │
                                                                              ▼
                                                                      #43c(문서) → #43d(배포)
                                                                                       │
                                                                                       ▼
                                                                                    #43e(Trivy DB 패널)
                                                                                       ‖
                                                                                    #44(Trivy DB 라이프사이클, 필수)
                                                                                       ‖
                                                                                    #43f(stage rename, 선택·v2.4.1)
```

---

## 7. 마일스톤 종료 게이트 (다음으로 넘어가기 전 필수 점검)

각 마일스톤은 아래를 **모두** 만족해야 "완료" 선언 + 다음 착수.

- **v2.1: ✅ 충족 (2026-05-24, 8 PR #145–#152 머지, main CI green).** A1–A3·B1–B5 머지 green · VEX export/import/UI 왕복 + adversarial + security-reviewer green · helm 프로덕션 차트(lint/template) · `/health/ready` 게이트 전환 · eval 프로파일 · API 레퍼런스(redoc) 공개 · 데모 read-only+일일리셋(코드) · EN/KO·문서 동기. **잔여=운영 레인만**: O1(이미지 게시·공개차단점), O2(데모 GCP 배포), O3(차트 ArtifactHub). → v2.2 착수 가능.
- **v2.2: ✅ 충족 (2026-05-25, 10 PR #153,154,156–164 머지, main CI green).** ≥1 생태계 자동 PR 생성(b3 #160 백엔드+#163 UI, opt-in·idempotent) + security-reviewer green(b1 #157·b3 #160 모두 fix-first 통과) · 팀이 코드 변경 없이 정책 편집(c1 #158 모델+c2 #161 동적게이트+c3 #162 UI) · SPDX adversarial green(c2 길이/깊이/토큰 bound·no-policy byte-identical) · `fixed_version` 실데이터 노출(a1 #153) · 의무 카탈로그(c4 #164) · main CI green. **잔여=후속만**: MultiFernet 키회전(태스크#8, b3 GA 전) + 사전존재 CI flake(태스크#10). → v2.3 착수 가능.
- **v2.3: ✅ 충족 (2026-05-25, 8 PR #165–#172 머지, main CI green).** 서명 SBOM `cosign verify` 외부 검증(s1 cosign 서명 #166 + s2 in-toto/SLSA attestation #167 + s3 다운로드 엔드포인트/번들 #170·UI #171·EN/KO 검증가이드 #172) + security-reviewer green(s1·s2·s3-be 모두 PASS+fix-first) · ≥1 언어 reachability 구분 노출(r1 govulncheck #165 + r2 API·게이트 #168·UI 배지 #169, Go) · `GATE_REACHABLE_CRITICAL_ONLY` safe-by-default fallback · D2 ✅ key-based 기본+keyless 옵션 · 마이그 head 0023 · main CI green. **잔여=선택만**: r3(차기 언어 reachability 확대, 종료조건 ≥1 언어 초과분). → **v2.1·v2.2·v2.3 전체 완료.**

---

## 8. 미결 결정 (착수 시점에 확정)

- **D1 ✅ 결정(2026-05-24): GitHub App** — 설치형, per-repo 세밀 권한(contents/pull_requests:write), 설치별 단기 토큰, 멀티테넌트. 2.2-b1에서 App 자격 모델 구현, security-reviewer 동반.
- **D2 ✅ 결정(2026-05-25): key-based 기본 + keyless 옵션** — 셀프호스팅/온프렘/에어갭 포지셔닝상 cosign **key-based**(키페어 생성·암호화 보관)를 기본으로, CI 친화 **keyless(OIDC)**는 옵션 경로로 제공. 키 취급은 security-reviewer 동반(2.3-s1).
- **D3 (B4): OpenAPI 통합 라이브러리** — redocusaurus(권장) vs docusaurus-openapi-docs. 착수 시 빌드 호환 확인.

---

## 9. 세션 핸드오프 규약

- 세션 종료 시 `docs/sessions/<YYYY-MM-DD>-<topic>.md` 작성(`v2-execution-plan.md` §7 양식).
- 핸드오프 "다음 세션" 섹션의 후속 항목은 **라벨만 적지 말 것** — 다음 세션이 단독으로 의도·범위·출발 파일/심볼을 파악할 수 있는 수준으로 풀어 쓴다([[feedback-handoff-next-session-must-be-self-sufficient]]). 의도가 정해지지 않은 라벨은 트래커·핸드오프 어디에도 두지 않는다.
- 다음 세션 첫 메시지(인테이크 모드): "사용자가 핸즈온에서 발견한 불편함/버그를 보고하면 인테이크 → 코드 대조 → PR scope로 좁힌다." 트래커 §0.5는 Wave 1~3까지 모두 ✅ 종결됐으므로 다음 항목은 사용자 보고로 정의된다.
- **이 문서의 체크박스·대시보드가 항상 현재 진실.**
