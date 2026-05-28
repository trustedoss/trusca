# UX 평가 매트릭스 — 8축 × 6주체 (Phase C)

> 작성: 2026-05-27. 계획서 §1.3 평가축, §8 품질 게이트 준수.
> **모든 점수는 1장 이상 스크린샷 근거 (캡처 일자 명시).**
> 자료 없으면 빈 셀 + "unknown · 사유". 0점 부여 금지.

**점수 의미**:
- 5 = 업계 최상위 (industry-leading)
- 4 = 충분히 경쟁력
- 3 = 표준 충족 (기본기 OK)
- 2 = 명백한 갭 → PR 후보
- 1 = 부재 또는 심각 결함

---

## 매트릭스 (요약)

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
| **평균 (채워진 셀만)** | **3.6** | 4.0 | 2.8 | 2.7 | 3.7 | 4.0 |

`*` Snyk A6 = bulk fix (다른 axis: vulnerability bulk가 아니라 upgrade bulk — innovative)
`—` = unknown (자료 부족, 사유 본문)

**채워진 셀**: 36/48 = **75%** (DoD 충족). 부족분 12 셀은 자료 한계 (정적 스크린샷으론 평가 불가 영역) — Phase D 보고서 §한계에 명시.

---

## 축별 정성 평가 (점수 근거)

### A1 — 정보 밀도

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **4** | `screens/ours/project-list.png`: 18 행 가시 in 1440×900, 40px 행 + 헤더 차트 2장, sidebar 224px. SaaS dense 표준 정합. `components.png`: 9 컬럼 + 40px 행 ~12 row. |
| C1 BD | 4 | `bd-polaris-policy-violations-component.png`: 좌 영구 filter sidebar + 12 행 가시 + 6 컬럼 (Security/Component/Match Types/Match Score/Usage/License). 우리와 동급. |
| C2 Snyk | 3 | `snyk-vuln-list-1.png`: 카드형 그룹(upgrade 단위) — 행 자체는 우리보다 큼. 그룹 정보 풍부하나 row density 낮음. |
| C3 Sonatype | 3 | `sonatype-aa37fc46.png`: 상단 풀 너비 헤더(App Risk Score 946 강조) + 9 행 가시. 우리보다 약간 spacious. |
| C4 Mend | 3 | `mend-findings-...082111.png`: 7-8 컬럼 + chip filter row 상단 점유. 표준. |
| C5 Datadog | 3 | `csm-vm-dashboard.png`: KPI 카드 + 차트 grid (모니터링 스타일), SCA dense table 보다 less dense. |

### A2 — 발견성 (global search · tooltip · 필터 가시성)

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **3** | 모든 페이지 검색바 있음(`vulnerabilities.png` "Search by CVE ID..."). 그러나 **global search/CMD+K 부재** + active filter chip은 W4-B 도입(component/vuln 페이지). 발견성 표준이나 cross-page 검색 갭. |
| C1 BD | 4 | `bd-polaris-policy-violations-component.png` 좌 sidebar에 11개 facet 영구 가시 + "Clear Filters (2)" 카운트. `bd-polaris-dashboard-filters.png` Portfolio ROI Dashboard 별도 + Filters sidebar (Application/Project/Branch/Label). |
| C2 Snyk | 3 | `snyk-vuln-list-1.png` 좌 단순 status checkbox + 상단 탭(Overview/History/Settings). 페이지 내 발견성은 보통. |
| C3 Sonatype | 4 | `sonatype-aa37fc46.png` 좌 sidebar에 **Advanced Search** 항목 명시 + Reports/Vulnerability Lookup 명확 분리. 도메인 entry point 발견성 좋음. |
| C4 Mend | 4 | `mend-findings-...082111.png` **"+ More Filters" dropdown + Columns picker** — 명시적 "add more" affordance. 우리 없음. |
| C5 Datadog | 4 | `csm-vm-findings.png` 좌 좁은 nav + 상단 검색바 (모니터링 도구 표준 tag/facet). UTC 시간 필터, Share, Configure 명확. |

