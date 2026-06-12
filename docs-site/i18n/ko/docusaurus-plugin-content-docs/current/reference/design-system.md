---
id: design-system
title: 디자인 시스템
description: TRUSCA 디자인 시스템 — 토큰(색·spacing·radius·shadow·motion·typography)·컴포넌트 규약·마이크로인터랙션·접근성·W11 시각 정체성 재정의.
sidebar_label: 디자인 시스템
sidebar_position: 10
---

# 디자인 시스템

포털 프론트엔드는 **Vercel** (light base — surface · dense row · sidebar tint) 과 **Linear** (타이포 hierarchy · motion · focus polish) 에 영감을 받은 단일 light 모드 디자인 시스템을 따릅니다. Dark 모드는 + 이후로 미룹니다.

:::note 대상 독자
프론트엔드 기여자, 디자이너, 리뷰어. 본 문서의 토큰은 canonical reference 입니다 — 컴포넌트는 hex 값이나 magic spacing 을 직접 박지 않습니다.
:::

본 페이지는 시각 결정의 단일 진실입니다. 구현은 아래에 있습니다:

- `apps/frontend/src/index.css` — CSS custom property (`--background`, `--ring`, `--risk-critical`, …)
- `apps/frontend/tailwind.config.ts` — CSS 변수에서 파생된 Tailwind 토큰
- `apps/frontend/src/components/ui/` — 토큰에 연결된 shadcn/ui primitives

## 철학

TRUSCA는 **리스크 우선 · 정보 밀도 · 모던 엔터프라이즈** SCA 도구입니다. 시각 정체성이 충족해야 할 것:

1. **한눈에 심각도 전달.** Severity 색 (Critical / High / Medium / Low / Info) 은 항상 텍스트 라벨 + 아이콘/dot 와 함께 — 색은 신호의 단독 수단이 아닙니다.
2. **답답하지 않게 데이터 밀도.** 40 px compact 행 · 224 px 사이드바 · 48 px 헤더 · 16/20/24 px 카드 padding 표준화.
3. **모던 엔터프라이즈 제품 톤.** Navy `#0f172a` 가 아닌 warm near-black `#18181b` · 순수 흰색이 아닌 off-white canvas `#fafafa` · subtle shadow · semibold heading · 가시 focus ring.
4. **필요한 만큼만 움직임.** Hover/focus 150 ms · drawer slide 200 ms · 페이지 크롬 250 ms. ease-out 만. Bounce 없음, fade-in delay 없음.

### W11 (2026-05-27) — 시각 재정의

W11 마일스톤에서 기존 "BD-style 2015" 미감을 Vercel+Linear 혼합으로 교체했습니다. 구조적 결정 (사이드바 nav · 40 px row · drawer-for-detail · severity 의미) 은 유지. 바뀐 것:

| Surface | Before | After |
|---|---|---|
| Primary CTA | `#0f172a` cool navy | `#18181b` warm near-black |
| 페이지 배경 | `#ffffff` 순백 | `#fafafa` off-white canvas |
| 카드 surface | grey 톤 | `#ffffff` 순백 (canvas 위로 떠 보임) |
| Border | `slate-200` | `#e5e5ea` neutral hairline |
| Radius | 8 px 일관 | 계층 — sm 4 / md 6 / lg 8 / xl 12 |
| Shadow | 없음 / 기본 | sm (card) / md (popover) / lg (drawer · dialog) |
| Motion | 브라우저 기본 | 150 / 200 / 250 ms ease-out |
| Heading weight | bold | semibold + tracking-tight |
| Focus ring | shadcn 기본 | 2 px outline + 2 px offset (a11y) |
| 디테일 surface | 드로어 전용 | dual surface — 드로어 (빠른 확인) + 페이지 nav (깊은 작업) |

