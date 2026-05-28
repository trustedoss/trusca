# 디자인 철학 진화 계획 (2026-05-27)

> **본 문서는 계획만** — 실행은 사용자 green light 후. 짝 문서:
> [`competitive-audit-2026-05-27.md`](./competitive-audit-2026-05-27.md) (audit 결과) ·
> [`competitive-audit-plan-2026-05-27.md`](./competitive-audit-plan-2026-05-27.md) (audit 운영)

---

## 0. 사용자 결정 (2026-05-27)

audit §11 "Design philosophy 5 갭" 중 사용자가 우선순위를 다음과 같이 선별:

| audit 갭 | 사용자 결정 |
|---|---|
| **#5 Black Duck 스타일 (2015년 미감 벤치마크)** | **HIGH — 개선 필요** |
| **#3 "드로어 = 디테일" 도그마** | **HIGH — 개선 필요** |
| #1 Risk-First인데 시간 축 위험 없음 | **당장 불필요 (defer)** |
| #2 Guided 모드 (newcomer onboarding) | 미언급 — 본 계획 범위 외 |
| #4 Prioritization-First (data → next-action 합성) | 미언급 — 본 계획 범위 외 |

본 계획은 **#3 + #5 두 가지에 집중**. #2/#4 는 향후 별도 계획으로 분리.

---

## 1. 새 디자인 철학 (제안)

### 1.1 유지 (변경 안 함)
- **"Compact, Information-Dense"** — 운영자 전문가 우선 (CLAUDE.md 원안 그대로)
- **"Risk-First"** — point-in-time 위험 framing 유지 (시간 축은 defer)
- **인라인 필터 (모달 X)** — W4 정착 패턴
- **EN/KO 동시** — i18n 정합

### 1.2 변경
| 기존 (CLAUDE.md) | 변경안 |
|---|---|
| Primary `#0f172a` 다크 네이비 (**Black Duck 스타일**) | **모던 엔터프라이즈 톤** — Linear / Vercel / Vanta / Stripe Dashboard 류 |
| 단일 light mode | (유지 — light mode 단일. **Dark mode 본 계획에서 제외**, 2026-05-27 사용자 결정 §0) |
| 폰트 Inter + JetBrains Mono | (유지) — Inter는 이미 모던 |
| **드로어 = 디테일** (모든 상세 보기 우반 슬라이드) | **드로어 = 빠른 확인 / 페이지 nav = 깊은 작업** (이중 surface, 같은 데이터) |
| 40px compact 행 | (유지) — density 정체성 유지하되 여백·타이포 호흡감 조정 |

### 1.3 한 줄 신 철학
> *Compact for the operator · Modern enterprise aesthetic (Vercel base + Linear polish) · Dual surface (drawer + page) for variable depth · Risk-first point-in-time · Light single-theme*

(audit §11 의 "Risk-as-trajectory" / "Prioritization-First" / "Guided for newcomer" 는 향후 진화 후보로 보존, 본 계획 미포함.)

---

## 2. 두 Wave 분할 + 순서

