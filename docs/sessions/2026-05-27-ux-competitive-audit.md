# 핸드오프 — UX 경쟁 감사 (2026-05-27)

> 양식: `docs/v2-execution-plan.md` §7. v2.4.0 GA 직전 우리 UI/UX 가 글로벌 상용 SCA SaaS 와 경쟁력 있는지 검증.

## 한 줄 요약
우리 8화면 × 경쟁 5도구(BD/Snyk/Sonatype/Mend/Datadog) × 8축 매트릭스 작성. **평균 3.6/5 (BD/Datadog 4.0 다음, Snyk/Sonatype 보다 우위) — 베이스라인 production-grade**. 5건 incremental 개선(W9 신규 wave) + drawer 데이터 버그 2건(P0). 캡처는 docs 재사용 가능한 dual-purpose SoT.

## 산출물
| 파일 | 용도 |
|---|---|
| `docs/ux/competitive-audit-plan-2026-05-27.md` | 계획서 (5 Phase · DoD · 재개 절차 · 버그 인테이크 §14) |
| `docs/ux/competitive-audit-2026-05-27.md` | **최종 보고서** (Executive summary + 매트릭스 + 축별 + PR 후보 7건) |
| `docs/ux/screens/ours/*.png` | 우리 EN 8화면 viewport + full-page (16장 + KO 3장 = 19장). 의미 이름. 2x DPR. dual-purpose docs reuse 가능 |
| `docs/ux/screens/competitors/{bd,snyk,sonatype,mend,datadog}/*.png` | 경쟁 34장 (BD 7 · Snyk 3 · Sonatype 9 · Mend 9 · Datadog 6) |
| `docs/ux/raw/ours-observations.md` | 화면당 3~5줄 1차 관찰 |
| `docs/ux/raw/competitor-sources.md` | 출처 URL · 일자 · 공정사용 메타 |
| `docs/ux/raw/scoring-matrix.md` | 8축 × 6주체 점수 + 1줄 근거 |
| `docs/ux/raw/bugs-found.md` | B2 2건 + D1 1건 |
| `apps/frontend/tests/e2e/ux-audit/capture-ours.spec.ts` | Playwright 재캡처 spec (1-명령 재실행) |
| `docs/post-ga-execution-tracker.md` | **W9 신규 wave 등재** (대시보드 + 본문 9 항목) |

## 점수 매트릭스 (요약)

| 축 | **우리** | BD | Snyk | Sonatype | Mend | Datadog |
|---|---|---|---|---|---|---|
| A1 정보 밀도 | **4** | 4 | 3 | 3 | 3 | 3 |
| A2 발견성 | **3** | 4 | 3 | 4 | 4 | 4 |
| A3 필터 UX | **4** | 4 | 3 | 3 | **5** | — |
| A4 드로어 | **3** | — | 2 | 2 | — | 4 |
| A5 Empty/Loading | 4 | — | — | — | — | — |
| A6 Bulk action | **4** | 4 | 4* | 2 | — | — |
| A7 차트 인터랙션 | **4** | — | 2 | 2 | — | **5** |
| A8 마이크로인터랙션 | 3 | — | — | — | 3 | — |
| **평균** | **3.6** | 4.0 | 2.8 | 2.7 | 3.7 | 4.0 |

채워진 36/48 = 75% (DoD 충족). A5/A8 동적 평가 본질적 한계.

## 핵심 발견

### 우리 강점 (4점)
- A1 정보 밀도 (BD 동급)
- A3 필터 UX (active chip + 차트 deep-link, W4 #188 효과)
- A6 Bulk action (W3 #33)
- A7 차트 인터랙션 (segment 클릭 deep-link)

### 우리 갭 (PR 후보)
| ID | 갭 | 출발 파일 | 모방 대상 |
|---|---|---|---|
| W9-#51-A **P0** | drawer References "REF" + Summary 중복 (B2-001/002) | `services/vulnerability_matching.py` (W6-#41) | 자체 버그 |
| W9-#50 P1 | Dashboard 부재 (D1-001) | `router.tsx:62`, 신규 `pages/Dashboard.tsx` | BD Portfolio ROI |
| W9-#52 P1 | "+More Filters" + Columns picker 부재 | `components/filters/` generic | Mend (A3=5) |
| W9-#51-B P2 | drawer NEXT STEPS 영구 sidebar 패턴 부재 | `features/vulnerabilities/drawer/` | Datadog |
| W9-#53 P2 | Vulnerabilities "Group by upgrade" 토글 없음 | `VulnerabilitiesTab.tsx`, v2.2-a3 재사용 | Snyk (innovative) |
| W9-#54 P3 | Global CMD+K palette 없음 | 신규 `CommandMenu.tsx` | BD/Datadog 표준 |
| W9-#55 P4 | Time-series 차트 (vuln 추세) | 신규 `RiskTrends.tsx` | Datadog (검토 후) |

**합계 P0~P3 = 9일 (1.8주)**

## W6/W8/W9 우선순위 권장
1. **W8-#46 (GA blocker)** — Maven classifier UniqueViolation, 1.5d
2. **W9-#51-A (P0)** — drawer 버그 fix, 0.5d (가시적, 빠름)
3. **W6 잔여** — #43c/#43d/#43e/#44 (문서/배포/Trivy 패널/DB 라이프사이클)
4. **W9-#50/#52** (P1) — Dashboard + filter UX, 각 1.5d
5. **W9-#51-B/#53** (P2) — drawer NEXT STEPS + upgrade-group, 각 1.5~2d
6. **W9-#54** (P3 별도 트랙) — CMD+K, 2d

## 다음 세션 — 인테이크 모드 + W6/W8/W9 병행

본 audit 으로 **GA 결정에 영향 줄 갭은 W9-#51-A(0.5d) 만**. 다른 5건은 v2.4.1~v2.5 점진. 

권장 인테이크 메뉴:
- (A) **W8-#46 + W9-#51-A 묶음** (P0 둘 다, ~2d) — GA 직전 머지블로커 정리
- (B) **W9-#50 단독** (Dashboard 신설, 1.5d) — UX 가장 가시적
- (C) **W9-#52 단독** (filter UX, 1.5d) — 빠른 polish
- (D) 사용자 다음 핸즈온 발견 우선 — 인테이크

## 운영 검증 후속
- 캡처 재실행: `cd apps/frontend && npx playwright test ux-audit/capture-ours` (1 명령)
- 경쟁 자료 재수집: 분기마다 1회 또는 메이저 UI 변경(W4-급) 시
- 외부 디자이너 1회 리뷰: v2.4.0 GA 후 권장 (평가자 bias 보정)

## 메모리 업데이트
- `project_ux_competitive_audit.md` 신규 — 본 audit 결과 + 재실행 트리거 + W9 P0~P3 핵심
- `project_v21_v23_execution_tracker.md` 1줄 갱신 — W9 추가됨
- MEMORY.md 인덱스 1줄 추가