Severity 팔레트 (Critical / High / Medium / Low / Info) 는 **의도적으로 유지** — 브랜드 의미가 릴리스 간 고정입니다. Severity hex 가 light tint 위 본문 텍스트로 사용될 때 WCAG AA 가 안 나오면, 같은 hue family 의 더 짙은 shade 로 텍스트 색만 어둡게 — 아래 [Severity 색 접근성](#severity-색-접근성) 참고.

## 색 토큰

모든 색 결정은 `index.css` 의 CSS custom property 를 참조합니다. 컴포넌트는 hex 값을 직접 박지 않습니다 — Tailwind utility (`bg-background`, `text-foreground`, `bg-risk-critical/10`) 나 CSS 변수를 사용합니다.

### Neutral 팔레트 (Vercel base)

| 토큰 | Hex | HSL | 용도 |
|---|---|---|---|
| `--background` | `#fafafa` | `0 0% 98%` | 페이지 canvas. 카드가 시각적으로 뜨도록. |
| `--card` | `#ffffff` | `0 0% 100%` | Elevated surface — 카드 · popover · 드로어 본문 · 툴팁. |
| `--foreground` | `#18181b` | `240 6% 10%` | 본문 텍스트. Warm near-black (navy 아님). |
| `--muted` | `#f4f4f5` | `240 5% 96%` | 미묘한 fill — 테이블 헤더 · 사이드바 tint · placeholder · disabled input. |
| `--muted-foreground` | `#71717a` | `240 4% 46%` | 보조 텍스트 · caption · 테이블 컬럼 헤더. |
| `--border` | `#e5e5ea` | `240 5% 91%` | Hairline border. 장식용 separator 만 — UI 영역 식별의 유일한 수단이 되지 않습니다. |
| `--input` | `#e5e5ea` | `240 5% 91%` | Input outline. |
| `--primary` | `#18181b` | `240 6% 10%` | Primary CTA — "이 페이지의 중요 액션". |
| `--primary-foreground` | `#fafafa` | `0 0% 98%` | Primary 위 텍스트. |
| `--destructive` | `#dc2626` | `0 72% 51%` | Destructive CTA. `--risk-critical` 와 같아서 destructive 버튼이 severity 와 같은 시각 언어. |
| `--destructive-foreground` | `#fafafa` | `0 0% 98%` | Destructive 위 텍스트. |
| `--ring` | `#18181b` | `240 6% 10%` | Focus ring. Primary 와 매칭 — outline 이 액션과 같은 색 패밀리로 읽힘. |

### Severity (도메인 의미 — 고정)

| 토큰 | Hex | 용도 |
|---|---|---|
| `--risk-critical` | `#dc2626` | Critical CVE · forbidden 라이선스 · build-blocking finding. |
| `--risk-high` | `#ea580c` | High-severity CVE · conditional 라이선스 위험. |
| `--risk-medium` | `#ca8a04` | Medium CVE · 검토 대기 conditional 라이선스. |
| `--risk-low` | `#2563eb` | Low CVE · 정보성 상태. |
| `--risk-info` | `#71717a` | 중립 정보. |

Severity hex 는 **릴리스 간 변경하지 않습니다**. 사용처:

- Recharts fill · 차트 범례 (raw hex, `--risk-X` 변수 참조).
- 배지 · dot 인디케이터의 `bg-risk-X/N` tint.
- 버튼 · alert 의 border accent (`border-risk-high/40`).

Severity 톤이 **본문 텍스트** 로 사용될 때 (배지 안의 색 단어 등) 는 같은 Tailwind hue family 의 더 짙은 shade 를 — [Severity 색 접근성](#severity-색-접근성) 참고.

## Spacing

| 토큰 | 값 | 용도 |
|---|---|---|
| `--layout-sidebar` | 224 px | 펼친 사이드바 폭 (기본값). |
| `--layout-sidebar-collapsed` | 64 px | 사용자가 사이드바를 접었을 때의 아이콘 전용 레일 폭 (≥`lg`). |
| `--layout-header` | 48 px | 상단 헤더 높이. |
| `--table-row` | 40 px | Compact 테이블 행 높이. |

**사이드바 동작.** 좌측 사이드바는 **사용자가 접을 수 있고 뷰포트에 반응**한다:

- **≥ `lg` (1024 px):** 고정 사이드바. 레일 하단의 토글로 224 px → 64 px 아이콘 전용 레일로 접으며, 접힌 라벨은 `aria-label` + 네이티브 hover 툴팁으로 노출된다. 선택 상태는 reload 후에도 유지된다 (`uiStore` → `localStorage` 키 `trustedoss-ui`). 폭은 `--duration-base` 동안 애니메이션된다.
- **< `lg`:** 고정 사이드바는 숨겨지고 헤더 햄버거가 전체 라벨 내비게이션을 담은 오버레이 드로어(좌측 `Sheet`)를 연다. 드로어는 이동·오버레이 클릭·ESC 시 닫힌다.

**카드 padding** 은 **16 / 20 / 24 px** (Tailwind `p-4` / `p-5` / `p-6`) 로 표준화:

- `p-4` — compact 카드 (대시보드 타일 · stat 카드).
- `p-5` — 표준 카드 (프로젝트 목록 행 · 드로어 섹션).
- `p-6` — 메인 콘텐츠 카드 (페이지 wrapper · 다이얼로그).

## Radius 계층

Affordance 별로 다른 radius — depth 가 한눈에 읽힘.

| 토큰 | 값 | Affordance |
|---|---|---|
| `--radius-sm` | 4 px | 작은 input · 배지 · 칩. |
| `--radius` | 6 px | **기본** — 버튼 · 카드 · 테이블 크롬. |
| `--radius-lg` | 8 px | 드로어 · 큰 surface. |
| `--radius-xl` | 12 px | 모달 · 다이얼로그. |

Tailwind config 가 `calc()` 로 `rounded-sm`/`rounded-md`/`rounded-lg`/`rounded-xl` 를 이 토큰에서 파생.

## Shadow 스케일

Vercel 톤의 subtle elevation. 가벼운 그림자만 — glow 없음.

| 토큰 | 값 | 용도 |
|---|---|---|
| `--shadow-sm` | `0 1px 2px 0 rgb(0 0 0 / 0.04)` | 카드 · stat 타일. |
| `--shadow-md` | `0 2px 8px -2px rgb(0 0 0 / 0.08), 0 1px 2px 0 rgb(0 0 0 / 0.04)` | 드롭다운 · popover · 툴팁. |
| `--shadow-lg` | `0 10px 28px -8px rgb(0 0 0 / 0.12), 0 3px 8px -3px rgb(0 0 0 / 0.06)` | 드로어 · 다이얼로그. |

## Motion

짧고 ease-out — Linear polish. 세 단계로 대부분의 UI 애니메이션을 커버.

| 토큰 | 값 | 용도 |
|---|---|---|
| `--duration-fast` | 150 ms | Hover · focus 링 fade-in · 배지 tint shift · 버튼 색 transition. |
| `--duration-base` | 200 ms | 드로어 slide · popover open · 드롭다운 reveal. |
| `--duration-slow` | 250 ms | 페이지 크롬 transition · 라우트 전환 진입. |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | 어디서나 같은 easing 커브. 진입은 snappy, 종료는 gentle. |

**로딩 상태는 spinner 가 아닌 스켈레톤.** 장시간 작업 (스캔 · export) 은 라벨이 붙은 progress bar — bare spinner 금지.

## Typography

| Element | Family | Size / Weight | Notes |
|---|---|---|---|
| Body | Inter | 14 px / regular | `letter-spacing: −0.005em` (Linear tight body). |
| Heading 1 / 2 / 3 / 4 | Inter | 18 ~ 24 px / semibold | `tracking-tight`. Bold 아님 — semibold 가 모던 엔터프라이즈 톤. |
| Mono | JetBrains Mono | 13 px | 코드 · 해시 · CVE ID · PURL · JSON 스니펫. `letter-spacing: 0` — mono 는 body tightening 상속 안 함. |

OpenType `rlig` · `calt` 가 `body` 에 활성화되어 Inter 가 제대로 렌더링.

**raw 유틸리티 대신 타이포그래피 프리미티브를 쓰세요.** `apps/frontend/src/components/ui/typography.tsx` 가 스케일을 이름 붙인 컴포넌트로 제공하므로, 같은 역할이 화면마다 어긋나지 않고(`text-lg` 와 `text-base` 가 섞이지 않고) 동일하게 보입니다.

| 컴포넌트 | 요소 | 역할 |
|---|---|---|
| `PageTitle` | `h1` | 페이지 제목 하나 — 18 px semibold tracking-tight. |
| `SectionTitle` | `h2` | 섹션 · 하위 영역 제목 — 16 px semibold. |
| `Subtitle` | `p` | 페이지 제목 아래 muted 보조 줄 — 14 px. |
| `Body` | `p` | 본문 — 14 px (`muted` prop 으로 보조 본문). |
| `Caption` | `span` | 밀집 메타(타임스탬프 · 카운트) — 12 px muted. |
| `Eyebrow` | `span` | 대문자 overline · 컬럼 그룹 라벨 — 12 px medium. |

어떤 프리미티브로도 안 되는 일회성 인라인 span 에만 raw `text-*` 유틸리티를 쓰고, 페이지 제목은 절대 직접 조합하지 마세요.

## Focus ring

키보드 navigation 시 모든 interactive element 가 가시 focus ring 표시:

```css
focus-visible:outline-none
focus-visible:ring-2
focus-visible:ring-ring
focus-visible:ring-offset-2
```

`--ring` 이 `--primary` 와 같아서 outline 이 액션과 같은 색 패밀리로 읽힘. `ring-offset-2` 가 2 px 숨돌이를 만들어 tinted 배경 (severity 배지 · alert 카드) 위에서도 ring 이 보입니다.

**Focus ring 을 절대 끄지 마세요.** 2 px outline 은 키보드 사용자의 1차 affordance — 제거하면 UI 가 도달 불가.

## 컴포넌트 규약

포털은 [shadcn/ui](https://ui.shadcn.com/) primitives 위에 빌드됩니다. 각 primitive 는 위 토큰에 연결되어 `apps/frontend/src/components/ui/` 에 re-export.

### Page header

`apps/frontend/src/components/PageHeader.tsx`

모든 라우트는 헤더를 `PageHeader` 로 렌더링해 제목 타이포그래피와 헤더 chrome 을 동일하게 맞춥니다. chrome 은 `bg-background` + `border-b`(off-white 캔버스에 가는 구분선)로 통일해, 아래의 흰 카드 · 테이블이 떠 보이게 합니다. 두 가지 형태:

- `variant="stacked"`(기본) — 더 높은 헤더(`py-4`)에 `PageTitle` 과 muted `description`. 설명 줄이 필요한 페이지(Scans, Admin 영역).
- `variant="bar"` — 48 px 슬림 행(`var(--layout-header)`), 제목과 선택적 오른쪽 `actions` 슬롯(버튼 또는 메타 텍스트), 부제 없음. 목적이 자명한 밀집 페이지(Dashboard, 프로젝트 목록).

stacked 변형에는 선택적 `meta` 슬롯도 있습니다 — 부제 아래에 오는 블록("2분 전 갱신" 같은 자체 test id 가진 줄)으로, 블록 내용을 부제 `<p>` 안에 중첩하지 않도록 `description` 과 분리합니다. `actions` 슬롯은 호출 측 마크업이라 버튼 · 메타의 기존 하네스 `data-testid` 가 보존됩니다.

`<header><h1>` 블록을 직접 만들지 말고, 정말 새 레이아웃이 필요하면 `PageHeader` 를 확장하세요. **예외:** detail 페이지(프로젝트 상세, 컴포넌트 · 취약점 상세, Compare, 스캔 상세)는 *브레드크럼 헤더*(breadcrumb `<nav>` + 맥락 제목)를 쓰며, 이는 `PageHeader` 가 아직 모델링하지 않은 별도 archetype 입니다. 이 페이지들은 직접 짠 헤더를 유지하되 타이포는 같은 스케일을 따릅니다.

### Button

`apps/frontend/src/components/ui/button.tsx`

- 기본 variant: `bg-primary text-primary-foreground` — solid warm near-black.
- `outline` variant: `border-input bg-background` — 보조 액션.
- `ghost` variant: 배경 없음, hover tint 만 — nav 항목 · toolbar 액션.
- `destructive`: `bg-destructive` — Critical-aligned 빨강, 되돌릴 수 없는 액션 전용 (delete · revoke · reject).
- Hover / focus transition: `transition-colors duration-fast ease-out` (150 ms).
- 모든 variant 에 focus ring 포함.

### Input / Select / Checkbox

- Border 색 `--input` · focus ring `--ring`.
- Disabled 상태: `bg-muted text-muted-foreground`.
- Error 상태: `border-destructive` + `aria-live="polite"` 메시지가 필드 아래.

### Card

- Off-white canvas 위 순백 surface (`bg-card`) — 무거운 shadow 없이 시각적으로 뜸.
- 기본 `rounded-md` (6 px) · 메인 콘텐츠 카드는 `rounded-lg` (8 px).
- Stat / 타일은 `shadow-sm` · 떠 있는 popover 는 `shadow-md`.

### Table

- Compact 밀도 — 행 높이 40 px · 헤더 tint `bg-muted`.
- Sortable 컬럼 헤더는 라벨 옆 12 px chevron.
- 행 hover: `bg-muted/50` + 150 ms transition.
- 1 k+ 행이면 virtual scrolling (`react-virtuoso`).
- Severity 컬럼은 항상 색 + 텍스트 라벨/아이콘 — SeverityBadge 참고.

### Drawer (`sheet.tsx`)

- 오른쪽 slide-in, **콘텐츠 밀도에 따라 폭 480 ~ 640 px**.
- 드로어 패널에 `shadow-lg`.
- 200 ms `ease-out` slide.
- 드로어 상태는 **URL 인코딩** (`?drawer=component:abc123`) — 리로드 후 살아남음.
- 드로어는 **빠른 확인** 용 — 표 행의 전체 payload · CVE 의 CVSS 분해 · 컴포넌트의 라이선스 체인. 페이지 nav 는 **깊은 작업** 용 — 일괄 편집 · 다단계 승인 · 스캔 설정.

### Dialog

- `bg-foreground/40` backdrop 위 중앙 정렬 모달.
- `rounded-xl` (12 px) · `shadow-lg`.
- **Destructive confirmation** (프로젝트 삭제 · API key revoke) 과 **인라인 생성 플로우** (새 프로젝트 · 새 팀) 전용.

### EmptyState

`apps/frontend/src/components/EmptyState.tsx`

- 중앙 정렬, max-width 420 px.
- 레이어드 아이콘 메달리온 (W12-D) — 부드러운 동심 muted 링 둘 뒤에 떠 있는 흰 안쪽 원판이 아이콘을 담음 — 그 아래 타이틀 (semibold) · 설명 (muted) · 단일 primary CTA. `illustration` 을 넘기면 메달리온 대신 더 풍부한 인라인 SVG 로 교체(인라인만, 새 에셋 없음).
- 용도: 빈 목록 · 빈 검색 결과 · 빈 드로어 탭 · 첫 사용 온보딩 카드.

### Skeleton

`apps/frontend/src/components/ui/skeleton.tsx` · `skeletons.tsx`

- `Skeleton` 은 기본 바(`animate-pulse` · `rounded-sm`). 전폭 바 하나보다, 최종 레이아웃을 닮은 composite 스켈레톤을 써서 콘텐츠가 reflow 없이 자리잡게 합니다.
- `TableRowsSkeleton` 은 로딩 테이블에 컬럼별 셀(컬럼당 너비 하나)을 렌더링. 테이블은 `aria-busy` 유지, 스켈레톤 행은 `aria-hidden`.

### Badge

`apps/frontend/src/components/ui/badge.tsx`

Risk-tinted variant 는 상태 단어와 디자인 시스템 색을 짝지움. 배경은 `bg-risk-X/10` (medium / info 는 `/15`) — 칩이 색 tint 로 읽히도록. 텍스트는 같은 hue family 의 더 짙은 shade — 렌더링 대비가 WCAG AA 4.5:1 을 통과 — [Severity 색 접근성](#severity-색-접근성) 참고.

### Toast

`apps/frontend/src/components/ui/toast.tsx`

`AppProviders` 에 마운트된 단일 `<ToastProvider>` 가 우하단에 쌓이는 영역 하나를 렌더링하고, `useToast().toast(text, opts)` 로 어디서든 띄웁니다. 토스트는 큐로 쌓이고 자동으로 사라지며(4초) `aria-live` 영역으로 안내됩니다.

- **피드백 규칙.** 성공 · 비차단 알림은 토스트, 폼 검증 에러는 필드 옆 **인라인**(RFC 7807 `detail`) — 사용자가 놓칠 토스트로 쓰지 않습니다.
- **test-id 계약.** `testId` 기본값은 `"admin-toast"` 이고 토스트는 `data-tone` + `data-toast-key` 를 달아, 모든 e2e 하네스가 선택하는 마크업(`[data-testid="admin-toast"][data-tone][data-toast-key]`)을 그대로 냅니다. `tone`(`success` / `error`)과 locale 독립 `key` 를 넘깁니다. ScanCancelButton 만 `testId: "scan-cancel-toast"` 로 덮어씁니다.
- **예외.** 두 표면은 자체 로컬 토스트를 유지합니다: 스캔 상세의 다운로드 알림(success / error 톤이 아닌 중립 `data-toast-variant`)과 Settings 탭의 인라인 `settings-toast` 저장 확인. 둘 다 자체 테스트 계약이 있고 success / error 모델에 맞지 않습니다.

## 마이크로인터랙션 가이드

W11-F polish phase 가 모든 인터랙티브 transition 의 타이밍 · easing 을 표준화. 컴포넌트는 토큰에서 motion 을 가져옵니다 — 새 값을 hand-roll 하지 않습니다.

| Interaction | Duration | Easing | Property |
|---|---|---|---|
| Button / link hover | 150 ms (`--duration-fast`) | `--ease-out` | `background-color`, `color`, `border-color` |
| 배지 tint shift on hover | 150 ms | `--ease-out` | `background-color` |
| Focus ring fade-in | 150 ms | `--ease-out` | `box-shadow`, `outline` |
| 드롭다운 / popover open | 200 ms (`--duration-base`) | `--ease-out` | `opacity`, `transform: translateY` |
| 드로어 slide | 200 ms | `--ease-out` | `transform: translateX` |
| 다이얼로그 open | 200 ms | `--ease-out` | `opacity` (backdrop), `transform: scale` (panel) |
| 탭 인디케이터 이동 | 200 ms | `--ease-out` | `transform: translateX` |
| 페이지 크롬 — 사이드바 접기 | 250 ms (`--duration-slow`) | `--ease-out` | `width` |
| 라우트 전환 진입 | 250 ms (`--duration-slow`) | `--ease-out` | `opacity` (`<main>` 을 pathname 으로 key) |
| 스켈레톤 pulse | 2000 ms loop (`animate-pulse`) | `ease-in-out` | `opacity` |

**브라우저 기본 easing 사용 금지.** 항상 `--ease-out` 참조 — 모션이 제품 전반에 걸쳐 단일 언어로 읽혀야 합니다.

**Reduced motion.** `index.css` 의 전역 `@media (prefers-reduced-motion: reduce)` 가드가 위의 모든 애니메이션 · transition 을 ~0 으로 줄이고(부드러운 스크롤도 끔), reduced motion 을 요청한 사용자는 즉시 상태 변화를 받습니다 — [접근성](#접근성) 참고.

## 접근성

포털은 **WCAG 2.1 Level AA** 를 목표로 합니다. 세 가지 정책으로 구체화.

### 색 대비 — 본문 4.5:1 · UI 3:1

| 쌍 | 비율 | 비고 |
|---|---|---|
| `--foreground` on `--background` | 16.97:1 | 본문. AAA. |
| `--foreground` on `--card` | 17.72:1 | 카드 위 본문. AAA. |
| `--muted-foreground` on `--background` | 4.63:1 | Caption · 보조 텍스트. AA. |
| `--muted-foreground` on `--card` | 4.83:1 | 카드 위 caption. AA. |
| `--primary-foreground` on `--primary` | 16.97:1 | Primary 버튼 라벨. AAA. |
| `--destructive-foreground` on `--destructive` | 4.63:1 | Destructive 버튼 라벨. AA. |
| `--ring` on `--background` | 16.97:1 | Focus ring. AAA. |

장식용 border (`--border` on `--background`, 1.20:1) 는 **의도적으로 저대비** — 시각 분리용일 뿐 정보성 UI 요소가 아니며 WCAG 1.4.11 이 면제 대상.

### Severity 색 접근성

Severity hex (`#dc2626` / `#ea580c` / `#ca8a04` / `#2563eb` / `#71717a`) 는 브랜드 고정. Light tint 위 **본문 텍스트** 로 쓰이면 2.5:1 까지 떨어져 AA 실패. 해결은 색이 아닌 구조 — severity 톤이 텍스트로 쓰일 때 렌더링 텍스트 색은 같은 Tailwind hue family 의 더 짙은 shade:

| Tone | Tint 배경 | 텍스트 색 | 대비 |
|---|---|---|---|
| `critical` | `bg-risk-critical/10` | `text-red-700` (`#b91c1c`) | 5.54:1 |
| `high` | `bg-risk-high/10` | `text-orange-800` (`#9a3412`) | 6.47:1 |
| `medium` | `bg-risk-medium/15` | `text-yellow-800` (`#854d0e`) | 5.91:1 |
| `low` | `bg-risk-low/10` | `text-blue-700` (`#1d4ed8`) | 5.83:1 |
| `info` | `bg-risk-info/15` | `text-slate-600` (`#52525b`) | 6.41:1 |

**Dot 인디케이터** (SeverityBadge · 차트 범례 · status pill) 는 계속 raw `bg-risk-X` 토큰 사용 — 색 정체성은 그대로, 텍스트 shade 만 어둡게. 참조 구현은 `apps/frontend/src/components/ui/badge.tsx` (W11-H).

### 색이 신호의 단독 수단 아님

Severity 가 표시되는 모든 곳에서 색은 다음 중 하나와 짝지움: 텍스트 라벨 ("Critical") · Lucide 아이콘 (`ShieldAlert`, `TriangleAlert`) · dot + 라벨 조합. 포털은 greyscale 에서도 사용 가능해야 합니다.

### 키보드 navigation

모든 interactive 요소는 `Tab` 으로 도달 가능 · `Enter` / `Space` 로 조작 가능. 포털은 열린 `Dialog` 내부 외에는 focus 를 trap 하지 않습니다 (Dialog 의 focus-trap 은 의도된 것).

- 사이드바 링크: `Tab` 으로 가시 항목 순환.
- 상단 헤더: avatar 드롭다운 · locale 토글 · 로그아웃 — `Tab` 도달 가능.
- 테이블 행: 드로어를 여는 행은 `<button>` 또는 `<a>` 로 렌더링 · `Enter` 로 활성화.
- 드로어: `Esc` 로 닫기 · 첫 tabstop 은 `X` 닫기 · 드로어 열려 있을 때 `Tab` 이 패널 안에서 순환.
- 다이얼로그: 드로어와 같은 패턴 + focus-trap. `Esc` 로 취소.
- Active filter chip: 각 칩의 `×` 가 `<button>` · `Tab` 도달 가능.
- 콤보 박스 (`Select` · 검색): `↑ ↓` 로 옵션 이동 · `Enter` 로 확정 · `Esc` 로 닫기.

### Form

- 모든 `<input>` 은 `<label>` 과 연결 — 시각적이거나 `aria-label` 로.
- 에러 메시지는 필드 옆 `<p role="alert" aria-live="polite">`.
- 필수 필드는 라벨 옆 `*` 와 `aria-required="true"`.
- 검증은 blur 와 submit 에서 — 모든 키입력마다 X (스크린리더 chatter).

### Live region

- Toast 알림은 `aria-live="polite"`.
- 스캔 progress bar 는 `aria-live="polite"` · 단계 전환 시 라벨 업데이트 ("컴포넌트 탐지" → "CVE 매칭" → "보고서 생성").
- 장기 CI build-gate 출력은 `aria-live="polite"` — 스크린리더가 stage 전환을 알림.

## 변경 이력

| Wave | 일자 | 변경 |
|---|---|---|
| W11-A | 2026-05-27 | 토큰 재정의 — Vercel base + Linear polish. Primary `#0f172a` → `#18181b` · 배경 `#ffffff` → `#fafafa` · 새 radius / shadow / motion / focus-ring 토큰. |
| W11-B | 2026-05-27 | Foundation re-skin — Button / Input / Select / Card / Badge 새 토큰 · Project list 가 첫 prototype 화면. |
| W11-C | 2026-05-27 | Table / Drawer / Dialog re-skin (PR #244). |
| W11-D | 2026-05-27 | 차트 re-skin — Recharts grid / axis / tooltip 토큰 (PR #245). |
| W11-E | 2026-05-27 | 8 EN + 3 KO before-after PNG 비교 (PR #246). |
| W11-F | 2026-05-27 | 마이크로인터랙션 polish — hover / focus / motion (PR #247). |
| W11-G | 2026-05-27 | 빈 상태 일러스트 (PR #248). |
| W11-H | 2026-05-27 | **A11y sweep + 디자인 시스템 문서.** Severity 배지 텍스트 색을 light tint 위 WCAG AA 통과로 짙게 (토큰 변경 없음). 본 페이지 추가. |
| W12-A | 2026-06-11 | **Craft 격상 — 타이포그래피 · 페이지 헤더 체계.** 타이포그래피 프리미티브(`PageTitle` · `SectionTitle` · `Subtitle` · `Body` · `Caption` · `Eyebrow`)와 공용 `PageHeader`(stacked · bar) 추가. 화면마다 어긋났던 페이지 제목 스케일(`text-lg` 대 `text-base`)과 헤더 chrome(`bg-card` 대 `bg-background`)을 통일. |
| W12-B | 2026-06-11 | **Craft 격상 — 전역 토스트.** `ToastProvider` + `useToast()`(큐 · 자동 사라짐 · `aria-live`) 추가, 손으로 짠 페이지별 토스트 11곳을 이전하면서 `admin-toast` / `data-toast-key` e2e 계약 보존. 스캔 상세 다운로드 알림 + Settings 인라인 확인은 문서화된 예외로 유지. |
| W12-C | 2026-06-11 | **Craft 격상 — 모션 (CSS-only).** 라우트 전환 진입 페이드(`<main>` 을 pathname 으로 key, 250 ms), 사이드바 접기 250 ms 정렬, 전역 `prefers-reduced-motion` 가드. 새 의존성 없음(tailwindcss-animate 만). 스켈레톤 문서를 실제 2000 ms `animate-pulse` 로 정정. |
| W12-D | 2026-06-12 | **Craft 격상 — 빈 상태 · 로딩 폴리시.** EmptyState 에 레이어드 아이콘 메달리온 + 선택적 `illustration` 슬롯 추가, 신규 `TableRowsSkeleton` 이 Scans · Admin Users 테이블에서 컬럼별 로딩 셀(전폭 바 대체)을 렌더링. |
| W12-E/F | 2026-06-12 | **Craft 격상 — 가드레일 + 문서.** `/dev/design-preview` 를 살아있는 컴포넌트 레퍼런스(타이포그래피 · 배지 · 빈 / 로딩 · 피드백)로 확장하고, 기여자 coding standards 에 "프론트엔드 UI" 섹션 추가. 시각 회귀 베이스라인 확장(4 → ~15)은 CI / 운영자 후속 — darwin 개발 머신에서는 올바른 linux 베이스라인을 생성할 수 없음. |

이전 "BD-style 2015" 미감 (`#0f172a` navy · 순백 canvas · 일관 8 px radius · shadow 없음 · 브라우저 기본 easing) 은 W11 로 완전 은퇴.

## 참고

- [아키텍처](./architecture.md) — backend / frontend / 스캔 파이프라인 개요.
- [코딩 표준](../contributor-guide/coding-standards.md) — 포맷·린트·커밋 규약.
- [`CLAUDE.md`](https://github.com/trustedoss/trusca/blob/main/CLAUDE.md) — 최상위 프로젝트 규칙. "디자인 시스템 (v2)" 절이 본 페이지를 요약.
- W11 진실의 단일 출처 — `docs/ux/design-philosophy-evolution-plan-2026-05-27.md` (in-repo).
