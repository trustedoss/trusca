# 두 TrustedOSS 사이트 전략 — 가이드(루트) vs 포털(제품 문서)

> 상태: **결정 완료** · 작성일 2026-06-04 · 범위: 방향 결정 + 향후 작업 지도 (이 문서 자체는 코드/링크 변경 없음)
>
> 이 문서는 내부 전략 문서다. Docusaurus 빌드 대상(`docs-site/`)이 **아니므로** 공개 사이트에는 노출되지 않는다.

---

## 1. 배경

`https://trustedoss.github.io/` 와 우리 제품 문서는 **같은 도메인을 공유하지만 서로 다른 두 사이트**다. 루트 사이트의 정체를 확인하던 중, 두 사이트의 관계와 "별도로 가져갈지 통합할지" 방향이 어디에도 기록돼 있지 않다는 갭이 드러났다. 본 문서로 그 방향을 고정한다.

---

## 2. 두 사이트 식별

| 항목 | 루트 가이드 (`/`) | 포털 문서 (`/trustedoss-portal/`) |
|---|---|---|
| URL | `https://trustedoss.github.io/` | `https://trustedoss.github.io/trustedoss-portal/` |
| 소유 레포 | **별도 org-pages 레포** (`trustedoss/trustedoss.github.io`) — **이 워크스페이스에 없음** | 이 모노레포 `docs-site/` |
| 생성기 | Docusaurus (커스텀) | Docusaurus (커스텀) |
| 콘텐츠 성격 | 벤더 중립 거버넌스 가이드 — OpenChain / ISO IEC 5230·18974 기반 (체계구축·DevSecOps·AI코딩·레퍼런스) | 특정 제품(Apache-2.0)의 설치·운영·CI 연동·API 문서 |
| 갱신 주기 | 에버그린 (상시) | **제품 릴리스에 종속** (현재 v0.10.0) |
| 코드 결합 | 없음 (독립 콘텐츠) | **강함** — `.github/workflows/docs.yml` 이 FastAPI 백엔드에서 OpenAPI 스펙을 재생성해 배포 |
| 기본 언어 | 한국어 중심 | EN 기본 + KO (`docs-site/docusaurus.config.ts:31-38`) |
| 상호 링크 | 포털로 가는 링크 **0** | 가이드로 가는 링크 **0** (유일한 외부 링크는 GitHub repo) |

**두 핵심 사실**
- **루트는 이 레포가 아니다.** 별도 org-pages 레포가 서빙한다.
- **포털이 하위 경로(`/trustedoss-portal/`)인 것은 버그가 아니라 의도된 설계다.** GitHub Pages 토폴로지상 `<org>.github.io` 루트는 org-pages 레포가, 프로젝트 레포는 하위 경로가 점유한다 (`docs-site/docusaurus.config.ts:16-17`).

---

## 3. 방향 결정 — 레포는 별도 유지, 경험은 통합

"통합"은 두 축으로 분리해야 오해가 없다.

- **레포 축** (두 레포를 하나로 합칠 것인가) → **아니오. 별도 유지.**
- **경험 축** (두 사이트를 하나의 브랜드/내비로 묶어 보이게 할 것인가) → **예. 통합.**

### 3.1 레포는 별도 유지 (근거)

1. **빌드 결합의 비대칭.** 포털 문서는 OpenAPI를 백엔드에서 재생성하고 제품 버전과 함께 릴리스되므로 **반드시 제품 모노레포 안에** 있어야 한다. 반대로 가이드(표준 해설)는 제품 릴리스 사이클에 종속되면 안 된다. 합치면 에버그린 콘텐츠가 제품 CI/릴리스에 인질이 된다.
2. **포지셔닝.** 가이드의 가치는 OpenChain/ISO 기반의 **벤더 중립 권위**다. 제품 레포 안으로 들어가면 "제품 마케팅"으로 읽혀 프론트(why)로서의 신뢰가 깎인다. "가이드=프론트, 포털=제품" 모델은 가이드의 편집 독립성 위에서만 성립한다.
3. **현재 토폴로지가 이미 정답.** org-pages=루트, project=하위 경로. 바꿀 이유가 없다.

### 3.2 경험은 통합 (근거)

같은 도메인 + 둘 다 Docusaurus인데 상호 링크가 0이라 사용자 동선이 끊긴다. 가이드에 도착한 사람은 도구를 못 찾고, 포털에 온 사람은 맥락(why)이 없다. 양방향 링크 + 공통 브랜딩 + 역할 분담 명문화로 "한 제품군"처럼 보이게 한다.

---

## 4. 관계 모델

```
가이드 (why / 체계구축의 입구)
   │  "표준 기반 OSS 관리체계를 구축하려면"
   │  "그래서 실제 스캔·SBOM·라이선스 도구가 필요하다면"
   ▼
포털 (how / 실제 도구)
   설치 · 스캔 · 운영 · CI 연동 · API
```

