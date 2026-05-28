# 핸드오프 — Design Philosophy 진화 결정 (2026-05-27)

> 양식: `docs/v2-execution-plan.md` §7. UX competitive audit 후속.

## 한 줄 요약
Audit §11 디자인 철학 5갭 중 **#5 시각(BD→modern enterprise) + #3 드로어 도그마**를 HIGH 우선순위로 선별. W10/W11 신규 wave 등재. **옵션 C 혼합 (Vercel base + Linear polish)**, light 단일, 외부 디자이너 없음, 사용자 본인 confirm 모델.

## 사용자 결정 (계획서 §9.1)
| Q | 답변 |
|---|---|
| W10/W11 분리 + W10 → W11 순서 | ✅ 동의 |
| 시각 톤 1차 추천 Linear-influenced | ✅ → reference 9장 시각 비교 후 **옵션 C (Vercel base + Linear polish)** 로 확정 |
| Dark mode | ✅ **제외** (light 단일, v2.5+ 별도 트랙) |
| 외부 디자이너 1회 리뷰 | ✅ 없음 |
| 사용자 시각 테스트 | ✅ 없음 (본인 confirm) |
| Reference set 추가 도구 | ✅ 없음 (Linear 4 + Vercel 5 충분) |
| W11 Phase B 첫 prototype 화면 | ✅ Project List (`/projects`) |

## 핵심 통찰 — Linear-influenced 와 Dark mode 충돌
Linear의 시각적 정체성은 dark-native 와 깊이 얽힘. 사용자 reference 5장 모두 dark. **light 단일 결정과 그대로 모방 불가**.

→ 옵션 C 혼합으로 우회:
- light 색 토큰 / 배경·border·surface / 좌 nav / dense row / status badge = **Vercel deployments-1 풍** (`docs/ux/reference/vercel-deployments-1.png`)
- typography hierarchy (semibold heading)·focus ring·hover transition·음영 elevation·dropdown polish = **Linear 풍** (light 재해석)
- 두 reference 충돌 시 우선순위: light 시각은 Vercel, micro polish는 Linear
- Linear의 dark-only 시각 요소는 흡수 안 함

## 산출물
| 파일 | 용도 |
|---|---|
| `docs/ux/design-philosophy-evolution-plan-2026-05-27.md` | SoT 계획서 (사용자 결정 반영 완료, §4.1 reference set 확정, §4.5 Phase B = Project List) |
| `docs/ux/reference/` | 9 PNG (Linear 4: dashboard-1/2, feature, roadmap · Vercel 5: deployments-1, domains, deployment-detail, dashboard, projects · 3 archive .bak) |
| `docs/post-ga-execution-tracker.md` | 대시보드 + W10/W11 본문 등재 |
| `docs/sessions/2026-05-27-design-philosophy-evolution-decision.md` | 본 핸드오프 |
| 메모리 `project_design_philosophy_evolution.md` | 결정 + 가이드라인 + 위험 + 산출물 |
| MEMORY.md 인덱스 1줄 추가 | |
| `project_v21_v23_execution_tracker.md` 1줄 갱신 | W10/W11 등재 명시 |

## W10 / W11 등재 (`docs/post-ga-execution-tracker.md` §0.5)

### W10 — 드로어 dual surface (4.5d, post-GA 먼저)
6 phase A~F. W9-#51-A (drawer 버그 P0) + W9-#51-B (NEXT STEPS sidebar P2) **자연 흡수** → 별도 PR 불필요.
- 산출: `apps/frontend/src/features/{vulnerabilities,components}/` 에 `DetailBody` 추출 + page nav 신규 + 양방향 affordance
- 선행: W6 잔여 권장, W8-#46 병렬 가능
- URL backward-compat: 기존 `?vuln=` 유지

### W11 — 시각 정체성 재정의 (11d, post-W10)
8 phase A~H. light 단일. 사용자 confirm 게이트 3곳 (Phase A 토큰·Phase B Project List prototype·Phase E 8화면 검증).
- 위치: v2.5 또는 v2.4.x patch series
- 선행: W10 완료 권장 (drawer body 분리 후 신 토큰 적용 자연스러움)
- CLAUDE.md §디자인 시스템 갱신은 W11 완료 시점에

## 다음 세션 — 실행 시작 권장 순서
1. **W8-#46** (Maven classifier P0, GA blocker) — 1.5d
2. **W6 잔여** (#43c → #43d → #43e → #44) — ~3.5d
3. **v2.4.0 GA**
4. **W10** (드로어 dual surface) — 4.5d
5. (병행 가능) W9-#52 (filter+columns picker), W9-#54 (CMD+K)
6. **W11** (시각 정체성) — 11d (~2.2주)
7. (W11 후) W9-#50 (Dashboard 신설 — W11 신 톤 적용된 신규 화면)

v2.4.0 GA 후 ~4.5주에 design philosophy 진화 + W9 잔여 모두 종결.

## 비포함 (defer)
| audit §11 갭 | 결정 | 비고 |
|---|---|---|
| #1 Risk-as-trajectory (time-series) | defer | 사용자 "당장 불필요" |
| #2 Guided 모드 (newcomer onboarding) | 미언급 → 향후 별도 계획 | |
| #4 Prioritization-First (data→next-action 합성) | 미언급 → 향후 별도 계획 | (Snyk upgrade-group W9-#53 부분 대체) |
| Dark mode | defer | v2.5+ 별도 트랙. W11에서 forward-compat 토큰 구조로 작성 |

## 운영 검증 후속
- W10 완료 시점: page nav 사용성 측정 (drawer-only flow 와 비교)
- W11 Phase E 시점: capture-ours spec 재실행 (`cd apps/frontend && npx playwright test ux-audit/capture-ours`) → 8 화면 before/after PNG 비교셋 사용자 confirm

## 메모리 업데이트 후보
- `project_design_philosophy_evolution.md` ✅ 신규 작성
- `project_v21_v23_execution_tracker.md` ✅ 1줄 갱신
- MEMORY.md ✅ 인덱스 추가
- `project_ux_competitive_audit.md` — W10/W11 등재 후속 1줄 추가 권장 (다음 세션)
