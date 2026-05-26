# Wave 4 — UX 정합성 + 정보 구조(IA) 재정비

> **상태**: 2026-05-26 작성. 사용자 핸즈온 후속 #16 ~ #22 항목의 단일 진실 계획.
> **선행**: W1~W3 (`docs/post-ga-execution-tracker.md` §0.5) + 운영 트리아지 P0~P4 (PR #173 ~ #184) 모두 머지 완료.
> **본 문서를 갱신하지 않은 채 항목을 진행하지 말 것** — 핸드오프가 깨진다.

---

## 0. 한눈에

| 묶음 | 항목 | 핵심 변화 | PR 단위 | 영역 | 우선 |
|---|---|---|---|---|---|
| **W4-A** | #18 | Admin 메뉴 진입 시 일반 메뉴 사라지는 버그 | 단독 PR | frontend-dev | **P0** |
| **W4-B** | #16, #17, #19 | UX 정합성 — 차트 deep-link, 컬럼 헤더 정렬, License 컬럼 분리, 필터 단순화 | 1~2 PR | frontend-dev (+일부 BE schema 확인) | P1 |
| **W4-C** | #20, #21, #22 | IA 재정비 — 탭 11개 → 8개로 통합 | 단일 PR (충돌 회피) | frontend-dev | P1 |
| **W4-D** | #17(d)·#19(component 컬럼) 후속 | TYPE/USAGE 본격 픽스 (cdxgen `dependencies` 누락) | 1 PR | scan-pipeline-specialist | P2 |
| **부속** | DT 자동 propagation | 운영자 키 등록 자동화 — 사용자 검토 중 (Task #17) | 보류 | devops-engineer | TBD |

**탭 정리 결과(W4-C 완료 시)**: `Overview / Releases / Components / Vulnerabilities / Source / Compliance / Reports / Settings` (8개).

---

## 1. W4-A — #18 Admin 메뉴 layout 버그 (P0, 단독 PR)

### 무엇이 갭
super-admin 로그인 → "Administration" 그룹의 메뉴(Users / Teams / DT / Scans / Disk / Audit / Health 등) 진입 시 사이드바에서 일반 메뉴(Dashboard / Projects / Scans / Approvals / Policies / Integrations)가 사라짐. 현재 layout이 admin nav와 main nav를 분리해 unmount하는 구조.

사용자 기대: **사이드바는 항상 일반 메뉴 + admin 섹션을 모두 보여줘야** — admin 진입했다고 일반 portal 사용이 막혀선 안 됨.

### 출발 파일 / 심볼
- `apps/frontend/src/components/Layout/AppShell.tsx` (또는 `Sidebar.tsx` — 검색 필요)
- `apps/frontend/src/App.tsx` — `<Route path="/admin/*">` 그룹이 별도 layout 컴포넌트를 wrapping하는지 확인
- `apps/frontend/src/features/admin/components/` — admin 영역의 layout 컴포넌트

### 사전 갭 분석 포인트
1. **AppShell 단일 vs Admin 별도 layout**: `git grep -nE "AppShell|AdminLayout|Outlet" apps/frontend/src/` 로 layout 컴포넌트 구조 파악.
2. **사이드바 메뉴 정의 위치**: 일반 메뉴와 admin 메뉴 정의가 같은 array인지, 별도 array인지. 같으면 단순 visibility 토글 문제, 다르면 layout 통합 작업.
3. **권한 게이팅**: admin 메뉴는 super-admin에게만 보임. 일반 사용자에게 admin 섹션을 sidebar 하단에 숨겨도 되는지 결정.

### 권장 접근
- 단일 AppShell + 사이드바에 일반 메뉴 + `(super-admin only)` admin 섹션 분리 표시
- Admin route는 별도 layout 없이 main `<Outlet/>` 안에 mount
- 시각적 구분: admin 섹션 위에 separator + 작은 라벨 "Administration"

### 검증
- super-admin 로그인 → Admin → Users 진입 → 사이드바에 Dashboard / Projects 여전히 보임
- developer 로그인 → admin 섹션 자체가 보이지 않음 (권한 가시성)
- 기존 admin 페이지 routing E2E 테스트 회귀 없음

---

## 2. W4-B — #16 / #17 / #19 UX 정합성 (P1, 1~2 PR)

세 항목은 **동일한 UX 패턴 3개**를 공유하므로 함께 처리해야 conflict + 재작업 회피.

### 2.1 공통 패턴

#### 패턴 1: Overview 차트 클릭 → 탭 deep-link
- Severity 차트 segment 클릭 → `?tab=vulnerabilities&severity=critical`
- License 차트 segment 클릭 → `?tab=licenses&category=forbidden` (또는 W4-C 후 `?tab=compliance&category=forbidden`)
- Recent Scans 행 클릭 → status별 분기 (succeeded → `?tab=components&scan=<id>`; running → drawer)

#### 패턴 2: 컬럼 헤더 정렬
- Toolbar 드롭다운(Sort by / Order) 제거
- 컬럼 헤더 클릭 → asc → desc → unset 3-state cycle
- sort indicator (↑↓) 표시

#### 패턴 3: License 컬럼 분리
- 실제 라이선스 이름 (MIT, Apache-2.0 등 SPDX) — `LICENSE` 컬럼
- 정책 카테고리 (Allowed/Forbidden/Conditional) — `LICENSE POLICY` 별도 컬럼

### 2.2 #16 — Overview 탭 정비

#### 무엇이 갭
(a) Risk Score 카드가 정보 가치 낮음 (이미 W1 #34에서 2축 분리 했지만 사용자 인지 X). 제거.
(b) Vulnerability severity / License classification 차트가 정적 — 클릭 동작 없음. 패턴 1 적용.
(c) Recent Scans 행 클릭: 현재 모두 drawer 열기. status별 분기 필요 (패턴 1).

#### 출발 파일
- `apps/frontend/src/features/projects/components/OverviewTab.tsx`
- `apps/frontend/src/features/projects/components/RiskAxes.tsx` (제거 또는 컴팩트 위치 이동)
- `apps/frontend/src/features/projects/components/SeverityDistributionChart.tsx` (onClick 추가)
- `apps/frontend/src/features/projects/components/LicenseDistributionChart.tsx` (onClick 추가)
- `apps/frontend/src/features/projects/components/RecentScansTable.tsx` (status별 라우팅 분기)
- `apps/frontend/src/features/projects/ProjectDetailPage.tsx` (`handleViewSnapshotComponents` 와 통합)

#### 사전 갭 분석
- **Risk Score 완전 제거 vs 헤더로 이동**: 헤더에 이미 게이지가 있음(`MEDIUM RISK 30/100`). Overview 안의 RiskAxes는 그 게이지의 상세 분해. 완전 제거 시 사용자가 "왜 30?" 못 알아봄 → **Overview 카드만 제거하고 헤더 게이지는 유지** 권장.
- **차트 deep-link의 fragment 처리**: 같은 페이지 내 탭 전환이므로 `setSearchParams` 사용, 페이지 reload 없음.
- **Recent Scans status 매핑**: `succeeded/failed/cancelled` → Components 탭 이동, `queued/running` → drawer 열기 (P1 #11에서 spinner는 픽스했지만 Components 라우팅은 미완료).

### 2.3 #17 — Components 탭 UX 대수술

#### 무엇이 갭
(a) **TYPE 컬럼 `-` 대부분** (P3 #12 진단 — cdxgen `dependencies` 배열 누락이 본 원인). **W4-D로 분리** — 본 PR scope에선 컬럼 추가만, 데이터 채워지는지 후속.
(b) **USAGE 컬럼 `-` 대부분** — npm은 cdxgen이 scope 미배출(P3 #12 진단). 마찬가지 데이터 갭, 컬럼은 표시만.
(c) LICENSE 컬럼이 "Allowed/Forbidden" → 패턴 3 적용.
(d) Severity/License 필터 드롭다운 제거 → 패턴 1로 대체.
(e) Dependency Type 컬럼 추가 — 데이터 이미 있음 (`dependency_type`).
(f) Sort 드롭다운 제거 → 패턴 2 적용.

#### 출발 파일
- `apps/frontend/src/features/projects/components/ComponentsTab.tsx`
- `apps/frontend/src/features/projects/components/ProjectListToolbar.tsx` (또는 Components 전용 toolbar — 검색)
- `apps/backend/schemas/component*.py` — 응답에 `license_spdx`와 `license_policy_category`가 분리 필드인지 확인. 합쳐져 있으면 BE 변경 필요.

#### 사전 갭 분석
- **BE 응답 shape**: `ComponentSummary` / `ComponentDetailResponse`에 `license_name` vs `license_policy_category`가 별개 필드인지. 같은 필드라면 BE에서 분리 작업 필요 (별도 sub-PR 또는 본 PR에 포함).
- **Dependency Type 컬럼 데이터**: W2 #31에서 `dependency_type`(direct/transitive), `usage`(required/optional)가 응답에 들어가 있음. 컬럼 표시만 추가.

### 2.4 #19 — Vulnerabilities 탭 UI 정비

#### 무엇이 갭
(a) 필터 8개 (Search/Severity/Status/Reachability/License/Sort by/Order/EPSS) 가로 정렬 → 화면 폭 초과
(b) 테이블 컬럼 10개 가로 스크롤 없이 잘림 (특히 TITLE)
(c) **Component / Version 컬럼 누락** — CVE가 어느 컴포넌트의 것인지 한눈에 안 보임
(d) LICENSE 컬럼이 "Allowed" → 패턴 3
(e) Severity/License 필터 + Sort 드롭다운 → 패턴 1, 2

#### 출발 파일
- `apps/frontend/src/features/projects/components/VulnerabilitiesTab.tsx`
- `apps/frontend/src/features/projects/components/VulnerabilitiesToolbar.tsx`
- `apps/backend/schemas/vulnerability*.py` — `affected` 필드가 component name + version 분리 가능한지 확인

#### 사전 갭 분석
- **AFFECTED 컬럼 데이터**: 현재 count 표시(예: "5 components"). component name + version 표시로 바꾸려면 응답 shape 확장 필요. 또는 컬럼을 2개로 — Component(첫 affected의 이름@버전) + Affected Count(N).
- **컬럼 우선순위 (잘림 회피)**: CVE ID / Component / Severity / CVSS / EPSS / Reachability / Title (ellipsis+tooltip) / Status. Discovered는 drawer로 이동.
- **남은 필터**: Search / Status / Reachability / EPSS ≥ (4개로 압축).

### 2.5 묶음 PR 권장
- **PR A**: 공통 인프라 — 차트 onClick prop, 컬럼 헤더 sort hook, License 분리 helper. 작은 PR.
- **PR B**: #16 + #17 + #19 적용. 큰 PR이지만 단일 영역(`features/projects/components/`)이라 관리 가능.

또는 단일 PR (총 + 약 800~1200줄 diff 예상).

---

## 3. W4-C — #20 / #21 / #22 IA 재정비 (P1, 단일 PR)

세 항목 모두 **`ProjectDetailPage.tsx`의 탭 정의를 동시 변경** → conflict 회피 위해 단일 PR.

### 3.1 #20 — Licenses + Obligations → Compliance 통합

#### 무엇이 갭
두 탭의 컬럼이 거의 동일(spdx_id/name/category/kind/affected_count). 사용자는 차이를 인지 못함. 실제 분리 가치는 NOTICE export action뿐.

#### 통합 디자인
- 단일 `ComplianceTab` 컴포넌트
- 상단: 라이선스 인벤토리 표(spdx_id / name / category / kind / affected_count)
- 하단/우측: 선택된 라이선스 drawer — 라이선스 본문 + 의무사항 본문 + 영향받는 컴포넌트 + NOTICE export 버튼
- 헤더 액션: "Generate NOTICE for all" 버튼

#### 출발 파일
- `apps/frontend/src/features/projects/components/LicensesTab.tsx` → 통합 후 삭제
- `apps/frontend/src/features/projects/components/ObligationsTab.tsx` → 통합 후 삭제
- `apps/frontend/src/features/projects/components/ObligationDrawer.tsx` → ComplianceDrawer로 확장
- `apps/frontend/src/features/projects/components/ComplianceTab.tsx` (신규)
- `apps/frontend/src/locales/{en,ko}/project_detail.json` — `licenses` + `obligations` 섹션 → `compliance` 단일

#### 사전 갭 분석
- BE 응답: `GET /v1/projects/{id}/licenses` 와 `GET /v1/projects/{id}/obligations` 가 별개 endpoint인지 + 데이터 중복인지 확인. 중복이면 단일 endpoint로 줄일 가치.
- `licenses` 와 `obligations` API 둘 다 사용하는 외부 코드(예: SDK, doc 예제) 영향 — `docs-site/` 검색.

### 3.2 #21 — SBOM 탭 → Reports로 흡수

#### 무엇이 갭
W3 #32에서 "통합 Reports 센터"가 들어왔지만 SBOM 탭이 별도로 존재. ReportsTab의 SBOM 카드는 "Go to SBOM" redirect만 함.

#### 통합 디자인
- SbomTab.tsx 콘텐츠를 ReportsTab의 collapsible section 또는 dedicated subview로 이전
- CycloneDX/SPDX 포맷 선택, Excel/PDF, signature 다운로드 등 모든 action을 ReportsTab 안에서 처리
- 별도 SBOM 탭 제거
- 라우팅: `?tab=sbom` → `?tab=reports#sbom` redirect (deprecation 호환)

#### 출발 파일
- `apps/frontend/src/features/projects/components/SbomTab.tsx` → 이전 후 삭제
- `apps/frontend/src/features/projects/components/ReportsTab.tsx` (SBOM section 추가)
- `apps/frontend/src/features/projects/ProjectDetailPage.tsx` (SBOM tab 제거)
- `apps/frontend/src/locales/{en,ko}/project_detail.json` — `tabs.sbom` 키 제거, `reports.sbom.*` 추가

#### 사전 갭 분석
- 기존 SBOM 다운로드 URL이 deep-link로 외부 시스템(CI 등)에서 호출되는지 확인. 영향 있으면 backward-compat 유지.

### 3.3 #22 — Remediation → Vulnerabilities로 통합

#### 무엇이 갭
"취약점 발견 → 해결책 자동 생성"이 연속 흐름인데 별도 탭이라 분절. Snyk처럼 통합형.

#### 통합 디자인
- VulnerabilitiesTab 상단 헤더 영역에 "Dry-run preview" 버튼 (모든 사용자) + "Open Remediation PR" 버튼 (team admin)
- dry-run 결과는 inline panel 또는 expandable section
- 기존 remediation PR 목록은 CVE 테이블 아래 collapsible "Remediation PR history" 섹션
- (선택) CVE drawer에 해당 CVE 해결 버전 제안 표시

#### 출발 파일
- `apps/frontend/src/features/projects/components/RemediationTab.tsx` → 이전 후 삭제
- `apps/frontend/src/features/projects/components/VulnerabilitiesTab.tsx` (remediation 슬롯 추가)
- `apps/frontend/src/features/projects/ProjectDetailPage.tsx` (Remediation tab 제거)
- 라우팅: `?tab=remediation` → `?tab=vulnerabilities#remediation` redirect

#### 사전 갭 분석
- `useRemediationPreview` / `useCreateRemediationPr` / `useRemediationPrs` 훅이 그대로 사용 가능한지 확인
- VulnerabilitiesTab이 #19로 이미 복잡해질 예정 — Remediation까지 흡수 시 panel 분리 신중히

### 3.4 묶음 PR 권장
- W4-C는 `ProjectDetailPage.tsx`를 세 곳 모두 만지므로 **반드시 단일 PR**
- 사이즈 예상: + 약 600~1000줄 diff (대부분 신규 컴포넌트), - 약 800~1200줄 (기존 4개 탭 삭제)

---

## 4. W4-D — TYPE / USAGE 본격 픽스 (P2)

### 무엇이 갭
P3 #12 PR #183 진단 보고서 (`docs/diagnose/p3-12-vulns-type-usage-2026-05-26.md`)의 갭 2 본격 해결:
- (a) cdxgen 12.3.3이 npm에서 `scope` 미배출 → Usage NULL
- (b) cdxgen이 `components`만 배출하고 `dependencies` 배열 누락 시 → Type NULL

### 접근
- ORT analyzer cross-merge (이미 plan 자체에 명시) — cdxgen 결과 + ORT analyzer 결과를 BE에서 병합
- 또는 cdxgen 옵션 튜닝 (`--required-only` 제거, `--include-dependencies` 명시 등)

### 출발 파일
- `apps/backend/integrations/cdxgen.py` — 호출 옵션
- `apps/backend/integrations/dependency_graph.py` — dependencies 파싱 + early-return WARNING 로그
- `apps/backend/tasks/scan_source.py` — 두 분석기 결과 병합 로직 (ORT 활성화 시)
- (선택) ORT integration 부활 검토

### 사전 갭 분석
- P3 #183이 추가한 WARNING 로그 + `dependencies_count` 로그 필드 — **재스캔 후 worker 로그 검토** 가 첫 단계. 실제 cdxgen이 emit한 dependencies 수를 보고 ORT cross-merge 필요성 확정.
- ORT analyzer 부활은 큰 작업 (v1에서 v2로 가면서 deprecated 했었음 — PR-A2 노트). 비용/효익 재평가.

---

## 5. 진행 순서 (권장)

```
Step 1: W4-A (#18 admin layout) — 즉시. 사용자 admin 기능 사용 막힘.
Step 2: W4-B-prep — 공통 인프라 PR (차트 onClick prop, 컬럼 header sort, license split helper)
Step 3: W4-B (#16 + #17 + #19) 적용 PR
Step 4: W4-C (#20 + #21 + #22) IA 재정비 단일 PR
Step 5: W4-D (TYPE/USAGE 본격) — 별도 트랙 (scan-pipeline-specialist)
```

각 step은 main 머지 후 다음 step branch가 main에서 fork. W4-B와 W4-C가 같은 `ProjectDetailPage.tsx`를 만지므로 순서 엄수.

---

## 6. 미머지 / 진행 중 작업 (2026-05-26 본 문서 작성 시점)

| ID | 상태 | 비고 |
|---|---|---|
| PR #185 (예정) | scan-pipeline-specialist agent — P2 #8c (cdxgen+scancode stdout WS streaming) | background, isolation: worktree. 완료 알림 대기. |
| Task #17 | DT propagation 자동화 (.env → ConfigProperty PUT) | 사용자 "좀 더 고민해볼게" — 보류 |

머지된 운영 트리아지 PR: #173 ~ #184 (15개 항목 + hotfix #184 완료).

---

## 7. 핸드오프 규약

다음 세션에서 본 문서를 읽고 즉시 진행 가능하도록:
- 각 항목의 **무엇이 갭 / 출발 파일 / 사전 갭 분석 포인트** 위에 명시
- 변경 시 본 문서를 갱신 + commit (`docs(w4): ...`)
- 머지된 PR은 §6에서 ✅ 표시
- 새 갭 발견 시 본 문서에 추가 후 트래커 §0.5 W4 라인 갱신

---

## 8. 참조 메모리

- [[feedback-tracker-clear-intent-only]] — 라벨만 적힌 한 줄 라인 금지
- [[feedback-handoff-next-session-must-be-self-sufficient]] — 본 문서 양식 근거
- [[feedback-parallel-subagent-worktree-isolation]] — W4 작업도 병렬 위임 시 `isolation: "worktree"` 명시
- [[feedback-tracker-text-may-overstate-gaps]] — 착수 전 코드 검증으로 갭 재확정