- **가이드 = 프론트**: OpenChain/ISO/DevSecOps/AI코딩 — *왜*, *무엇을* 갖춰야 하는가.
- **포털 = 제품 문서**: TrustedOSS Portal로 *어떻게* 실행하는가.

---

## 5. 디자인 패리티 — 경험 통합의 선행 조건

cross-link 만으로는 부족하다. 같은 도메인에서 두 사이트의 브랜드 시그니처가 어긋나면 "한 제품군"이 아니라 "다른 두 사이트"로 읽힌다. **최소한 주색상 + 로고 + footer 를 맞추기 전에 cross-link 를 붙이면 이음매가 그대로 드러난다.**

좋은 출발점: **둘 다 커스텀된 Docusaurus** 라서(어느 쪽도 디폴트 템플릿 아님) 통합은 밑바닥 재설계가 아니라 **토큰 정합** 수준이다.

### 5.1 현재 격차

| 항목 | 포털 문서 | 루트 가이드 | 비고 |
|---|---|---|---|
| 주색상 | 딥 네이비 `#0f172a` (Vercel/Black Duck) | 틸/시안 계열 (추정) | **가장 큰 단일 신호** — 헤더만 봐도 다른 사이트로 인지 |
| 폰트 | Inter + JetBrains Mono (명시, `docs-site/src/css/custom.css`) | 일반 sans-serif (Infima 기본 추정) | |
| 아이콘 톤 | 텍스트 주도, 이모지 0 (Linear/Vercel 절제) | 이모지 🏗️🔒🤖 (따뜻·캐주얼) | 레지스터 충돌 |
| 컬러모드 | light 기본 + 다크 토글 ON | 미상 | |
| 기본 언어 | EN 기본 | KO 중심 | |
| 로고 | 사이트별 자체 lockup | 사이트별 자체 lockup | |

> 신뢰도 한계: 가이드의 정확한 hex·폰트·다크모드는 렌더된 페이지만으로는 확정 불가. 정밀 감사는 가이드 레포의 `src/css/custom.css` + `docusaurus.config.ts` 가 필요하며, 그 레포는 이 워크스페이스에 없다.

### 5.2 권고 — 단일 브랜드 토큰

TrustedOSS **단일 브랜드 토큰 세트**(색·폰트·로고·footer·아이콘 톤)를 정하고 양쪽이 공유한다. 포털은 이미 `CLAUDE.md` + `docs-site/docs/reference/design-system.md` 라는 엄격히 문서화된 디자인 시스템을 보유하므로, **포털 토큰을 정본(canonical)으로 삼고 가이드를 거기에 맞추는 방향**이 자연스럽다. 가이드는 콘텐츠 톤의 따뜻함은 유지하되 팔레트/폰트/로고만 정합한다.

### 5.3 가이드 레포 확보 시 대조할 디자인 패리티 체크리스트

- [ ] 주색상(`--ifm-color-primary`)을 `#0f172a` 로 정합
- [ ] 폰트 스택을 Inter (본문) + JetBrains Mono (코드)로 정합
- [ ] 로고 lockup·favicon 통일
- [ ] footer 구조·카피라이트 톤 정합 ("Copyright © … TrustedOSS")
- [ ] 컬러모드(light 기본, 다크 토글 정책) 일치
- [ ] 아이콘 레지스터 결정(이모지 유지 vs 절제된 아이콘셋)
- [ ] navbar 항목 순서·언어 토글 동작 일치

---

## 6. 경험 통합 청사진 (미구현, 향후 작업 지도)

### 6.1 포털 → 가이드 (이 레포에서 가능)

- **navbar 우측**: `docs-site/docusaurus.config.ts:99-137` 의 `items` 배열, GitHub 항목(`:132-136`) 옆에 추가
  ```ts
  { href: "https://trustedoss.github.io/", label: "Guide", position: "right" },
  ```
- **footer**: `docs-site/docusaurus.config.ts:139-177` 의 "Project" 섹션(`:151-167`) 또는 신규 "TrustedOSS" 섹션에 가이드 링크.
- **i18n 동기화**: KO navbar/footer 라벨을 `docs-site/i18n/ko/...` 에서 맞춘다 (프론트 i18n no-plural 규칙 준수).

### 6.2 가이드 → 포털 (별도 org-pages 레포, 워크스페이스 밖)

- 가이드의 "도구 / DevSecOps" 섹션에서 포털 문서(`/trustedoss-portal/`)로 연결. **퍼널상 더 가치 큰 방향**(why → how)이다.
- 그 레포 작업이라 본 레포 PR로는 처리 불가 → 구체 지시는 **§7 (가이드 레포 관리자 핸드오프)** 에 자립적으로 정리했다.

---

## 7. 가이드 레포 관리자에게 — 해야 할 작업 (핸드오프)

> 이 섹션은 `trustedoss/trustedoss.github.io` (루트 가이드 사이트) 레포 관리자에게 그대로 전달하기 위한 자립적 작업 지시다. 위 맥락을 모두 읽지 않아도 이 섹션만으로 실행할 수 있다.

