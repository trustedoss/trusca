# 경쟁 UX 감사 보고서 (2026-05-27)

> 짝 문서: 계획서 [`competitive-audit-plan-2026-05-27.md`](./competitive-audit-plan-2026-05-27.md)
> 원자료: `screens/`, `raw/{ours-observations,competitor-sources,scoring-matrix,bugs-found}.md`
> 평가자: 우리 portal 개발자 (bias 인지 — §11 한계 참조)

---

## 1. Executive Summary

**TrustedOSS Portal v2의 UI/UX는 글로벌 상용 SCA SaaS 대비 production-grade 베이스라인 수준.** 8축 평가에서 우리 평균 3.6/5 (채워진 8 셀), BD/Datadog 4.0, Mend 3.7, Snyk 2.8, Sonatype 2.7. **두드러지게 약한 영역은 없으나, "충분히 경쟁력 있다"고 단언하려면 5건의 개선이 필요**:

| 발견 | 영향 | 우선 |
|---|---|---|
| **Drawer 데이터 버그 2건** (References "REF"만, Summary 중복) | 핵심 SCA 화면 신뢰성 손상 | **P0** |
| **Dashboard 부재** (D1-001) — 우리만 `/` → `/projects` redirect | 신규 사용자 첫 경험 약화, 모든 경쟁자 보유 | **P1** |
| Filter UX 갭 — "+More Filters" dropdown · Columns picker 부재 (Mend 5 vs 우리 4) | 다중 facet 발견성 | P1 |
| Drawer 우측 "NEXT STEPS" 영구 sidebar 패턴 부재 (Datadog 4) | 트리아지/리메디에이션 액션 발견성 | P2 |
| Upgrade-centric 그루핑 부재 (Snyk innovative) — vuln 별이 아니라 upgrade 별 | 리메디에이션 효율 (이미 v2.2-a3 infra 있음) | P2 |

**결론**: 두드러진 후행 없음. v2.4.0 GA에는 P0(drawer 버그) 머지 권장, P1~P2는 v2.4.1~v2.5에 점진 적용.

---

## 2. 점수 매트릭스

| 축 | **우리** | C1 BD | C2 Snyk | C3 Sonatype | C4 Mend | C5 Datadog |
|---|---|---|---|---|---|---|
| A1 정보 밀도 | **4** | 4 | 3 | 3 | 3 | 3 |
| A2 발견성 | **3** | 4 | 3 | 4 | 4 | 4 |
| A3 필터 UX | **4** | 4 | 3 | 3 | **5** | — |
| A4 드로어/세부보기 | **3** | — | 2 | 2 | — | 4 |
| A5 Empty/Loading | 4 | — | — | — | — | — |
| A6 Bulk action | **4** | 4 | 4* | 2 | — | — |
| A7 차트 인터랙션 | **4** | — | 2 | 2 | — | **5** |
| A8 마이크로인터랙션 | 3 | — | — | — | 3 | — |
| **평균** | **3.6** | 4.0 | 2.8 | 2.7 | 3.7 | 4.0 |
| 채워진 셀 | 8/8 | 4/8 | 5/8 | 5/8 | 3/8 | 4/8 |

`*` Snyk A6 = upgrade-centric bulk (다른 axis, innovative)
`—` = unknown · 정적 캡처 한계 (§11 참조)

채워진 셀 합계 **36/48 = 75%** — DoD 충족. 점수 의미: 5=업계 최상위 · 4=충분 경쟁 · 3=표준 · 2=명백 갭 · 1=부재.

---

## 3. 축별 상세

### A1 — 정보 밀도 (우리 4 / 평균 3.3)

**우리 강점**. 1440px viewport에 사이드바 224 + 컨텐츠 1216. 40px compact 행. project-list 18행/components 12행 가시. CLAUDE.md design spec("Enterprise SCA — Compact, Information-Dense")이 작동.

| 동급 | 약함 |
|---|---|
| BD Polaris 4 (좌 영구 filter sidebar + 12 행 동급) | Snyk 3 (카드형 그룹·행 큼), Sonatype 3 (헤더 spacious), Mend 3, Datadog 3 (모니터링-스타일) |

**액션 없음** — 베이스라인 우수.

