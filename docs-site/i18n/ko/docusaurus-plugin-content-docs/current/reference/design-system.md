---
id: design-system
title: 디자인 시스템
description: TRUSCA 디자인 시스템 — 토큰(색·spacing·radius·shadow·motion·typography)·컴포넌트 규약·마이크로인터랙션·접근성·W11 시각 정체성 재정의.
sidebar_label: 디자인 시스템
sidebar_position: 10
---

# 디자인 시스템

포털 프론트엔드는 **Vercel** (light base — surface · dense row · sidebar tint)과 **Linear** (타이포 hierarchy · motion · focus polish)에 영감을 받은 단일 light 모드 디자인 시스템을 따릅니다. Dark 모드는 + 이후로 미룹니다.

:::note 대상 독자
프론트엔드 기여자, 디자이너, 리뷰어. 본 문서의 토큰은 canonical reference 입니다 — 컴포넌트는 hex 값이나 magic spacing을 직접 박지 않습니다.
:::

본 페이지는 시각 결정의 단일 진실입니다. 구현은 아래에 있습니다:

- `apps/frontend/src/index.css` — CSS custom property (`--background`, `--ring`, `--risk-critical`, …)
- `apps/frontend/tailwind.config.ts` — CSS 변수에서 파생된 Tailwind 토큰
- `apps/frontend/src/components/ui/` — 토큰에 연결된 shadcn/ui primitives

## 철학

TRUSCA는 **리스크 우선 · 정보 밀도 · 모던 엔터프라이즈** SCA 도구입니다. 시각 정체성이 충족해야 할 것:

1. **한눈에 심각도 전달.** Severity 색 (Critical / High / Medium / Low / Info)은 항상 텍스트 라벨 + 아이콘/dot와 함께 — 색은 신호의 단독 수단이 아닙니다.
2. **답답하지 않게 데이터 밀도.** 40 px compact 행 · 256 px 사이드바 · 56 px 글로벌 바 · 16/20/24 px 카드 padding 표준화.
3. **모던 엔터프라이즈 제품 톤.** Navy `#0f172a`가 아닌 warm near-black `#18181b` · 순수 흰색이 아닌 off-white canvas `#fafafa` · subtle shadow · semibold heading · 가시 focus ring.
4. **필요한 만큼만 움직임.** Hover/focus 150 ms · drawer slide 200 ms · 페이지 크롬 250 ms. ease-out 만. Bounce 없음, fade-in delay 없음.

### W11 (2026-05-27) — 시각 재정의

W11 마일스톤에서 기존 "enterprise-style 2015" 미감을 Vercel+Linear 혼합으로 교체했습니다. 구조적 결정 (사이드바 nav · 40 px row · drawer-for-detail · severity 의미)은 유지. 바뀐 것:

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