**누가 / 왜**: TrustedOSS Portal(제품)과 가이드 사이트는 같은 도메인(`trustedoss.github.io`)을 공유하는 한 제품군이다. 현재 두 사이트는 서로 링크가 없고 디자인 시그니처도 어긋나, 사용자에게 "별개의 두 사이트"로 보인다. 아래 작업으로 **(A) 가이드 → 포털 동선**과 **(B) 공통 브랜드 외양**을 맞춘다. 양쪽 다 Docusaurus라 밑바닥 재설계가 아니라 토큰/링크 정합 수준이다.

### 7.1 가이드 → 포털 cross-link (필수)

가이드 사이트에서 제품 문서로 가는 경로를 만든다.

- **navbar**: `docusaurus.config.ts` 의 `themeConfig.navbar.items` 에 외부 링크 추가
  ```ts
  { href: "https://trustedoss.github.io/trustedoss-portal/", label: "Portal", position: "right" },
  ```
- **footer**: `themeConfig.footer.links` 에 "TrustedOSS Portal" 항목 추가 (`href` 동일).
- **콘텐츠 인라인 링크**: "DevSecOps / 도구 / SCA" 관련 문서 본문에서, 실제 스캔·SBOM·라이선스·CI 게이트를 실행하는 도구로 포털 문서를 연결한다. 이게 *why → how* 퍼널의 핵심 방향이다.
  - 제품 문서 진입점들:
    - 설치: `https://trustedoss.github.io/trustedoss-portal/docs/quickstart`
    - CI 연동: `https://trustedoss.github.io/trustedoss-portal/docs/ci-integration/github-actions`
    - API 레퍼런스: `https://trustedoss.github.io/trustedoss-portal/reference/api`

### 7.2 공통 브랜드 토큰 정합 (디자인 패리티)

포털이 정본(canonical) 디자인 시스템을 보유하므로 가이드를 거기에 맞춘다. 가이드 콘텐츠의 따뜻한 톤(이모지 등)은 유지해도 되지만, **주색상·폰트·로고·footer** 는 맞춰야 같은 제품군으로 읽힌다.

`src/css/custom.css` 의 `:root` 블록을 아래로 정합 (포털 `docs-site/src/css/custom.css` 와 동일):

```css
:root {
  --ifm-color-primary: #0f172a;          /* deep navy */
  --ifm-color-primary-dark: #0b1220;
  --ifm-color-primary-darker: #0a1020;
  --ifm-color-primary-darkest: #07101c;
  --ifm-color-primary-light: #1e293b;
  --ifm-color-primary-lighter: #334155;
  --ifm-color-primary-lightest: #475569;

  --ifm-font-family-base:
    "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --ifm-font-family-monospace:
    "JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace;
}
```

다크모드를 쓴다면 `[data-theme="dark"]` 의 primary 도 포털과 맞춘다 (`--ifm-color-primary: #93c5fd` 계열).

### 7.3 체크리스트

- [ ] navbar 에 "Portal" 외부 링크 추가 (7.1)
- [ ] footer 에 "TrustedOSS Portal" 링크 추가 (7.1)
- [ ] DevSecOps/도구 본문에서 포털 진입점으로 인라인 링크 (7.1)
- [ ] 주색상(`--ifm-color-primary`) `#0f172a` 정합 (7.2)
- [ ] 폰트: Inter (본문) + JetBrains Mono (코드) (7.2)
- [ ] 로고 lockup·favicon 통일 (포털 `docs-site/static/img/logo.svg`·`favicon.svg` 참고)
- [ ] footer 카피라이트 톤 정합 (`Copyright © <year> TrustedOSS …`)
- [ ] 컬러모드 정책 일치 (포털: light 기본 + 다크 토글 ON)
- [ ] 아이콘 레지스터 결정 (이모지 유지 vs 절제된 아이콘셋 — 가이드 재량)

### 7.4 협의가 필요한 항목

- **공통 navbar/로고 완전 통일** 여부 — 두 사이트에 동일 헤더를 둘지(완전 통합), 링크만 둘지(느슨한 연결)는 양측 합의 사항.
- 단일 브랜드 토큰을 **공유 패키지/CSS** 로 뺄지 — 지금은 각자 `custom.css` 에 복제하는 방식으로 충분.

---

## 8. 열린 질문 / 다음 단계

1. **포털측 경험통합 PR** — §6.1 navbar/footer 링크 + i18n. 단, §5 디자인 패리티(최소 주색상)와 묶어 진행할지 결정.
2. **org-pages 레포 접근** — 가이드→포털 링크와 디자인 정합의 절반은 그 레포에서 해야 한다. 레포 경로/접근 확보 필요.
3. **공통 헤더 통일 여부** — 두 사이트에 동일 navbar/로고를 둘지(완전 통합) vs 링크만 둘지(느슨한 연결).