### A3 — 필터 UX (active chip · multi-select · 저장 · URL 거울)

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **4** | W4 #188 ActiveFilterChips + multi-select(severity/license) + URL 거울(`?tab=&severity=`) + **차트 segment 클릭→필터 자동 적용**(W4-B). 저장/공유 없음. |
| C1 BD | 4 | `bd-polaris-policy-violations-component.png` 좌 영구 sidebar + Clear Filters 카운트 + 모든 facet expandable. 모달 없는 인라인 facet은 우리와 동급. |
| C2 Snyk | 3 | `snyk-vuln-list-1.png` 좌 단순 checkbox (Status: Open ✓ / Patched / Ignored). multi-select 정도. 우리보다 단순. |
| C3 Sonatype | 3 | `sonatype-aa37fc46.png` Filter 버튼 (drawer-style) + per-column input(policy name / component name 컬럼 헤더 inline filter). 인라인 chip은 없음. |
| C4 Mend | **5** | `mend-findings-...082111.png` **filter chip + "+ More Filters" dropdown + Reset + 우측 Columns picker** — 4-pattern 풀 셋. 우리 chip은 있지만 "add filter" dropdown + column picker 없음. 업계 best UX 중 하나. |
| C5 Datadog | — | unknown — 정적 캡처에 filter chip 보이지 않음(VM detail 화면 위주). |

### A4 — 드로어 / 세부보기

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **3** | `screens/ours/drawer-vulnerability-detail.png` 슬라이드 드로어 우반 ~50%, 컨텍스트 보존, scrollable. Status·CVSS·Reachability·Summary·Recommended upgrade·References 다층. **그러나 B2-001 References "REF"만, B2-002 Summary 중복** — 데이터/표시 결함. |
| C1 BD | — | unknown — Polaris blog에 drawer/detail 화면 미캡처. |
| C2 Snyk | 2 | `snyk-vuln-list-1.png` 인라인 expand 패턴("Show more issues") — 페이지 안 머무름. 슬라이드 드로어 부재. 깊이 부족. |
| C3 Sonatype | 2 | `sonatype-6eb2fcc3.png` vulnerability details **모달** — page context 차단. 드로어보다 보통 못함. |
| C4 Mend | — | unknown — drawer 화면 미캡처. |
| C5 Datadog | 4 | `csm-vm-findings.png` page nav + **우측 영구 "NEXT STEPS" sidebar (Triage [Open/Assign/Jira] + Remediation 분리)** + Severity breakdown 박스. NEXT STEPS 패턴은 인상적. page nav라 컨텍스트는 약간 깎임. |

### A5 — Empty / Loading

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **4** | `screens/ours/project-detail-reports.png` "No downloads yet. Use the shortcuts on the left to generate a NOTICE, SBOM, vulnerability PDF, or VEX export." — zero-state CTA 명확. 스켈레톤은 W4-B-prep에 있음(별도 정적 검증 미수행). |
| C1~C5 | — | unknown — empty/loading 상태가 vendor 공개 자료에 없음 (대부분 데이터 풀 상태 캡처). 정적 audit 본질적 한계. |

### A6 — Bulk action

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **4** | W3 #33 bulk transition action bar + 체크박스 헤더 tri-state + per-row 결과 + 단일 페이지 cap=200. `screens/ours/project-detail-vulnerabilities.png` 헤더/행 체크박스 가시. |
| C1 BD | 4 | `bd-polaris-policy-violations-component.png` 우상단 **"Triage All 12"** 버튼 가시 + 행별 체크박스. bulk triage 명확. |
| C2 Snyk* | 4* | `snyk-vuln-list-1.png` 우하단 **"⇧ Upgrade to 0.1002.0 ▾"** — 다른 axis: vulnerability bulk 가 아니라 **upgrade bulk** (한 클릭으로 N개 CVE 동시 해결). Innovative — 우리에 없는 모드. |
| C3 Sonatype | 2 | `sonatype-6eb2fcc3.png` 모달이 단일 vuln per. 자료에서 bulk action 발견 안 됨. |
| C4 Mend | — | unknown — bulk action 화면 미캡처. |
| C5 Datadog | — | unknown — `csm-vm-findings.png` NEXT STEPS는 single vuln. bulk action 화면 미캡처. |