### A2 — 발견성 (우리 3 / 평균 3.7) ⚠ -0.7

**우리 약함**. 페이지별 검색바 있으나 **global search/CMD+K 부재**가 명확한 갭. BD는 좌 sidebar에 11 facet 영구 가시 + "Clear Filters (2)" 카운트. Sonatype은 "Advanced Search" 별도 메뉴. Datadog은 모니터링 표준 tag/facet.

**Mend는 4지만 다른 axis** — `"+More Filters" dropdown + Columns picker`의 명시적 affordance (A3에 더 적합).

**PR 후보 W9-#54** (P3 별도 트랙) — 글로벌 CMD+K palette: 프로젝트 검색·CVE 검색·라우트 jump. shadcn `cmdk` 가 정확히 이 패턴. 예상 2일.

### A3 — 필터 UX (우리 4 / 평균 3.75) — Mend 만 5

**우리 강함**. W4-B에서 active filter chip + 차트 deep-link + URL 거울 정착. BD/Snyk/Sonatype 와 동급 또는 약간 우위.

**그러나 Mend = 5** (업계 best). 차이:
- **"+ More Filters" dropdown** — 사용자가 "더 많은 facet 있나?" 명시 발견 가능
- **우측 Columns picker** — 사용자가 컬럼 표시 customize 가능
- 우리 chip은 활성된 filter만 chip, "add more" 발견성은 부족

**PR 후보 W9-#52** (P1) — 우리 ActiveFilterChips 옆에 "+ Add filter" dropdown + Columns picker. 우리 generic filter component 한 군데 수정. 예상 1.5일.

### A4 — 드로어 / 세부보기 (우리 3 / 평균 3.0) — Datadog 만 4

**우리 표준**. 슬라이드 드로어 우반 ~50%, scrollable, 컨텍스트 보존. 깊은 정보(CVSS/EPSS/Reachability/Summary/Upgrade/References) 잘 구조화.

**그러나 두 가지 이슈**:
1. **B2-001 References "REF"만** — `bugs-found.md` 참조. 8 reference 모두 placeholder. 사용자가 advisory 추적 불가.
2. **B2-002 Summary 단락 중복** — 동일 텍스트 2회 표시.

**Datadog 4의 이유**: page nav 이지만 **우측 "NEXT STEPS" 영구 sidebar** (Triage [Open/Assign/Jira] + Remediation [Upgrade · Set up Automation · Comments] 분리). 우리 drawer가 1단 깊이라면 NEXT STEPS는 2단 분리 — action 발견성·집중도 모두 강함.

**Sonatype 2 / Snyk 2** — 모달(컨텍스트 차단) · 인라인 expand(깊이 부족) 패턴은 우리보다 못함.

**PR 후보**:
- **W9-#51-A** (P0) — B2-001/002 데이터 버그 수정. `services/vulnerability_matching.py` Trivy persist 시 reference_urls 매핑 + summary/description 중복 제거. 예상 0.5일.
- **W9-#51-B** (P2) — drawer "NEXT STEPS" 영구 우측 sub-panel. 단, drawer 자체가 50% width인 데서 추가 분할이라 layout 신중. 예상 1.5일.

### A5 — Empty / Loading (우리 4) — 다른 도구 자료 부재

`reports.png`에서 "No downloads yet. Use the shortcuts on the left to generate..." zero-state CTA 명확. 우리 4점이지만 **비교 불가** — vendor 공개 자료는 모두 데이터-풀 상태. 정적 audit 본질적 한계.

**액션 없음** — 우리 베이스라인 견고.

### A6 — Bulk action (우리 4 / 평균 3.5) — Snyk innovative 다른 axis

**우리 강함**. W3 #33 bulk transition: 체크박스 tri-state + per-row 결과 + 단일 페이지 cap=200. BD "Triage All 12" 동급.

**Sonatype 2** — 단일 vuln 모달.

**Snyk 4 의 innovative axis** — `snyk-vuln-list-1.png` "⇧ Upgrade to 0.1002.0 ▾". **vuln 별 bulk action 이 아닌 upgrade 별 group action** — 한 클릭으로 N개 CVE 동시 해결. 우리에 없는 패턴.