Severity 팔레트 (Critical / High / Medium / Low / Info)는 **의도적으로 유지** — 브랜드 의미가 릴리스 간 고정입니다. Severity hex가 light tint 위 본문 텍스트로 사용될 때 WCAG AA가 안 나오면, 같은 hue family의 더 짙은 shade로 텍스트 색만 어둡게 — 아래 [Severity 색 접근성](#severity-색-접근성) 참고.

## 색 토큰

모든 색 결정은 `index.css`의 CSS custom property를 참조합니다. 컴포넌트는 hex 값을 직접 박지 않습니다 — Tailwind utility (`bg-background`, `text-foreground`, `bg-risk-critical/10`) 나 CSS 변수를 사용합니다.

### Neutral 팔레트 (Vercel base)

| 토큰 | Hex | HSL | 용도 |
|---|---|---|---|
| `--background` | `#fafafa` | `0 0% 98%` | 페이지 canvas. 카드가 시각적으로 뜨도록. |
| `--card` | `#ffffff` | `0 0% 100%` | Elevated surface — 카드 · popover · 드로어 본문 · 툴팁. |
| `--foreground` | `#18181b` | `240 6% 10%` | 본문 텍스트. Warm near-black (navy 아님). |
| `--muted` | `#f4f4f5` | `240 5% 96%` | 미묘한 fill — 테이블 헤더 · 사이드바 tint · placeholder · disabled input. |
| `--muted-foreground` | `#6c6c75` | `240 4% 44%` | 보조 텍스트 · caption · 테이블 컬럼 헤더. |
| `--border` | `#e7e7e9` | `240 5% 91%` | Hairline border. 장식용 separator 만 — UI 영역 식별의 유일한 수단이 되지 않습니다. |
| `--input` | `#e7e7e9` | `240 5% 91%` | Input outline. |
| `--primary` | `#18181b` | `240 6% 10%` | Primary CTA — "이 페이지의 중요 액션". |
| `--primary-foreground` | `#fafafa` | `0 0% 98%` | Primary 위 텍스트. |
| `--destructive` | `#dc2626` | `0 72% 50.6%` | Destructive CTA. `--risk-critical`와 같아서 destructive 버튼이 severity와 같은 시각 언어. 소수점이 의미를 갖습니다 — `51%`는 `#dc2828`로 렌더되어 그 hex가 아닙니다. |
| `--destructive-foreground` | `#fafafa` | `0 0% 98%` | Destructive 위 텍스트. |
| `--ring` | `#18181b` | `240 6% 10%` | Focus ring. Primary와 매칭 — outline이 액션과 같은 색 패밀리로 읽힘. |
| `--overlay` | `#18181b` | `240 6% 10%` | 다이얼로그·드로어 뒤의 스크림, 40 % 로 씁니다. `--foreground`가 아니라 별도 토큰인 이유는 스크림이 두 테마 모두에서 덮는 것보다 어두워야 하기 때문입니다. 텍스트는 그렇지 않습니다. |

### Severity (도메인 의미 — 고정)

| 토큰 | Hex | 용도 |
|---|---|---|
| `--risk-critical` | `#dc2626` | Critical CVE · forbidden 라이선스 · build-blocking finding. |
| `--risk-high` | `#ea580c` | High-severity CVE · conditional 라이선스 위험. |
| `--risk-medium` | `#ca8a04` | Medium CVE · 검토 대기 conditional 라이선스. |
| `--risk-low` | `#2563eb` | Low CVE · 정보성 상태. |
| `--risk-info` | `#71717a` | 중립 정보. |

Severity hex는 **릴리스 간 변경하지 않습니다**. 사용처:

- Recharts fill · 차트 범례 (raw hex, `--risk-X` 변수 참조).
- 배지 · 콜아웃 · 격자 셀의 `bg-risk-X/N` tint.
- 버튼 · alert의 border accent (`border-risk-high/40`).

Tint 단계는 정확히 셋이며, 표면은 이 밖의 값을 쓰지 않습니다.

| 단계 | 용도 |
|---|---|
| `/10` | tint를 입힌 표면. 콜아웃·배지·셀의 기본값. |
| `/20` | 같은 표면을 강조할 때. hover 상태, 또는 `/10` 위에 겹치는 칩. |
| `/40` | 두 경우 모두의 테두리. |

불투명도 수식어가 동작하는 이유는 `tailwind.config.ts`가 severity 토큰마다 Tailwind의 `<alpha-value>`를 담은 `color-mix()`로 감싸기 때문입니다. `var()`를 그대로 두면 동작하지 않습니다 — Tailwind가 채널을 분해하지 못하고, 값을 낮춰 내보내는 대신 규칙을 아예 만들지 않아 요소가 투명하게 그려집니다. G0-7이 이 구조를 고치기 전까지 앱의 모든 `bg-risk-X/N`은 아무것도 칠하지 않았습니다. `apps/frontend/tests/unit/design/riskTintOpacity.test.ts`가 이 설정을 실제로 컴파일해, 한 단계라도 규칙이 사라지면 실패합니다.

#### Severity tint 전경색

Severity hex는 차트용 색입니다. tint **위에** 얹는 텍스트는 별도의 짙은 shade가 필요하므로, severity 마다 `-foreground` 짝을 제공합니다. 이 토큰을 쓰고 Tailwind 팔레트 클래스를 직접 쓰지 않습니다 — 같은 상태가 어떤 파일에서는 `text-yellow-800`, 다른 파일에서는 `text-amber-900`으로 갈린 것이 그 방식의 결과입니다.

대비 수치는 전경색이 견뎌야 하는 가장 불리한 조건, 곧 가장 짙은 tint(`/20`)를 페이지 바탕(`--background`, tint가 얹히는 두 표면 중 어두운 쪽) 위에 합성한 값입니다. 일반적인 `/10` 단계를 카드 위에 올리면 각 수치는 대략 1 정도 높아집니다.

| 토큰 | Hex | `bg-risk-X/20` 위 대비 |
|---|---|---|
| `--risk-critical-foreground` | `#b91c1c` | 4.55:1 |
| `--risk-high-foreground` | `#9a3412` | 5.51:1 |
| `--risk-medium-foreground` | `#854d0e` | 5.43:1 |
| `--risk-low-foreground` | `#1d4ed8` | 4.86:1 |
| `--risk-info-foreground` | `#52525b` | 5.80:1 |

```tsx
// 권장
<Badge className="bg-risk-medium/10 text-risk-medium-foreground">KEV</Badge>
// 지양 — severity hex 는 자기 tint 위에서 읽히지 않습니다 (high 는 3.05:1)
<Badge className="bg-risk-medium/10 text-risk-medium">KEV</Badge>
// 지양 — 파일마다 shade 가 갈리고, 대비를 검사하는 장치가 없습니다
<Badge className="bg-risk-medium/10 text-yellow-800">KEV</Badge>
```

`statusTokenContrast.test.ts`는 양쪽을 모두 단언합니다. 각 전경색이 자기 tint 위에서 AA를 넘는다는 것과, severity 토큰 자체는 넘지 **못한다**는 것 — 그래서 "`text-risk-high`를 쓰면 안 되나?" 라는 물음에는 실패하는 테스트가 답합니다.

[Severity 색 접근성](#severity-색-접근성)을 참고하세요.

### 브랜드 액센트 (W14)

로고는 리브랜딩 이후 계속 틸을 써 왔지만 인터페이스까지 내려오지 않아, 제품이 중립색만으로 남아 있었습니다. 이제 액센트에 토큰이 생겼고, 전면이 아니라 **화이트리스트**에만 적용합니다 — 사이드바 활성 행, 탭 활성 밑줄, 진행 표시, 빈 상태, 로그인 관문.

| 토큰 | Hex | 용도 | 대비 |
|---|---|---|---|
| `--brand` | `#0d9488` | 인디케이터·진행·활성 표식 | `--card` 위 3.74:1 (UI, 3 이상) |
| `--brand-subtle` | `#f0fdfa` | 활성 행의 tint 배경 | 본문 텍스트 16.99:1 |
| `--brand-on-ink` | `#2dd4bf` | 로고 자체의 틸 — **잉크 표면 전용**: 글로벌 바와 게이트웨이 브랜드 패널 | `--topbar` 위 9.52:1, 흰 배경 2.49:1 |

`--brand-strong`(teal-700, 액센트를 텍스트로 쓰는 용도)은 W14에서 선언했다가 다시 뺐습니다. 그 색으로 그리는 곳이 없었고, `tokenConsumers.test.ts`는 아무도 쓰지 않는 토큰 선언을 실패로 처리합니다. 쓰이지 않는 색은 실제 표면에서 대비를 확인한 적이 없는 약속이기 때문입니다. 필요한 호출부가 생기면 그때 다시 선언하고 그때 측정합니다.



틸이 둘인 이유는 하나로 두 역할을 못 하기 때문입니다. 로고 틸은 흰 표면에 쓰기엔 너무 밝고, 인터페이스용 짙은 틸은 잉크 바에서 묻힙니다.

**액센트를 쓰지 않는 곳**: risk 색상, `--destructive`, primary CTA, focus ring. severity는 도메인 의미이고, 브랜드로 물들이면 신호가 흐려집니다. 또 위의 모든 적용에서 액센트는 **두 번째** 표식입니다 — 활성 행의 라벨도, 활성 탭의 제목도 잉크색을 유지하므로 상태가 색에만 의존하지 않습니다.

### 글로벌 바 (W14)

셸의 상단이 전체 폭을 차지하며 사이드바와 콘텐츠 위에 놓입니다. 라이트 테마 안의 잉크 표면이라 자체 전경색 계열을 갖습니다 — 페이지 것을 빌려 쓰면 검정 위에 검정이 됩니다.

| 토큰 | Hex | 용도 |
|---|---|---|
| `--topbar` | `#18181b` | 바 배경. `--primary`와 같아 바와 primary CTA가 같은 재질로 읽힙니다 |
| `--topbar-foreground` | `#fafafa` | 주 텍스트·아이콘 — 16.97:1 |
| `--topbar-muted-foreground` | `#a1a1aa` | 보조 텍스트 — 6.91:1 |
| `--topbar-border` | `#27272a` | 하단 경계, 컨트롤 외곽선 |
| `--topbar-accent` | `#27272a` | 바 위의 hover·칩 배경 |

바와 페이지 양쪽에 나타나는 컴포넌트(알림 벨, 언어 토글, ⌘K 트리거, 브랜드 마크)는 복제 대신 `onInk` prop을 받습니다.

### Status surface (동작 · 개체 상태)

Severity와 구분합니다. 스캔이 *실행 중* 이고, 게이트를 *통과* 했고, 자격 증명이 *등록됨* 인 것은 finding의 심각도가 아니라 라이프사이클 상태입니다. 콜아웃과 pill이 팔레트 클래스로 우회하지 않도록 별도 계열을 둡니다.

status 마다 토큰 4종을 제공합니다.

| 접미사 | 계약 |
|---|---|
| `--status-X` | dot · bar 등 **텍스트가 아닌** 표식의 solid fill — WCAG 1.4.11, `--background` 대비 3:1 이상. |
| `--status-X-subtle` | tint 배경. |
| `--status-X-border` | tint 위 테두리. tint와 전경색이 이미 의미를 전달하므로 장식용이며 대비 게이트 대상이 아닙니다. |
| `--status-X-foreground` | tint 위 텍스트 — WCAG AA, 4.5:1 이상. |

| Status | Solid | Subtle | Border | Foreground | 텍스트 대비 |
|---|---|---|---|---|---|
| `success` | `#059669` | `#ecfdf5` | `#6ee7b7` | `#047857` | 5.21:1 |
| `warning` | — | `#fffbeb` | `#fcd34d` | `#92400e` | 6.84:1 |
| `danger` | `#dc2626` | `#fef2f2` | `#fca5a5` | `#b91c1c` | 5.91:1 |
| `info` | — | `#eff6ff` | `#93c5fd` | `#1d4ed8` | 6.16:1 |

solid은 그 색으로 텍스트가 아닌 표식을 실제로 그리는 곳이 있을 때만 선언합니다. `success`는 스캔 완료 진행 바, `danger`는 대시보드 추이 패널의 "신규" 막대가 그 자리입니다. `tokenConsumers.test.ts`는 아무도 쓰지 않는 토큰 선언을 실패로 처리합니다. 그리는 곳이 없는 색은 실제 표면에서 대비를 확인한 적이 없는 약속이기 때문입니다. warning dot이 생기면 그때 solid를 선언하고 그때 측정합니다. 이때 amber-600은 쓸 수 없습니다. `#fafafa` 대비 3.05:1로 3:1 기준선에 너무 가깝습니다.

`--status-danger`는 `--risk-critical` 및 destructive와 같은 hex 입니다. finding의 심각도와 동작의 상태는 서로 다른 질문이므로 이름은 계속 구분하지만, "잘못됐다"를 뜻하는 빨강이 미묘하게 다른 두 가지 빨강일 이유는 없습니다.

```tsx
<Badge
  variant="outline"
  className="border-status-success-border bg-status-success-subtle text-status-success-foreground"
>
  Succeeded
</Badge>
```

:::note PR 시점에 강제됩니다
`npm run token:lint`는 `src/` 아래에 새로 들어온 raw hex 나 Tailwind 팔레트 클래스에서 실패하며, 기록된 baseline은 줄어들기만 합니다. 위 토큰의 대비는 `tests/unit/design/statusTokenContrast.test.ts`가 단언하므로, 대비를 다시 재지 않고 토큰 값만 바꾸면 빌드가 실패합니다. 정의 파일(`index.css`) · 브랜드 마크 · 외부 서비스 아이콘은 예외입니다.
:::

## 다크 테마 (W18)

Zinc가 아니라 slate 입니다. 라이트 테마는 zinc(hue 240, 중립 회색)인데 이것을 그대로 어둡게 옮기면 shadcn을 쓰는 모든 애플리케이션이 함께 쓰는 다크 팔레트가 됩니다. 이 테마는 브랜드의 다크 슬레이트 `#0f172a`의 hue를 물려받아, "다크 모드를 켰다"가 아니라 이 제품으로 읽히게 했습니다.

`<html>`의 `.dark` 클래스로 전환합니다. 사용자 설정은 번들이 로드되기 전에 `index.html`의 인라인 스크립트가 해석합니다. React 안에서 해석하면 첫 프레임이 라이트로 그려져 새로 고침마다 화면이 하얗게 번쩍이기 때문입니다. 상태는 셋(라이트 · 다크 · 시스템 따라가기)이고 글로벌 바에서 순환합니다.

### 테마가 바꾸지 않는 것

- **severity 5색 hex.** 장식이 아니라 도메인 의미입니다(W11 "Severity 색 변경 0"). 다크의 두 표면 모두에서 WCAG 1.4.11이 비텍스트 마크에 요구하는 3:1을 넘습니다. 가장 빠듯한 것은 `--card` 위의 `--risk-low`로 3.38:1 입니다.
- **레이아웃 · radius · 모션 토큰.** 테마는 형태나 타이밍에 관여하지 않습니다.

### 라이트가 거저 얻는 것

라이트에서는 조용히 따라오지만 다크에서는 따로 만들어야 하는 것이 둘 있습니다.

라이트에서 카드를 캔버스 위로 띄우는 것은 **그림자**입니다. 어두운 바탕에서 그림자는 어두운 얼룩이라 아무 일도 하지 않으므로, 카드의 경계는 `--border`가 맡습니다 — 여기서는 `--card` 대비 1.51:1, 라이트에서는 1.23:1 입니다. 라이트에서 장식이던 것이 다크에서는 역할을 갖습니다.

**tint 위 텍스트**는 방향이 뒤집힙니다. 라이트는 severity hue를 짙게 만들고 다크는 밝게 만들어야 하므로, `-foreground`는 400 단계 shade 입니다. 두 바탕과 텍스트를 담는 두 tint 단계를 통틀어 최악이 5.49:1 입니다.

### 중립 팔레트

| 토큰 | 다크 | HSL | 설명 |
|---|---|---|---|
| `--background` | `#080c16` | `223 47% 6%` | 캔버스. 카드 아래. |
| `--foreground` | `#f1f5f9` | `210 40% 96%` | 본문 텍스트 — 캔버스 위 17.84:1. |
| `--card` | `#11192d` | `221 46% 12%` | 브랜드의 다크 슬레이트. 떠 있는 면. |
| `--card-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--popover` | `#17213a` | `223 43% 16%` | 카드보다 위. 페이지 위 카드, 그 위 다이얼로그가 그림자 없이 세 층으로 읽힙니다. |
| `--popover-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--muted` | `#1c2740` | `222 39% 18%` | 테이블 헤더 · 사이드바 tint · disabled input. |
| `--muted-foreground` | `#94a3b8` | `215 20% 65%` | 캔버스 위 7.62:1, 카드 위 6.76:1, `--muted` 위 5.79:1 — 라이트 테마가 걸렸던 그 표면입니다. |
| `--border` | `#2d3b53` | `218 30% 25%` | 카드 경계를 맡습니다. |
| `--input` | `#2d3b53` | `218 30% 25%` | |
| `--primary` | `#f1f5f9` | `210 40% 96%` | 반전 — 카드 슬레이트 위 near-white, 16.30:1로 라이트의 16.97:1과 반올림 차이입니다. |
| `--primary-foreground` | `#11192d` | `221 46% 12%` | |
| `--secondary` | `#1c2740` | `222 39% 18%` | |
| `--secondary-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--accent` | `#1c2740` | `222 39% 18%` | |
| `--accent-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--destructive` | `#f87171` | `0 90.6% 70.8%` | 반전. 아래 설명 참조. |
| `--destructive-foreground` | `#11192d` | `221 46% 12%` | |
| `--ring` | `#94a3b8` | `215 20% 65%` | 카드 위 6.76:1 — 보이지 않는 2 px outline은 focus 표시가 아닙니다. |
| `--overlay` | `#000000` | `0 0% 0%` | 캔버스가 아니라 순수 검정입니다. 캔버스는 이미 어두워서 그것으로 만든 40 % 스크림은 거의 드러나지 않습니다. |

`--destructive`는 두 가지를 겸합니다 — destructive 버튼의 배경, 그리고 shadcn의 `text-destructive`를 통한 오류 문장의 색입니다. 라이트에서는 `#dc2626`이 둘 다 해내지만, 다크 카드 위에서 텍스트로는 3.62:1 입니다. 밝게 바꾸면 텍스트가 해결되고 배경도 관례에 맞으며, 설계 주장도 살아남습니다. 라이트는 `--risk-critical`과, 다크는 `--risk-critical-foreground`와 짝을 이룹니다 — 다크 텍스트에 필요한 밝기의 같은 빨강입니다.

### Risk tint foreground

severity 배경색은 그대로이고 텍스트 shade 만 뒤집힙니다.

| 토큰 | 다크 | 최악 실측 |
|---|---|---|
| `--risk-critical-foreground` | `#f87171` | 5.49:1 |
| `--risk-high-foreground` | `#fb923c` | 6.20:1 |
| `--risk-medium-foreground` | `#facc15` | 8.54:1 |
| `--risk-low-foreground` | `#60a5fa` | 5.66:1 |
| `--risk-info-foreground` | `#a1a1aa` | 5.51:1 |

최악 실측은 다크의 두 바탕과 텍스트를 담는 두 tint 단계(`/10`, `/20`)를 통틀어 잰 값입니다.

:::note 아이콘은 severity hex를 유지합니다
글자를 그리면 `-foreground`를 쓰고, 아이콘이나 장식용 `aria-hidden` 기호이면 severity hex를 그대로 씁니다. 텍스트는 놓이는 표면에 대해 AA를 지켜야 합니다 — `text-risk-medium`은 흰 카드 위에서 2.86:1 이므로, 다크가 생기기 전에도 라이트에서 이미 미달인 호출부가 있었습니다. 마크는 3:1 이면 되고, 두 테마의 모든 표면에서 그것을 넘으며, 알아볼 수 있다는 것이 마크의 존재 이유입니다.
:::

### Status 표면

| Status | Solid | Subtle | Border | Foreground | 텍스트 대비 |
|---|---|---|---|---|---|
| `success` | `#059669` | `#052e22` | `#15803d` | `#4ade80` | 8.48:1 |
| `warning` | — | `#2e2205` | `#a16207` | `#facc15` | 10.18:1 |
| `danger` | `#dc2626` | `#3a0d0d` | `#b91c1c` | `#f87171` | 6.11:1 |
| `info` | — | `#0a1a2e` | `#1d4ed8` | `#60a5fa` | 6.88:1 |

선언된 solid 두 개는 값이 그대로이고 비텍스트 마크로서 3:1을 넘습니다(success 5.19:1, danger 4.05:1). border는 라이트와 마찬가지로 장식이며 게이트 대상이 아닙니다.

### 브랜드 액센트와 글로벌 바

| 토큰 | 다크 | 설명 |
|---|---|---|
| `--brand` | `#2dd4bf` | 다크에서는 마크의 틸이 전면에 섭니다 — 카드 위 9.39:1 이고, teal-600은 4.63:1로 AA는 넘지만 여유가 없고 슬레이트 옆에서 탁하게 보입니다. |
| `--brand-subtle` | `#0b2b28` | 활성 내비게이션 바탕 — 본문 텍스트 13.79:1, 틸 8.11:1. |
| `--brand-on-ink` | `#2dd4bf` | 바는 두 테마 모두 어두우므로 여기서는 두 틸이 같아집니다. |
| `--topbar` | `#17213a` | popover 평면에 둡니다. `#18181b`은 이 캔버스에서 1.06:1로 바가 사라졌습니다. |
| `--topbar-foreground` | `#f1f5f9` | |
| `--topbar-muted-foreground` | `#94a3b8` | |
| `--topbar-border` | `#2d3b53` | 바 대비 1.55:1 — 캔버스와 바를 가르는 것이 이 선입니다. |
| `--topbar-accent` | `#1c2740` | 바 안쪽 hover 바탕. |

틸 두 개의 역할이 뒤바뀌는데, 계획이 예측한 그대로입니다. 이름은 그대로 유효합니다. `--brand`는 "테마가 무엇으로 정하든 그 액센트" 이고, `--brand-on-ink`는 "바 위의 틸" 입니다.

### 미러링 범위

TRUSCA와 BomLens는 계보를 공유하고 서로를 기준으로 검토됩니다. 그 검토에는 경계가 있고, 경계는 여기입니다.

- **중립 토큰은 패밀리 공유 자산입니다.** 캔버스 · 카드 · border · muted · shadcn 시맨틱 세트 — 이것들은 관례이고, 맞춘다고 해서 모방이 아닙니다.
- **액센트 · 다크 팔레트 · 셸 골격은 제품 정체성입니다.** 틸, 슬레이트 다크 테마, 잉크 글로벌 바, 사이드바 그룹 구성. 어느 방향으로도 미러링하지 않으며, 이것을 수렴시키자는 파리티 검토 제안은 제품이 만들려는 차이를 지우자는 제안입니다.

`bomlens-parity-review.md`에 같은 경계가 한 문장으로 들어가 있습니다.

:::note PR 시점에 강제됩니다
`tests/unit/design/tokenDocParity.test.ts`가 위의 모든 표를 `index.css`의 `.dark` 블록과 대조합니다 — 양방향으로, 그리고 한국어 미러와도 대조합니다. `tokenFormatContract.test.ts`는 각 토큰이 `tailwind.config.ts`가 읽는 형식으로 쓰였는지 확인합니다. config가 `hsl(var(--x))`로 읽는 자리에 hex를 두면 `hsl(#f87171)`이 되어 색이 아니고, 그 요소는 배경 없이 렌더됩니다. 두 게이트 모두 그 결함이 실제로 나갈 뻔한 뒤에 만들어졌습니다.
:::

## Spacing

| 토큰 | 값 | 용도 |
|---|---|---|
| `--layout-sidebar` | 256 px | 펼친 사이드바 폭 (기본값). |
| `--layout-topbar` | 56 px | 글로벌 바 높이 — 셸의 최상단, 사이드바 위 전체 폭. |
| `--layout-sidebar-collapsed` | 64 px | 사용자가 사이드바를 접었을 때의 아이콘 전용 레일 폭 (≥`lg`). |
| `--layout-header` | 48 px | 페이지 안 `PageHeader` 행 높이. 셸의 최상단이 아니며, 그쪽은 `--layout-topbar` 입니다. |
| `--table-row` | 40 px | Compact 테이블 행 높이. |

**사이드바 동작.** 좌측 사이드바는 **사용자가 접을 수 있고 뷰포트에 반응**한다:

- **≥ `lg` (1024 px):** 고정 사이드바. 레일 하단의 토글로 256 px → 64 px 아이콘 전용 레일로 접으며, 접힌 라벨은 `aria-label` + 네이티브 hover 툴팁으로 노출된다. 선택 상태는 reload 후에도 유지된다 (`uiStore` → `localStorage` 키 `trustedoss-ui`). 폭은 `--duration-base` 동안 애니메이션된다.
- **< `lg`:** 고정 사이드바는 숨겨지고 헤더 햄버거가 전체 라벨 내비게이션을 담은 오버레이 드로어(좌측 `Sheet`)를 연다. 드로어는 이동·오버레이 클릭·ESC 시 닫힌다.

**카드 padding**은 **16 / 20 / 24 px** (Tailwind `p-4` / `p-5` / `p-6`)로 표준화:

- `p-4` — compact 카드 (대시보드 타일 · stat 카드).
- `p-5` — 표준 카드 (프로젝트 목록 행 · 드로어 섹션).
- `p-6` — 메인 콘텐츠 카드 (페이지 wrapper · 다이얼로그).

## Radius 계층

Affordance 별로 다른 radius — depth가 한눈에 읽힘.

| 토큰 | 값 | Affordance |
|---|---|---|
| `--radius-sm` | 4 px | 작은 input · 배지 · 칩. |
| `--radius` | 6 px | **기본** — 버튼 · 카드 · 테이블 크롬. |
| `--radius-lg` | 8 px | 드로어 · 큰 surface. |
| `--radius-xl` | 12 px | 모달 · 다이얼로그. |

Tailwind config가 `calc()`로 `rounded-sm`/`rounded-md`/`rounded-lg`/`rounded-xl`를 이 토큰에서 파생.

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

**로딩 상태는 spinner가 아닌 스켈레톤.** 장시간 작업 (스캔 · export)은 라벨이 붙은 progress bar — bare spinner 금지.

## Typography

| Element | Family | Size / Weight | Notes |
|---|---|---|---|
| Body | Inter | 14 px / regular | `letter-spacing: −0.005em` (Linear tight body). |
| Heading 1 / 2 / 3 / 4 | Inter | 18 ~ 24 px / semibold | `tracking-tight`. Bold 아님 — semibold가 모던 엔터프라이즈 톤. |
| Mono | JetBrains Mono | 13 px | 코드 · 해시 · CVE ID · PURL · JSON 스니펫. `letter-spacing: 0` — mono는 body tightening 상속 안 함. |

OpenType `rlig` · `calt`가 `body`에 활성화되어 Inter가 제대로 렌더링.

**raw 유틸리티 대신 타이포그래피 프리미티브를 쓰세요.** `apps/frontend/src/components/ui/typography.tsx`가 스케일을 이름 붙인 컴포넌트로 제공하므로, 같은 역할이 화면마다 어긋나지 않고(`text-lg`와 `text-base`가 섞이지 않고) 동일하게 보입니다.

| 컴포넌트 | 요소 | 역할 |
|---|---|---|
| `PageTitle` | `h1` | 페이지 제목 하나 — 18 px semibold tracking-tight. |
| `SectionTitle` | `h2` | 섹션 · 하위 영역 제목 — 16 px semibold. |
| `Subtitle` | `p` | 페이지 제목 아래 muted 보조 줄 — 14 px. |
| `Body` | `p` | 본문 — 14 px (`muted` prop으로 보조 본문). |
| `Caption` | `span` | 밀집 메타(타임스탬프 · 카운트) — 12 px muted. |
| `Eyebrow` | `span` | 대문자 overline · 컬럼 그룹 라벨 — 12 px medium. |

어떤 프리미티브로도 안 되는 일회성 인라인 span 에만 raw `text-*` 유틸리티를 쓰고, 페이지 제목은 절대 직접 조합하지 마세요.

## Focus ring

키보드 navigation 시 모든 interactive element가 가시 focus ring 표시:

```css
focus-visible:outline-none
focus-visible:ring-2
focus-visible:ring-ring
focus-visible:ring-offset-2
```

`--ring`이 `--primary`와 같아서 outline이 액션과 같은 색 패밀리로 읽힘. `ring-offset-2`가 2 px 숨돌이를 만들어 tinted 배경 (severity 배지 · alert 카드) 위에서도 ring이 보입니다.

**Focus ring을 절대 끄지 마세요.** 2 px outline은 키보드 사용자의 1차 affordance — 제거하면 UI가 도달 불가.

## 컴포넌트 규약

포털은 [shadcn/ui](https://ui.shadcn.com/) primitives 위에 빌드됩니다. 각 primitive는 위 토큰에 연결되어 `apps/frontend/src/components/ui/`에 re-export.

### Page header

`apps/frontend/src/components/PageHeader.tsx`

모든 라우트는 헤더를 `PageHeader`로 렌더링해 제목 타이포그래피와 헤더 chrome을 동일하게 맞춥니다. chrome은 `bg-background` + `border-b`(off-white 캔버스에 가는 구분선)로 통일해, 아래의 흰 카드 · 테이블이 떠 보이게 합니다. 두 가지 형태:

- `variant="stacked"`(기본) — 더 높은 헤더(`py-4`)에 `PageTitle`과 muted `description`. 설명 줄이 필요한 페이지(Scans, Admin 영역).
- `variant="bar"` — 48 px 슬림 행(`var(--layout-header)`), 제목과 선택적 오른쪽 `actions` 슬롯(버튼 또는 메타 텍스트), 부제 없음. 목적이 자명한 밀집 페이지(Dashboard, 프로젝트 목록).

stacked 변형에는 선택적 `meta` 슬롯도 있습니다 — 부제 아래에 오는 블록("2분 전 갱신" 같은 자체 test id 가진 줄)으로, 블록 내용을 부제 `<p>` 안에 중첩하지 않도록 `description`과 분리합니다. `actions` 슬롯은 호출 측 마크업이라 버튼 · 메타의 기존 하네스 `data-testid`가 보존됩니다.

`<header><h1>` 블록을 직접 만들지 말고, 정말 새 레이아웃이 필요하면 `PageHeader`를 확장하세요. **예외:** detail 페이지(프로젝트 상세, 컴포넌트 · 취약점 상세, Compare, 스캔 상세)는 *브레드크럼 헤더*(breadcrumb `<nav>` + 맥락 제목)를 쓰며, 이는 `PageHeader`가 아직 모델링하지 않은 별도 archetype 입니다. 이 페이지들은 직접 짠 헤더를 유지하되 타이포는 같은 스케일을 따릅니다.

### Button

`apps/frontend/src/components/ui/button.tsx`

- 기본 variant: `bg-primary text-primary-foreground` — solid warm near-black.
- `outline` variant: `border-input bg-background` — 보조 액션.
- `ghost` variant: 배경 없음, hover tint 만 — nav 항목 · toolbar 액션.
- `destructive`: `bg-destructive` — Critical-aligned 빨강, 되돌릴 수 없는 액션 전용 (delete · revoke · reject).
- Hover / focus transition: `transition-colors duration-fast ease-out` (150 ms).
- 모든 variant에 focus ring 포함.

### Input / Select / Checkbox

- Border 색 `--input` · focus ring `--ring`.
- Disabled 상태: `bg-muted text-muted-foreground`.
- Error 상태: `border-destructive` + `aria-live="polite"` 메시지가 필드 아래.

### Card

- Off-white canvas 위 순백 surface (`bg-card`) — 무거운 shadow 없이 시각적으로 뜸.
- 기본 `rounded-md` (6 px) · 메인 콘텐츠 카드는 `rounded-lg` (8 px).
- Stat / 타일은 `shadow-sm` · 떠 있는 popover는 `shadow-md`.

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
- 드로어는 **빠른 확인** 용 — 표 행의 전체 payload · CVE의 CVSS 분해 · 컴포넌트의 라이선스 체인. 페이지 nav는 **깊은 작업** 용 — 일괄 편집 · 다단계 승인 · 스캔 설정.

### Dialog

- `bg-foreground/40` backdrop 위 중앙 정렬 모달.
- `rounded-xl` (12 px) · `shadow-lg`.
- **Destructive confirmation** (프로젝트 삭제 · API key revoke)과 **인라인 생성 플로우** (새 프로젝트 · 새 팀) 전용.

### EmptyState

`apps/frontend/src/components/EmptyState.tsx`

- 중앙 정렬, max-width 420 px.
- 레이어드 아이콘 메달리온 (W12-D) — 부드러운 동심 muted 링 둘 뒤에 떠 있는 흰 안쪽 원판이 아이콘을 담음 — 그 아래 타이틀 (semibold) · 설명 (muted) · 단일 primary CTA. `illustration`을 넘기면 메달리온 대신 더 풍부한 인라인 SVG로 교체(인라인만, 새 에셋 없음).
- 용도: 빈 목록 · 빈 검색 결과 · 빈 드로어 탭 · 첫 사용 온보딩 카드.

### Skeleton

`apps/frontend/src/components/ui/skeleton.tsx` · `skeletons.tsx`

- `Skeleton`은 기본 바(`animate-pulse` · `rounded-sm`). 전폭 바 하나보다, 최종 레이아웃을 닮은 composite 스켈레톤을 써서 콘텐츠가 reflow 없이 자리잡게 합니다.
- `TableRowsSkeleton`은 로딩 테이블에 컬럼별 셀(컬럼당 너비 하나)을 렌더링. 테이블은 `aria-busy` 유지, 스켈레톤 행은 `aria-hidden`.

### Badge

`apps/frontend/src/components/ui/badge.tsx`

Risk-tinted variant는 상태 단어와 디자인 시스템 색을 짝지움. 배경은 `bg-risk-X/10` — 칩이 색 tint로 읽히도록. 텍스트는 그 severity의 `-foreground` 토큰 — 렌더링 대비가 WCAG AA 4.5:1을 통과 — [Severity 색 접근성](#severity-색-접근성) 참고.

### Toast

`apps/frontend/src/components/ui/toast.tsx`

`AppProviders`에 마운트된 단일 `<ToastProvider>`가 우하단에 쌓이는 영역 하나를 렌더링하고, `useToast().toast(text, opts)`로 어디서든 띄웁니다. 토스트는 큐로 쌓이고 자동으로 사라지며(4초) `aria-live` 영역으로 안내됩니다.

- **피드백 규칙.** 성공 · 비차단 알림은 토스트, 폼 검증 에러는 필드 옆 **인라인**(RFC 7807 `detail`) — 사용자가 놓칠 토스트로 쓰지 않습니다.
- **test-id 계약.** `testId` 기본값은 `"admin-toast"` 이고 토스트는 `data-tone` + `data-toast-key`를 달아, 모든 e2e 하네스가 선택하는 마크업(`[data-testid="admin-toast"][data-tone][data-toast-key]`)을 그대로 냅니다. `tone`(`success` / `error`)과 locale 독립 `key`를 넘깁니다. ScanCancelButton 만 `testId: "scan-cancel-toast"`로 덮어씁니다.
- **예외.** 두 표면은 자체 로컬 토스트를 유지합니다: 스캔 상세의 다운로드 알림(success / error 톤이 아닌 중립 `data-toast-variant`)과 Settings 탭의 인라인 `settings-toast` 저장 확인. 둘 다 자체 테스트 계약이 있고 success / error 모델에 맞지 않습니다.

## 마이크로인터랙션 가이드

W11-F polish phase가 모든 인터랙티브 transition의 타이밍 · easing을 표준화. 컴포넌트는 토큰에서 motion을 가져옵니다 — 새 값을 hand-roll 하지 않습니다.

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
| 라우트 전환 진입 | 250 ms (`--duration-slow`) | `--ease-out` | `opacity` (`<main>`을 pathname으로 key) |
| 스켈레톤 pulse | 2000 ms loop (`animate-pulse`) | `ease-in-out` | `opacity` |

**브라우저 기본 easing 사용 금지.** 항상 `--ease-out` 참조 — 모션이 제품 전반에 걸쳐 단일 언어로 읽혀야 합니다.

**Reduced motion.** `index.css`의 전역 `@media (prefers-reduced-motion: reduce)` 가드가 위의 모든 애니메이션 · transition을 ~0으로 줄이고(부드러운 스크롤도 끔), reduced motion을 요청한 사용자는 즉시 상태 변화를 받습니다 — [접근성](#접근성) 참고.

## 접근성

포털은 **WCAG 2.1 Level AA**를 목표로 합니다. 세 가지 정책으로 구체화.

### 색 대비 — 본문 4.5:1 · UI 3:1

| 쌍 | 비율 | 비고 |
|---|---|---|
| `--foreground` on `--background` | 16.97:1 | 본문. AAA. |
| `--foreground` on `--card` | 17.72:1 | 카드 위 본문. AAA. |
| `--muted-foreground` on `--background` | 4.98:1 | Caption · 보조 텍스트. AA. |
| `--muted-foreground` on `--card` | 5.20:1 | 카드 위 caption. AA. |
| `--primary-foreground` on `--primary` | 16.97:1 | Primary 버튼 라벨. AAA. |
| `--destructive-foreground` on `--destructive` | 4.63:1 | Destructive 버튼 라벨. AA. |
| `--ring` on `--background` | 16.97:1 | Focus ring. AAA. |

장식용 border (`--border` on `--background`, 1.20:1)는 **의도적으로 저대비** — 시각 분리용일 뿐 정보성 UI 요소가 아니며 WCAG 1.4.11이 면제 대상.

### Severity 색 접근성

Severity hex (`#dc2626` / `#ea580c` / `#ca8a04` / `#2563eb` / `#71717a`)는 브랜드 고정. Light tint 위 **본문 텍스트**로 쓰이면 2.5:1까지 떨어져 AA 실패. 해결은 색이 아닌 구조 — severity 톤이 텍스트로 쓰일 때 렌더링 텍스트 색은 같은 hue의 더 짙은 shade, 곧 그 severity의 `-foreground` 토큰입니다.

| Tone | Tint 배경 | 텍스트 색 | 카드 위 대비 |
|---|---|---|---|
| `critical` | `bg-risk-critical/10` | `text-risk-critical-foreground` (`#b91c1c`) | 5.54:1 |
| `high` | `bg-risk-high/10` | `text-risk-high-foreground` (`#9a3412`) | 6.46:1 |
| `medium` | `bg-risk-medium/10` | `text-risk-medium-foreground` (`#854d0e`) | 6.21:1 |
| `low` | `bg-risk-low/10` | `text-risk-low-foreground` (`#1d4ed8`) | 5.82:1 |
| `info` | `bg-risk-info/10` | `text-risk-info-foreground` (`#52525b`) | 6.85:1 |

G0-7 이전에는 이 색들을 Tailwind 팔레트 클래스(`text-red-700`)로 적고 측정값은 코드 주석에 남겼습니다. 지금은 토큰이므로 같은 조합을 어디서나 재사용하고, 위 수치는 주석이 아니라 테스트에서 나옵니다.

**Dot 인디케이터** (SeverityBadge · 차트 범례 · status pill)는 계속 raw `bg-risk-X` 토큰 사용 — 색 정체성은 그대로, 텍스트 shade 만 어둡게. 참조 구현은 `apps/frontend/src/components/ui/badge.tsx`.

한 가지는 분명히 적어 둡니다. `--muted-foreground`는 페이지 바탕 위에서 AA를 0.03 차이로 통과하므로, risk tint 위에서는 통과하지 못합니다. tint를 입힌 콜아웃 안의 보조 텍스트는 `text-foreground`를 쓰고 위계는 크기와 굵기로 표현합니다.

### 색이 신호의 단독 수단 아님

Severity가 표시되는 모든 곳에서 색은 다음 중 하나와 짝지움: 텍스트 라벨 ("Critical") · Lucide 아이콘 (`ShieldAlert`, `TriangleAlert`) · dot + 라벨 조합. 포털은 greyscale 에서도 사용 가능해야 합니다.

### 키보드 navigation

모든 interactive 요소는 `Tab`으로 도달 가능 · `Enter` / `Space`로 조작 가능. 포털은 열린 `Dialog` 내부 외에는 focus를 trap 하지 않습니다 (Dialog의 focus-trap은 의도된 것).

- 사이드바 링크: `Tab`으로 가시 항목 순환.
- 글로벌 바, DOM 순서: 메뉴(`lg` 미만) · 브랜드 · 팀 스위처 · 검색 트리거 · 알림 · locale 토글 · 프로필 · 로그아웃 — 전부 `Tab` 도달 가능. 팀 스위처는 `menuitemradio` 그룹이라 현재 팀이 장식용 체크 표시가 아니라 소리로 전달됩니다.
- 테이블 행: 드로어를 여는 행은 `<button>` 또는 `<a>`로 렌더링 · `Enter`로 활성화.
- 드로어: `Esc`로 닫기 · 첫 tabstop은 `X` 닫기 · 드로어 열려 있을 때 `Tab`이 패널 안에서 순환.
- 다이얼로그: 드로어와 같은 패턴 + focus-trap. `Esc`로 취소.
- Active filter chip: 각 칩의 `×`가 `<button>` · `Tab` 도달 가능.
- 콤보 박스 (`Select` · 검색): `↑ ↓`로 옵션 이동 · `Enter`로 확정 · `Esc`로 닫기.

### Form

- 모든 `<input>`은 `<label>`과 연결 — 시각적이거나 `aria-label`로.
- 에러 메시지는 필드 옆 `<p role="alert" aria-live="polite">`.
- 필수 필드는 라벨 옆 `*`와 `aria-required="true"`.
- 검증은 blur와 submit에서 — 모든 키입력마다 X (스크린리더 chatter).

### Live region

- Toast 알림은 `aria-live="polite"`.
- 스캔 progress bar는 `aria-live="polite"` · 단계 전환 시 라벨 업데이트 ("컴포넌트 탐지" → "CVE 매칭" → "보고서 생성").
- 장기 CI build-gate 출력은 `aria-live="polite"` — 스크린리더가 stage 전환을 알림.

## 변경 이력

| Wave | 일자 | 변경 |
|---|---|---|
| W11-A | 2026-05-27 | 토큰 재정의 — Vercel base + Linear polish. Primary `#0f172a` → `#18181b` · 배경 `#ffffff` → `#fafafa` · 새 radius / shadow / motion / focus-ring 토큰. |
| W11-B | 2026-05-27 | Foundation re-skin — Button / Input / Select / Card / Badge 새 토큰 · Project list가 첫 prototype 화면. |
| W11-C | 2026-05-27 | Table / Drawer / Dialog re-skin (PR #244). |
| W11-D | 2026-05-27 | 차트 re-skin — Recharts grid / axis / tooltip 토큰 (PR #245). |
| W11-E | 2026-05-27 | 8 EN + 3 KO before-after PNG 비교 (PR #246). |
| W11-F | 2026-05-27 | 마이크로인터랙션 polish — hover / focus / motion (PR #247). |
| W11-G | 2026-05-27 | 빈 상태 일러스트 (PR #248). |
| W11-H | 2026-05-27 | **A11y sweep + 디자인 시스템 문서.** Severity 배지 텍스트 색을 light tint 위 WCAG AA 통과로 짙게 (토큰 변경 없음). 본 페이지 추가. |
| W12-A | 2026-06-11 | **Craft 격상 — 타이포그래피 · 페이지 헤더 체계.** 타이포그래피 프리미티브(`PageTitle` · `SectionTitle` · `Subtitle` · `Body` · `Caption` · `Eyebrow`)와 공용 `PageHeader`(stacked · bar) 추가. 화면마다 어긋났던 페이지 제목 스케일(`text-lg` 대 `text-base`)과 헤더 chrome(`bg-card` 대 `bg-background`)을 통일. |
| W12-B | 2026-06-11 | **Craft 격상 — 전역 토스트.** `ToastProvider` + `useToast()`(큐 · 자동 사라짐 · `aria-live`) 추가, 손으로 짠 페이지별 토스트 11곳을 이전하면서 `admin-toast` / `data-toast-key` e2e 계약 보존. 스캔 상세 다운로드 알림 + Settings 인라인 확인은 문서화된 예외로 유지. |
| W12-C | 2026-06-11 | **Craft 격상 — 모션 (CSS-only).** 라우트 전환 진입 페이드(`<main>`을 pathname으로 key, 250 ms), 사이드바 접기 250 ms 정렬, 전역 `prefers-reduced-motion` 가드. 새 의존성 없음(tailwindcss-animate 만). 스켈레톤 문서를 실제 2000 ms `animate-pulse`로 정정. |
| W12-D | 2026-06-12 | **Craft 격상 — 빈 상태 · 로딩 폴리시.** EmptyState에 레이어드 아이콘 메달리온 + 선택적 `illustration` 슬롯 추가, 신규 `TableRowsSkeleton`이 Scans · Admin Users 테이블에서 컬럼별 로딩 셀(전폭 바 대체)을 렌더링. |
| W12-E/F | 2026-06-12 | **Craft 격상 — 가드레일 + 문서.** `/dev/design-preview`를 살아있는 컴포넌트 레퍼런스(타이포그래피 · 배지 · 빈 / 로딩 · 피드백)로 확장하고, 기여자 coding standards에 "프론트엔드 UI" 섹션 추가. 시각 회귀 베이스라인 확장(4 → ~15)은 CI / 운영자 후속 — darwin 개발 머신에서는 올바른 linux 베이스라인을 생성할 수 없음. |

이전 "enterprise-style 2015" 미감 (`#0f172a` navy · 순백 canvas · 일관 8 px radius · shadow 없음 · 브라우저 기본 easing)은 W11로 완전 은퇴.

## 참고

- [아키텍처](./architecture.md) — backend / frontend / 스캔 파이프라인 개요.
- [코딩 표준](../contributor-guide/coding-standards.md) — 포맷·린트·커밋 규약.
- 여기 정리된 디자인 결정은 프로젝트 내부 기획 문서와 함께 관리되며, 그 문서들은 이 사이트로 공개되지 않습니다. 토큰 계약은 이 페이지가 공개 기준입니다.