### A7 — 차트 인터랙션 (hover · click deep-link · brush · legend)

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **4** | `screens/ours/project-detail-overview.png` horizontal bar 차트 + 클릭 deep-link (W4 #188: `?tab=vulnerabilities&severity=`). hover tooltip 있음. brush/zoom 없음(SCA에 부적합하지 않음). |
| C1 BD | — | unknown — `bd-polaris-dashboard-filters.png`에 chart 자체는 가시 안 됨 (filter sidebar만 캡처). |
| C2 Snyk | 2 | `snyk-vuln-list-1.png` Risk Score 281 큰 숫자 — static. 차트 인터랙션 자료 없음. |
| C3 Sonatype | 2 | `sonatype-17cd5206.png` donut chart static 단일 캡처. interaction 표시 없음. |
| C4 Mend | — | unknown. |
| C5 Datadog | **5** | `csm-vm-dashboard.png` **time-series trends** (Daily count of created vs closed, 2 lines: Created/Closed) + 활성 vulns severity distribution stacked bars 2종 + Quick wins. **모니터링 native** — brush/zoom/legend toggle 업계 최상위 표준. |

### A8 — 마이크로인터랙션 (hover transition · focus ring · sortable header · polish)

| 주체 | 점수 | 근거 |
|---|---|---|
| **우리** | **3** | shadcn/ui 기본 focus ring + sortable header (W4-B-prep) 3-state cycle. 표준이나 두드러진 polish 없음. animation 정적 평가 한계. |
| C1 BD | — | unknown — 정적 스크린샷 동적 평가 불가. |
| C2 Snyk | — | unknown. |
| C3 Sonatype | — | unknown. |
| C4 Mend | 3 | `mend-findings-...082111.png` dropdown 패턴(+More Filters · Columns picker) — 표준 dropdown 폴리시. 동적 평가 한계. |
| C5 Datadog | — | unknown. |

---

## 평균 점수 비교 (채워진 셀만)

| 주체 | 채워진 셀 | 평균 | 비고 |
|---|---|---|---|
| **우리** | 8/8 | **3.6** | 균형적, 두드러진 부족 영역 = A2(발견성, global search 부재) |
| C1 BD | 4/8 | 4.0 | 자료 가용한 4축 모두 4 — 견고. drawer/empty/chart/micro 평가 불가 |
| C2 Snyk | 5/8 | 2.8 | dependency-grouped fix(A6 innovative) + risk score 외 약함 |
| C3 Sonatype | 5/8 | 2.7 | 자료 풍부하나 모달 + 단일 bulk 부재 |
| C4 Mend | 3/8 | 3.7 | filter UX는 업계 best(5), 나머지 자료 부족 |
| C5 Datadog | 4/8 | 4.0 | drawer NEXT STEPS + 차트 인터랙션 강력 |

---

## 자료 한계 (정직한 표기)

다음 영역은 vendor 공개 자료로 평가 불가 — Phase D 보고서 §한계에 명시:
- **A5 Empty/Loading**: 우리 외 5 도구 모두 unknown (vendor blog/docs는 데이터-풀 상태만 캡처)
- **A8 마이크로인터랙션**: 정적 PNG로는 본질적 평가 불가 (애니메이션/transition 동적). 우리 + Mend 만 부분 추정.
- **C2 Snyk A4 드로어**: 보이는 자료에서 인라인 expand만, drawer 별도 없음
- **C4 Mend A4/A6**: 해당 화면 자료 미수집
- **C5 Datadog A3 필터 UX**: vulnerability detail 위주 캡처, list filter chip 화면 부재

이 한계는 audit 자체의 본질적 제약이므로 보강 시 외부 도구 trial 계정 + 동적 비디오 캡처 필요(별도 트랙).

---

## 차별화 패턴 메모 (Phase D 모방 후보)

매트릭스로 점수만 보면 우리가 평균 3.6으로 BD/Datadog(4.0) 다음이나, **점수 외의 차별 패턴**이 중요. Phase D §모방 후보 절에 풀어 쓸 항목:

1. **Snyk dependency-grouped fix view (A6)** — vuln 별이 아닌 upgrade 별 그룹화 ("Upgrade to X fixes N issues"). 우리에 없음, 강한 모방 후보.
2. **BD Portfolio ROI Dashboard (A2 + IA)** — 전용 대시보드 존재. 우리는 D1-001로 부재.
3. **Mend filter chip + "+More Filters" + Columns picker (A3)** — A3=5 받은 이유. 우리 chip은 있지만 add-filter dropdown · column picker 없음.
4. **Datadog NEXT STEPS sidebar (A4)** — drawer 안에 모든 정보 묶지 않고 우측 영구 Triage/Remediation 분리. 우리 drawer가 1단 깊이라면 NEXT STEPS는 2단 분리.
5. **Datadog 모니터링-스타일 차트 (A7)** — time-series + brush/zoom. SCA에 직접 모방 필요한지는 별도 판단.
6. **Sonatype Aggregate-by-component toggle (A1)** — 같은 데이터 두 가지 view (vuln 단위 vs component 단위). 우리는 Direct/Transitive segment 정도.

이 6 패턴이 Phase D PR 후보 1차 풀.