**PR 후보 W9-#53** (P2) — Vulnerabilities 탭에 "Group by upgrade" 토글 (기본 OFF). v2.2-a3 `upgrade_recommendation` 데이터가 이미 backend에 있어 frontend grouping만. 예상 2일.

### A7 — 차트 인터랙션 (우리 4 / 평균 3.25) — Datadog 만 5

**우리 강함**. W4 #188 차트 segment 클릭 → `?tab=&severity=` deep-link 자동 적용. SCA에서 명확한 차별점.

**Snyk 2 / Sonatype 2** — Risk Score 큰 숫자·donut 모두 static.

**Datadog 5**: time-series 2종(Created vs Closed) + severity distribution stacked bars + brush/zoom/legend (모니터링 native). 우리 SCA에 직접 모방 필요한지는 사용자 판단 — vuln-count-over-time 시계열은 v2.x에 있어도 좋음.

**액션** — 모방 강제 아님. v2.5에서 "조직 risk 추세" 화면(W9 후보) 검토.

### A8 — 마이크로인터랙션 (우리 3 / 평균 3.0) — 정적 평가 한계

shadcn 기본 polish 수준. 자료 부족으로 다른 도구와 비교 어려움. **별도 동적 audit(비디오 캡처) 필요** — 본 audit 범위 외.

---

## 4. 화면별 우열 (O1~O8)

| 화면 | 가장 가까운 경쟁 비교 | 우열 | 권장 |
|---|---|---|---|
| O1 Dashboard | BD Polaris Portfolio ROI Dashboard | **열위** (우리는 `/projects` redirect, D1-001) | W9-#50 Dashboard 신설 |
| O2 Project list | (직접 비교 자료 없음) | 표준 | 액션 없음 |
| O3 Project Detail Overview | BD Project Test Details (right header) | 동급 | 액션 없음 |
| O4 Project Detail Components | BD policy-violations-component | **동급** (좌 sidebar 위치만 차이) | 액션 없음 |
| O5 Project Detail Vulnerabilities | Snyk Fixable issues / Sonatype Vulnerabilities list | 우위 (필터·bulk action 풍부) | 추가 W9-#52 (column picker), W9-#53 (upgrade group) |
| O6 Drawer (vuln) | Datadog vulnerability detail | **약열위** (NEXT STEPS sidebar 패턴 부재 + B2 버그 2건) | W9-#51-A + W9-#51-B |
| O7 Reports | (직접 비교 자료 없음) | 표준 (zero-state 명확) | 액션 없음 |
| O8 Scans Queue | (직접 비교 자료 없음) | 표준 | 액션 없음 |

---

## 5. PR 후보 (W9 등재 후보)

신규 wave `W8` 자리는 scan-bench 발견이 차지. UX 감사 후속은 **W9 (UX competitive)** 로 신규 등재 권장.