| Wave | 내용 | 예상 | 위험 |
|---|---|---|---|
| **W10 (먼저)** | 드로어 dual surface 아키텍처 (#3) | 3~4d | 중 (URL 라우팅 변경) |
| **W11 (다음)** | 시각 정체성 재정의 (#5) — 토큰 재정의 + 컴포넌트 re-skin (light 단일) | 9~11d (2주) | 큼 (미감 재정의 실패 가능) |

**순서 근거** (W10 → W11):
- W10이 구조적 변경 (URL/컴포넌트 분리). W11이 표면 polish (토큰/색/여백).
- 만약 W11을 먼저 하면 W10 진행 시 모든 컴포넌트 다시 re-skin 필요 → 작업 2배.
- W10 후 분리된 컴포넌트(드로어 body + 페이지 body)에 W11 적용 → 1배.

**선행 조건** (사용자 인테이크 권장 순서):
1. W8-#46 (Maven classifier P0, GA blocker) — 1.5d
2. W9-#51-A (drawer 데이터 버그 P0) — 0.5d
3. W6 잔여 (#43c/#43d/#43e/#44) — 3.5d
4. **W10 (드로어 dual surface)** — 3~4d
5. **W11 (시각 정체성)** — 12~15d
6. (병행 가능) W9 잔여 (#50/#52/#51-B/#53/#54)

W11은 사실상 **v2.5 마일스톤** 또는 **v2.4.x patch series**. v2.4.0 GA에는 포함 안 함 (시간 부족 + 미감 변경은 사용자 표지 변화라 별도 release notes 필요).

---

## 3. W10 — 드로어 dual surface 아키텍처

### 3.1 목표
같은 vuln/component 디테일 데이터를 **두 surface**에서 렌더:
- **drawer**: 우반 슬라이드 (현재 — 빠른 확인용, 컨텍스트 보존)
- **page nav**: dedicated 페이지 (신규 — 깊은 작업용, 여러 탭, 풍부한 컨텍스트)

사용자 선택: drawer 헤더의 *"Open in full view"* affordance → 페이지 nav. 그 반대(페이지에서 drawer로) 없음 (URL 자연스러움 위해).

### 3.2 URL 구조
```
drawer (현재 유지)
  /projects/:id?tab=vulnerabilities&vuln=<cve_id_or_finding_id>
  /projects/:id?tab=components&component=<cv_id>

page nav (신규)
  /projects/:id/vulnerabilities/<finding_id>
  /projects/:id/components/<cv_id>
```

backward-compat: 기존 deep-link (`?vuln=`) 그대로. 새 페이지 nav는 추가 surface.

### 3.3 컴포넌트 구조
```
features/vulnerabilities/
├── VulnerabilityDetailBody.tsx          ← 신규 (shared)
│   - Status / CVSS / EPSS / Reachability / Summary / Upgrade / References / Comments / Audit / Triage history
├── VulnerabilityDrawer.tsx              ← 기존 → body 호출만
└── VulnerabilityDetailPage.tsx          ← 신규 (page nav, 더 풍부한 sidebar/multi-tab)
```

같은 패턴 components/ 에도.

### 3.4 페이지 nav 의 추가 가치 (drawer 와 차별점)
드로어 50% 폭에서 표시 어려운 것을 페이지 nav 에서:
- **multi-tab 깊이**: Description / Reachability / Triage history / Comments / Audit trail / References (각 탭)
- **breadcrumb**: `Projects / fx-maven-node / Vulnerabilities / CVE-2024-45296`
- **우측 영구 sidebar**: Datadog "NEXT STEPS" 패턴 (W9-#51-B 와 통합 — 본 wave 에 흡수)
- **풍부한 References**: 클릭 가능 URL + advisory title (W9-#51-A 와 통합 — drawer 버그 fix 면 페이지에도 정상 표시)

**효과**: W9-#51-A (drawer P0) + W9-#51-B (NEXT STEPS sidebar P2) 가 본 W10에 자연스럽게 합류 → 별도 PR 불필요.

### 3.5 Phase 분할

| Phase | 내용 | 산출 | 예상 |
|---|---|---|---|
| A | `VulnerabilityDetailBody` 추출 (drawer 본문 = 새 shared 컴포넌트 호출만, 기능 동일성 검증) | feature parity test green | 0.5d |
| B | `VulnerabilityDetailPage.tsx` 신규 + router 추가 + breadcrumb | 페이지 nav 접근 가능 | 1d |
| C | drawer 헤더에 "Open in full view" 버튼 + 페이지에서 drawer 로 돌아가기 (close → 이전 화면) | UX 양방향 | 0.5d |
| D | **NEXT STEPS 영구 sidebar** (Datadog 패턴, W9-#51-B 흡수) + **drawer 버그 fix** (W9-#51-A 흡수) | drawer A4 점수 3→4 | 1d |
| E | Components 도 동일 패턴 (`ComponentDetailBody` + `ComponentDetailPage`) | drawer/페이지 parity | 1d |
| F | Playwright 하네스 verb 추가 + 기존 spec 회귀 검증 + 신규 page nav 시나리오 | 회귀 0 | 0.5d |

**합계**: 4.5d (audit 추정 3~4d 보다 약간 보수적, NEXT STEPS sidebar + drawer bug fix 흡수 반영).

### 3.6 DoD
- [ ] drawer 기능 동일성 (회귀 spec 100% pass)
- [ ] 새 페이지 nav 접근 + 데이터 표시 + breadcrumb 정상
- [ ] "Open in full view" 양방향 작동
- [ ] NEXT STEPS sidebar 페이지 에 영구 가시 (Triage + Remediation 분리)
- [ ] drawer References "REF" 버그 fix, Summary 중복 fix (B2-001/002)
- [ ] EN/KO 신규 키 (label · breadcrumb · tab 명) 동시
- [ ] typecheck / lint / vitest / i18n:check / Playwright green

### 3.7 위험 + 대응
| 위험 | 대응 |
|---|---|
| URL 라우팅 변경이 기존 deep-link 깨뜨림 | 기존 `?vuln=` 유지 + 새 페이지 nav 추가만. Backward-compat 명시. |
| 드로어 vs 페이지에서 같은 데이터 표시 갈라짐 | Phase A 의 `DetailBody` shared 컴포넌트가 single source. 분기 0. |
| Phase D의 NEXT STEPS sidebar 가 narrow drawer 에서 깨짐 | drawer 에서는 NEXT STEPS 를 inline section 으로 (sidebar 아님), 페이지 에서만 우측 영구 sidebar. |
| 사용자가 페이지 nav 발견 못 함 | drawer 헤더 expand 아이콘 명확 + 첫 발견 시 tooltip 1회 |

---

## 4. W11 — 시각 정체성 재정의

### 4.1 목표 — 사용자 결정 (2026-05-27)
"BD-style 2015년 미감" → **"modern enterprise aesthetic — Vercel base + Linear polish (옵션 C 혼합)"**.

**확정 reference set** (`docs/ux/reference/`, 총 9장 + 1 archive):
| 도구 | 파일 | 역할 |
|---|---|---|
| **Vercel** (light, 모노크롬 base) | `vercel-deployments-1.png` ★, `vercel-domains.png` ★, `vercel-deployment-detail.png`, `vercel-dashboard.png`, `vercel-projects.png` | **색·여백·spacing·dense table·sidebar nav 토큰의 1차 source** |
| **Linear** (polish, typography hierarchy) | `linear-dashboard-1.png`, `linear-dashboard-2.png`, `linear-feature.png`, `linear-roadmap.png` | **typography hierarchy·micro-interaction·음영·dropdown polish** (다만 dark-native이라 light 재해석 필요) |
| (archive) | `.linear-hero-bg-{1,2,3}.png.bak` | hero gradient, 의미 없음 |

**핵심 가이드라인 (옵션 C 혼합 운영)**:
- **light 색 토큰 / 배경·border·surface / 좌 nav / dense row / status badge 색** = Vercel deployments-1 풍 모방
- **typography hierarchy (heading semibold + body regular weight)·focus ring·hover transition·음영 elevation·dropdown polish** = Linear 풍 (light variant 재해석)
- **두 reference의 충돌 시 우선순위**: light 시각성은 Vercel, micro polish는 Linear
- **Linear의 dark-only 시각 요소는 흡수 안 함** (dark mode 본 계획 제외 — §1.2)

### 4.2 핵심 token 재정의

```
색 (현재 → 변경안)
  Primary             #0f172a (navy)        →  #1a1a1f (warm near-black) + accent #7c3aed (Linear violet) or #2563eb (calm blue)
  Background          #ffffff               →  light: #fafafa / dark: #0a0a0c
  Surface             gray-50               →  light: #f5f5f7 / dark: #18181b
  Border              gray-200              →  light: #e5e5e7 / dark: #27272a
  Severity (유지)      Critical #dc2626, High #ea580c, Medium #ca8a04, Low #2563eb, Info #71717a
  
Spacing
  row-height          40px (유지)            →  40px (compact) + 48px (comfortable) — 사용자 토글
  card-padding        currently varies       →  표준화 16/20/24
  
Typography
  body                Inter 14/20            →  Inter 14/20 (유지, 약간 letter-spacing 조정)
  heading             Inter 18~24 bold       →  Inter 18~24 semibold (Linear 톤)
  mono                JetBrains Mono 13      →  유지

Radius
  현재 8px 일관          →  4 (small inputs) / 6 (cards) / 8 (drawer) / 12 (modal) — hierarchy

Shadow / Elevation
  현재 거의 없음        →  light: subtle shadow-sm/md/lg / dark: ring + bg shift

Motion (신규)
  transition           none/default          →  150ms ease-out (hover) / 200ms (drawer slide) / 250ms (page nav)
  
Focus ring
  shadcn 기본            →  visible 2px outline + offset 2px (a11y 강화)
```

### 4.3 Dark mode — 본 계획 제외
2026-05-27 사용자 결정: **light mode 단일 유지**. Dark mode 는 v2.5+ 별도 트랙 검토. 본 W11 의 모든 토큰·컴포넌트는 light 단일 정의. Tailwind `dark:` variant 사용 안 함 (향후 추가 시 토큰 mirroring 만 필요한 구조로 작성 — forward-compat).

### 4.4 컴포넌트 re-skin 범위

| 카테고리 | 컴포넌트 | 작업 |
|---|---|---|
| 기본 | Button / Input / Select / Checkbox / Radio / Switch | shadcn 토큰 재정의 (한 군데) |
| 표 | Table / SortableColumnHeader / Row / Badge | row hover · border · density |
| 카드 | Card / Stat / RiskGauge / SeverityChart / LicenseChart | shadow · radius · 차트 dark variant |
| 네비 | Sidebar / AppShell header / Breadcrumb | dark variant + active state |
| 드로어/모달 | Drawer / Dialog | shadow · radius · 닫기 affordance |
| 필터 | ActiveFilterChips / FilterBar | chip 시각 |
| 빈 상태 | EmptyState / Skeleton | 일러스트 (Linear 풍 미세) |
| 알림 | Toast / Alert / Banner | severity 별 background |
| Total | ~30 컴포넌트 | ~12~15d |

### 4.5 Phase 분할

| Phase | 내용 | 산출 | 예상 |
|---|---|---|---|
| **A — Reference & Token** | 시각 reference 5장 캡처 + 토큰 정의 (`tailwind.config.ts` + `design-tokens.ts`) — light 단일 | 토큰 + sample 1~2 컴포넌트 | 1.5d |
| **B — Foundation re-skin** | Button/Input/Select/Card/Badge 등 shadcn primitives. **첫 prototype 화면 = Project List (`/projects`)** — Vercel deployments-1 톤 모방 정합 확인 후 다른 컴포넌트 확장 (2026-05-27 사용자 결정 §0). | 모든 기본 컴포넌트 신 토큰 + Project List prototype | 2d |
| **C — Table & Drawer re-skin** | Table/Row/Sortable header/Drawer/Dialog (dense 정체성 유지하며 호흡감 조정) | 4 대형 컴포넌트 | 2d |
| **D — Chart re-skin** | SeverityChart/LicenseChart/RiskGauge (Recharts 토큰) | 차트 신 톤 | 1d |
| **E — 화면별 검증** | 우리 8 화면 모두 캡처 (Playwright capture-ours spec 재실행) + 비교 (before/after) | 8 PNG × 2 = 16 비교셋 | 1d |
| **F — Microinteraction & Polish** | hover transition + focus ring + drawer slide motion + skeleton 폴리시 | A8 점수 3→4 목표 | 1.5d |
| **G — Empty state 일러스트** | 8개 빈 상태 미세 일러스트 | 일러스트 컴포넌트 | 1d |
| **H — A11y sweep + 문서** | 색 대비 (WCAG AA) + focus ring 확인 + keyboard nav + docs-site UI 가이드 갱신 | a11y 통과 | 1d |

**합계**: ~11d (dark mode 제외로 ~2d 감소).

### 4.6 DoD
- [ ] 모든 컴포넌트 신 토큰 적용 (light 단일)
- [ ] WCAG AA 색 대비 (도구 검증)
- [ ] Playwright capture-ours 재실행 → 8 화면 before/after PNG 비교셋 보관
- [ ] 차트 신 톤 readable
- [ ] focus ring · keyboard nav 정상
- [ ] EN/KO 동시
- [ ] docs-site `design-system.md` 신규 (토큰 catalog · 컴포넌트 가이드)
- [ ] typecheck / lint / vitest / Playwright green

### 4.7 위험 + 대응 (W11 큰 위험)
| 위험 | 대응 |
|---|---|
| **미감 재정의 실패 — "별로다"** | Phase A 의 reference 9장 + 사용자 confirm 게이트 (§9.1). 작업 시작 전 톤 합의. Phase E 의 before/after PNG 비교셋 사용자 confirm 게이트. **Phase A 실패 대비 사전 합의된 3 fallback (2026-05-27 사용자 동의)**: **(a)** Vercel 더 강하게 모방, Linear polish 축소 · **(b)** 현재 BD 톤 부분 modernize (배경 회색 밝게 + spacing 호흡 + typography 정리, 토큰 재정의 최소) · **(c)** W11 전체 v2.5 로 미루고 v2.4.0 은 W6 잔여만으로 빠른 GA. |
| **shadcn 토큰 재정의가 3rd party 컴포넌트 깨뜨림** | Tailwind dark variant 사용 (shadcn 기본 지원). 우리 wrapper 우선 변경, primitive 자체는 마지막. |
| **2.5~3주 일정 슬리피지** | Phase 단위 독립 산출. 중단 시 Phase 별 partial 보존. (anti-fizzle §6). |
| **dark mode 차트 readable 안 함** | Phase D 별도 — Recharts theme 토큰화. 차트 reviewer (frontend-dev) 검증. |
| **사용자 토글 confusion** | 첫 사용 시 system preference 자동, 명시 토글은 헤더 우상단 한 곳. |
| **scope creep** — Phase A 에서 추가 reference 무한 추가 | reference 5장 cap, 추가 1장 = 사용자 결정 |

### 4.8 성공 지표 (정량)
- W11 후 audit 재실행 (Playwright capture-ours + 매트릭스):
  - **A8 마이크로인터랙션**: 3 → 4 (목표)
  - **A1 정보 밀도**: 4 → 4 (유지 — 호흡감 늘려도 density 유지)
  - **A2 발견성**: 3 → 3+ (focus ring · hover 개선)
- **외부 보고서/landing page 1차 인상** — "looks modern enterprise" (사용자 또는 외부 디자이너 정성 평가)
- 사용자 핸즈온 후 "전체적으로 더 모던하다" 정성 confirm

---

## 5. 산출물 디렉토리

```
docs/ux/
├── design-philosophy-evolution-plan-2026-05-27.md     ← 이 문서 (SoT)
├── design-tokens-2026-MM-DD.md                         ← W11 Phase A 산출
├── reference/                                           ← W11 reference 5장
│   ├── linear-1.png
│   ├── vercel-dashboard.png
│   ├── vanta-1.png
│   ├── cursor-1.png
│   └── stripe-dashboard.png
├── before-after/                                        ← W11 Phase E 산출
│   ├── dashboard-before.png
│   ├── dashboard-after.png
│   └── ...
└── screens/                                             ← 기존 audit 자산
```

W10 산출은 코드 변경 (`apps/frontend/src/features/{vulnerabilities,components}/`).

---

## 6. Anti-Fizzle 메커니즘 (W4/W6/W9 패턴 차용)

- **Wave 단위 독립 머지** — W10 완료 시점에 멈춰도 dual surface 가치 살아 있음. W11 부분 완료도 partial 가치.
- **Phase 단위 산출** — W11 Phase A (토큰) → B (foundation) → C → D 순. Phase 끝마다 vitest/playwright/typecheck green.
- **DoD 명시** — §3.6 / §4.6 체크리스트.
- **재개 절차** — 본 문서 SoT, 산출물 디렉토리 확인 → 마지막 완료 Phase 식별 → 다음 Phase 시작.
- **결정 게이트** (§9) — 자율 진행 vs 사용자 confirm 명시.
- **품질 게이트** — Phase D capture-ours 재실행 결과 사용자 confirm (W11 미감 변경은 사용자 표지 변화).

---

## 7. 트래커 등재 (실행 승인 후)

| 항목 | 내용 | 상태 |
|---|---|---|
| **W10 (드로어 dual surface)** | Phase A~F 6 단계, 4.5d, W9-#51-A/#51-B 흡수 | 본 계획 승인 후 등재 |
| **W11 (시각 정체성 재정의)** | Phase A~H 8 단계, ~13d, v2.5 마일스톤 후보 | 본 계획 승인 후 등재 |

W9 의 다음 항목들은 W10/W11 과 충돌 없이 병행 가능:
- W9-#50 (Dashboard) — W11 후 더 좋게 — W11 후로 미루기 권장
- W9-#52 (filter + columns picker) — 병행 가능
- W9-#53 (upgrade-group) — 병행 가능
- W9-#54 (CMD+K) — 병행 가능

---

## 8. 일정 (1차 추정)

| 트랙 | 항목 | 일수 | 누적 |
|---|---|---|---|
| GA 직전 | W8-#46 + W9-#51-A | 2d | 2d |
| GA 직전 | W6 잔여 (#43c/#43d/#43e/#44) | 3.5d | 5.5d |
| | **v2.4.0 GA** | | |
| Post-GA | W10 (드로어 dual surface) | 4.5d | 10d |
| Post-GA | W9-#52 / #53 (병행 가능) | 3.5d | (병행) |
| Post-GA | W11 (시각 정체성, light 단일) | 11d | 21d |
| Post-GA | W9-#50 (Dashboard, W11 후) | 1.5d | 24.5d |
| Post-GA | W9-#54 (CMD+K, 병행 가능) | 2d | (병행) |

**v2.4.0 GA 후 ~4.5주 (W10 1주 + W11 2.2주 + W9 잔여 1주)** 로 디자인 진화 + W9 미해결 모두 종결. v2.5 또는 v2.4.x patch series 로 release.

---

## 9. 사용자 결정 필요 (계획 승인 / 실행 게이트)

### 9.1 본 계획 승인 단계 — 2026-05-27 사용자 결정 완료
1. ✅ W10/W11 분리 + 순서 (W10 → W11) 동의
2. ✅ Linear-influenced 1차 추천 동의 (단, **reference 5장 시각 후보 먼저 보고 최종 확정** — 본 turn 진행 중)
3. ✅ **Dark mode 제외** (light 단일 유지, v2.5+ 별도 트랙)
4. ✅ 외부 디자이너 없음
5. ✅ 사용자 본인 confirm (5 KO/EN 사용자 테스트 없음)

### 9.2 실행 중간 게이트
- W11 Phase A 끝: 토큰 정의 + 1~2 컴포넌트 sample → 사용자 confirm (톤 정합 확인)
- W11 Phase E 끝: 8 화면 before/after PNG → 사용자 confirm (전체 화면 인상 확인)

### 9.3 자율 진행 (사용자 미확인)
- W10 모든 Phase (drawer 변경은 visual 영향 적음)
- W11 Phase B~D/F~H (Phase A 톤 합의 후엔 자율)
- 신규 코드 + 테스트 + i18n + docs

---

## 10. 알려진 한계 (정직)

1. **W11 미감은 정성 평가** — "modern aesthetic" 정의가 절대적이지 않음. reference set 합의 + 사용자 confirm 게이트로 보완하나 본질적 주관성 잔존.
2. **평가자 bias** (audit §11 와 동일) — v2.4.0 GA 후 외부 디자이너 1회 review 권장.
3. **dark mode 표준** — system preference 자동 감지 + 토글이지만, 사용자가 EN/KO 토글 + dark/light 토글 두 가지 UI control 갖게 됨. 인지 부담 증가.
4. **a11y sweep 깊이** — Phase H 가 1.5d 라 WCAG AA 자동 도구 검증 수준. 본격 a11y audit 별도 트랙 (v2.6+).
5. **기존 사용자 인지 disruption** — W11 후 익숙한 인터페이스가 달라짐. release notes + 변경점 강조 + (옵션) "classic theme" 유지 토글 검토.

---

## 11. 본 계획의 위치

| 문서 | 역할 |
|---|---|
| `CLAUDE.md` §디자인 시스템 | 원안 (Black Duck 스타일) — W11 완료 시 갱신 |
| `competitive-audit-2026-05-27.md` | 본 계획의 트리거 (audit §11 #3/#5) |
| `competitive-audit-plan-2026-05-27.md` | audit 자체 운영 |
| **`design-philosophy-evolution-plan-2026-05-27.md` (본 문서)** | **W10/W11 SoT** — 실행 승인 후 트래커 등재 |
| `post-ga-execution-tracker.md` | 승인 후 W10/W11 등재 |

---

## 12. 다음 행동 (사용자 응답 후)

**case A — 승인 + 사용자 결정 §9.1 모두 답함**:
1. 본 계획서 §11 트래커 등재
2. 메모리 `project_design_philosophy_evolution.md` 신규
3. W8-#46 → W9-#51-A → W6 잔여 → W10 순서로 진행 시작 권장 (별 인테이크 없으면)

**case B — 일부 수정 요청**:
- §9.1 답변에 따라 본 계획 §2/§3/§4 갱신
- 재승인 후 case A

**case C — 더 깊은 사전 검토 필요**:
- 예: "reference 5장 시각 후보 먼저 보고 싶다" → Phase A 의 일부를 사전 수행
- 예: "외부 디자이너 의견 먼저" → 본 계획 보류

답변 기다림.