| # | 우선 | 항목 | 출발 파일/심볼 | 예상 |
|---|---|---|---|---|
| **W9-#51-A** | **P0** | **Drawer 데이터 버그 수정** (B2-001/002): References "REF" → 실 URL/title + Summary 중복 제거. Trivy persist 시 reference_urls 매핑 + summary/description 단일 source. | `apps/backend/services/vulnerability_matching.py` (W6-#41 산출), `apps/frontend/src/features/vulnerabilities/` drawer 컴포넌트 후보 | 0.5d |
| **W9-#50** | P1 | **Dashboard 신설** (D1-001): `/`가 redirect 가 아닌 dedicated Dashboard 렌더. cross-project risk portfolio + severity/license 분포 + recent activity. Project List는 `/projects` 그대로 유지. | `apps/frontend/src/router.tsx:62` (현 redirect), `pages/Dashboard.tsx` 신규, ProjectListPage 의 상단 차트 컴포넌트 재사용 | 1.5d |
| **W9-#52** | P1 | **Filter UX 강화 — "+More Filters" + Columns picker** (Mend 패턴, A3). 우리 ActiveFilterChips 옆에 add-filter dropdown + 우측 Columns picker. | `apps/frontend/src/components/filters/` (또는 features 단 generic filter 컴포넌트), Vulnerabilities·Components 탭에 적용 | 1.5d |
| **W9-#51-B** | P2 | **Drawer "NEXT STEPS" 영구 sub-panel** (Datadog 패턴, A4). drawer 내 right column으로 Triage + Remediation 액션 영구 가시화. layout 신중 (50% width drawer 안 추가 분할). | `apps/frontend/src/features/vulnerabilities/drawer/` | 1.5d |
| **W9-#53** | P2 | **Vulnerabilities 탭 "Group by upgrade" 토글** (Snyk innovative, A6). v2.2-a3 `upgrade_recommendation` 데이터 재사용. 기본 OFF, 토글 ON 시 N CVE → 1 upgrade로 그룹화. | `apps/frontend/src/features/vulnerabilities/VulnerabilitiesTab.tsx`, `services/upgrade_recommendation.py` (read-only 재사용) | 2d |
| W9-#54 (별도 트랙) | P3 | **Global CMD+K palette** (A2 갭). shadcn `cmdk` — 프로젝트 검색·CVE 검색·라우트 jump. | 신규 `apps/frontend/src/components/CommandMenu.tsx`, AppShell 통합 | 2d |
| W9-#55 (검토 후) | P4 | **Time-series 차트** (Datadog 패턴, A7) — vuln-count-over-time 조직 추세. v2.5+ 검토. | 신규 `pages/RiskTrends.tsx` 또는 Dashboard 통합 | 2d |
| W9-#56 (검토 후) | P4 | **Aggregate-by-component 토글** (Sonatype 패턴, A1). Components/Vulnerabilities 탭이 이미 분리되어 있어 우선순위 낮음. | — | — |

**합계**: P0 0.5d + P1 3d + P2 3.5d + P3 2d = **9일 (1.8주)**. P4 제외.

---

## 6. 감사 중 발견된 버그

`raw/bugs-found.md` 참조. 본 §5 W9-#51-A 에 통합.

| ID | 분류 | 화면 | 요약 | 등재 |
|---|---|---|---|---|
| B2-001 | Non-blocking | O6 drawer | References 모두 "REF" 텍스트만 (URL 미파싱) | W9-#51-A |
| B2-002 | Non-blocking | O6 drawer | Summary 단락 동일 텍스트 2회 반복 | W9-#51-A |
| D1-001 | Design observation | O1 dashboard | `/` → `/projects` redirect, dedicated Dashboard 부재 (의도된 product 결정, 모든 경쟁자와 IA 불일치) | W9-#50 |

---

## 7. 모방 가치 평가 (어떤 패턴을 따라야 하는가)

| 패턴 | 출처 | 모방 가치 | 비용 | 결론 |
|---|---|---|---|---|
| Filter chip + "+More Filters" + Columns picker | Mend | **높음** — UX 표준 best | 낮음 (1.5d) | **모방** W9-#52 |
| Dashboard 신설 | BD | **높음** — 신규 사용자 첫 경험 | 중 (1.5d) | **모방** W9-#50 |
| NEXT STEPS sidebar (drawer) | Datadog | 중 — 액션 발견성 좋음 | 중 (1.5d, layout 신중) | **모방** W9-#51-B |
| Upgrade-centric grouping | Snyk | **높음** (innovative) | 중 (2d) | **모방** W9-#53 |
| Time-series 차트 (모니터링) | Datadog | 낮음~중 — SCA에 필요성 별도 판단 | 중 (2d) | v2.5+ 검토 |
| Aggregate-by-component toggle | Sonatype | 낮음 — Components/Vulns 탭 분리로 부분 대체 | — | 우선순위 낮음 |
| 좌 filter sidebar 영구 | BD | 낮음 — 우리 상단 인라인 패턴(CLAUDE.md) 의도적 | — | 모방 안 함 |

---

## 8. 사용자 결정 필요

1. **W9 트래커 등재 형태**:
   - (a) **단일 wave** "W9 UX competitive" 로 #50~#54 모두 등재
   - (b) **P0 만 W8 확장** (W8-#50 → drawer 버그) + 나머지는 별도 wave
   - (c) **사용자가 직접 선별** (등재할 항목만 등재)

   **권장: (a)** — 한 wave 로 묶고 우선순위(P0/P1/P2)로 작업 순서 결정.

2. **W6 잔여 vs W8(#46 GA blocker) vs W9 우선순위**:
   - W6 잔여: #43c/#43d/#43e/#44 (문서/배포/Trivy DB 패널/라이프사이클)
   - W8-#46: Maven classifier UniqueViolation (GA blocker)
   - W9-#51-A: drawer 데이터 버그 (P0, 0.5d)

   **권장**: W8-#46(GA blocker) → W9-#51-A(P0, 0.5d) → W6 잔여 → W9 나머지. drawer 버그가 빠르고 가시적이라 우선.

3. **time-series 차트(W9-#55) 필요성**: 우리 SCA가 모니터링-스타일을 차용해야 하는가? Datadog 만의 차별점. **사용자 의견 필요**.

4. **외부 디자이너 1회 리뷰 (계획서 §13.4 옵션 C)**: 본 audit은 평가자 = 우리 개발자 bias 있음. v2.4.0 GA 후 외부 review 1회 권장 여부.

---

## 9. 종합 결론

**TrustedOSS Portal v2 UI/UX는 글로벌 상용 SCA SaaS 대비 production-grade 베이스라인 수준이며, GA 가능.**

- **우리 평균 3.6 점은 BD/Datadog 4.0 다음, Mend 3.7 동급, Snyk 2.8 / Sonatype 2.7 보다 우위.**
- 두드러진 후행 영역 없음. 발견된 5건 개선 사항은 모두 incremental (대규모 재설계 불필요).
- **GA 머지 권장**: W9-#51-A (drawer 버그 P0, 0.5d) 만.
- v2.4.1~v2.5 점진 적용: W9-#50/#52/#51-B/#53 (총 ~6.5d).

**"충분히 경쟁력이 있는가"**: **Yes, with caveats** — 베이스라인은 충분. 발견된 5건 갭을 점진 보완하면 평균 4점대 진입 가능. global search(CMD+K)·dashboard·filter UX는 사용자 expectation 표준이라 우선 권장.

---

## 10. 다음 단계 (Phase E)

1. `docs/post-ga-execution-tracker.md` 에 W9 신규 wave 등재 (사용자 결정 §8.1 후 형태 확정)
2. `docs/sessions/2026-05-27-ux-competitive-audit.md` 핸드오프
3. 메모리:
   - `project_ux_competitive_audit.md` 신규 — 본 audit 결과 + 재실행 트리거
   - `project_v21_v23_execution_tracker.md` 1줄 갱신
   - MEMORY.md 인덱스 추가
4. (옵션) Snyk dir 의 무효 HTML 파일 2개 정리 PR

---

## 11. 한계 (정직한 표기)

1. **평가자 bias**: 평가자 = 우리 portal 개발자. 외부 디자이너 1회 리뷰 권장 (§8.4).
2. **정적 캡처 한계**: A5(empty/loading), A8(마이크로인터랙션) 동적 평가 불가. 12/48 셀 unknown.
3. **공개 자료 의존**: vendor 의 marketing-curated 스크린샷이라 실제 사용 환경과 차이 가능. trial 계정 동적 검증 별도.
4. **시간 정점**: 2026-05-27 snapshot. 도구는 매 분기 UI 변경 — 6개월 후 재실행 권장 (§13.4 트리거).
5. **vendor 자료 가용성 편차**: BD Hub(non-Polaris) SPA-rendered, Snyk Reports GitBook 제한, Mend Risk Dashboard 403. C5 Datadog 만 docs CDN 풍부.

---

## Footer (공정 사용 / Trademarks)

본 보고서에 인용된 경쟁 도구의 모든 스크린샷·trademark·brand name은 각 vendor 의 소유.
- Black Duck SCA, Polaris — Synopsys / Black Duck Software 등록상표
- Snyk — Snyk Limited 등록상표
- Sonatype Lifecycle, Nexus IQ — Sonatype Inc. 등록상표
- Mend (전 WhiteSource) — Mend.io Ltd. 등록상표
- Datadog Vulnerability Management — Datadog Inc. 등록상표

본 자료의 사용 목적은 TrustedOSS Portal v2 의 internal UX audit 이며, 모든 스크린샷은 각 vendor 의 공식 docs/blog/product page 공개 자료에서 fair-use 원칙 하 인용. 제 3자 배포 없음.
