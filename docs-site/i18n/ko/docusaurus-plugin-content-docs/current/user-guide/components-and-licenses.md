---
id: components-and-licenses
title: 컴포넌트와 라이선스
description: 스캔이 발견한 컴포넌트를 탐색하고 declared·concluded 라이선스를 검토하며 허용·조건부·금지 분류에 따라 행동합니다.
sidebar_label: 컴포넌트·라이선스
sidebar_position: 3
---

# 컴포넌트와 라이선스

스캔이 끝나면 프로젝트의 **Components** 탭에 파이프라인이 발견한 모든 패키지와 스캔이 부여한 라이선스가 나열됩니다. 본 문서는 테이블 읽기, 라이선스 분류 모델, **declared** 와 **detected** 라이선스의 차이, 포털이 추적하는 의무사항을 다룹니다.

:::note 대상 독자
의존성 위생 분류를 수행하는 엔지니어; 라이선스를 검토하는 법무·컴플라이언스 리뷰어. 읽기는 팀 멤버십, 변경(억제·수동 concluded 라이선스)은 `developer` 이상 필요.
:::

## 컴포넌트 테이블

![프로젝트 상세 — 가상 스크롤 행, severity 필터, 라이선스 카테고리 배지가 있는 Components 탭](/img/screenshots/user-components-list.png)

컬럼:

- **컴포넌트 (Component)** — 패키지 이름(예: `lodash`, `org.springframework:spring-web`).
- **타입 (Type)** — **Direct** / **Transitive** / `—`. 의존성 그래프 깊이를 요약한 색상 배지: `Direct`(깊이 1, 본인이 선언) 대 `Transitive`(깊이 2+, 다른 패키지가 끌어옴), `—` 는 그래프를 기록하지 못한 생태계 또는 그래프 정보 없이 만들어진 이전 스캔. [직접 vs. 전이](#dependency-depth) 참고.
- **버전 (Version)** — 매니페스트나 락파일에서 고정된 버전.
- **라이선스 (License)** — 컴포넌트에 부여된 라이선스. 의존성의 경우 `cdxgen` 이 패키지 메타데이터에서 읽은 **declared** 라이선스입니다. detected·concluded 와의 관계는 [declared vs. detected](#declared-vs-detected) 참고. 빌드 게이트가 사용하는 값입니다.
- **Usage** — **Required** / **Optional** / `—`. `cdxgen` 이 본 컴포넌트로 가는 *가장 얕은* 경로에서 기록한 의존성 스코프(같은 컴포넌트가 여러 경로로 도달 가능하면 가장 높은 스코프 — `Required` > `Optional` — 가 이김). `—` 는 스캐너가 스코프를 emit 하지 않은 경우. Optional 의존성은 Required 와 같은 법적 의무를 가지는 경우가 많지만, **Required / Optional** 구분은 라이선스 컴플라이언스 부담에 매핑됩니다 — 사용하지 않는 `Optional` extra 는 깊이 박힌 required transitive 의존성보다 제거 비용이 낮습니다.
- **EOL** — 컴포넌트의 릴리즈 사이클이 공표된 지원 종료를 지났으면 **EOL** 배지, 아니면 빈칸. [지원 종료 표시](#end-of-life-flagging) 참고.
- **최신성 (Currency)** — 컴포넌트의 (아직 지원되는) 릴리즈 라인에 더 새로운 패치가 공개돼 있으면 **뒤처짐** 배지, 아니면 빈칸. [버전 최신성](#version-currency) 참고.
- **심각도 (Severity)** — 본 컴포넌트의 미해결 CVE 중 가장 높은 심각도(범례를 통해 라이선스 분류 색상도 함께 표시).
- **CVEs** — 본 컴포넌트의 미해결 취약점 수(클릭 시 사전 필터링된 Vulnerabilities 탭으로 이동).

테이블은 가상화되어 수천 개의 컴포넌트도 부드럽게 스크롤됩니다.

### 필터

상단 인라인 필터 바:

- **검색 (Search)** — `name@version` 부분 일치.
- **의존성 타입 (Dependency type)** — 3-스테이트 세그먼트 컨트롤(`Any` / `Direct only` / `Transitive only`). Direct-only 세그먼트는 API 의 `?direct=true` 로, Transitive-only 는 `?direct=false` 로 매핑됩니다.
- **Usage** — 다중 선택(`Required` / `Optional`). 둘 다 선택하면 필터 없음과 동일합니다. 미지값만 선택한 쿼리는 422 거부 대신 empty page 로 드롭됩니다(심각도·라이선스 카테고리 필터 시멘틱과 일치).
- **심각도 (Severity)** — 다중 선택 배지(Critical / High / Medium / Low / Info).
- **라이선스 카테고리 (License category)** — 다중 선택(`Allowed` / `Conditional` / `Forbidden` / `Unknown`).
- **EOL만 (EOL only)** — 지원 종료된 컴포넌트만 남기는 토글(`?eol=true`).
- **뒤처진 것만 (Outdated only)** — 릴리즈 라인의 최신 패치보다 뒤처진 컴포넌트만 남기는 토글(`?outdated=true`). [버전 최신성](#version-currency) 참고.
- **정렬 (Sort)** + **순서 (order)** — 컬럼 기반 정렬과 오름·내림 토글.

필터는 결합됩니다. URL(`?direct=…`, `?dependency_scope=…`, …)이 갱신되어 필터된 뷰를 공유할 수 있습니다.

### 런타임 스코프 필터링 {#runtime-scope-filtering}

테이블에는 빌드가 해석한 전부가 아니라 **배포되는 런타임 세트**가 표시됩니다.
cdxgen 은 해석된 모든 노드를 기록하므로, 산출물과 함께 배포되지 않는 테스트·개발
도구(`junit`, `lombok`, `jest`, `eslint`, …)까지 SBOM 에 담깁니다. 기본 설정에서
스캐너는 SBOM 을 저장·서명하고 취약점 DB 에 매칭하기 전에 이런 컴포넌트를
제거합니다.

- **Maven** — cdxgen 이 `optional`(Maven `test` scope) 또는 `excluded`
  (`provided`/`system` scope)로 태깅한 컴포넌트를 제거합니다. 이 필터는 SBOM 에
  scope 태그가 실제로 존재할 때만 동작하며, 태그가 없는 SBOM 은 그대로 두므로
  탐지 범위가 줄어드는 일은 없습니다.
- **npm** — 프로젝트의 `package-lock.json` 이 `dev` 로 분류한 패키지를
  제거합니다. lockfile 이 다루지 않는 패키지는 항상 유지합니다(모노레포의 중첩
  manifest 는 루트 lockfile 에 없습니다). 즉 dev 의존성이라는 명확한 근거가 있는
  컴포넌트만 제거합니다.

제외된 컴포넌트 수는 스캔에 기록되고, SBOM 의 `metadata.properties` 에는
생태계별 개수를 담은 `trusca:scope_filter` 항목이 남아 필터된 문서가 제거 내역을
스스로 설명합니다. 인제스트 API 로 업로드한 SBOM 은 **절대** 필터링하지
않습니다 — 업로드된 SBOM 은 공급자가 선언한 내용 그대로를 신뢰합니다.

알아 둘 주의점 2가지:

- cdxgen 은 Maven `<optional>true</optional>` 의존성을 `test` scope 와 같은
  방식으로 태깅하므로, 드물게 *런타임* optional 의존성도 함께 제거됩니다. 그런
  프로젝트에서는 `SCAN_SCOPE_FILTER_MAVEN_ENABLED=false` 로 끄십시오.
- 필터를 끄면(`SCAN_SCOPE_FILTER_ENABLED=false`) 다음 스캔부터 전체 해석
  그래프가 복원됩니다. 토글 3종은 [환경변수](../reference/env-variables.md)를
  참고하십시오.

### 지원 종료(EOL) 표시 {#end-of-life-flagging}

공표된 **지원 종료(EOL, end-of-life)** 를 지난 런타임·프레임워크에는
업스트림 수정이 더 이상 제공되지 않습니다 — 내일 발견되는 Critical CVE 에
패치가 없다는 뜻입니다. 오늘의 CVE 수와는 별개의 공급망 리스크라서 스캐너가
따로 표시합니다. 스캔된 모든 컴포넌트를 [endoflife.date](https://endoflife.date)
추적 대상 제품의 선별 목록(Spring Boot, Spring Framework, Express, Next.js,
Angular, Vue, Django, Rails, Symfony, Laravel)과 대조하고, 버전에서 릴리즈
사이클을 유도한 뒤(`3.2.0` → 사이클 `3.2`), 해당 사이클의 지원 종료일을
**릴리즈에 번들된 스냅숏**에서 읽습니다 — 스캔 시 네트워크 호출이 없어
air-gapped 환경에서도 동작합니다.

화면 읽는 방법:

- **EOL 컬럼/배지** — 사이클이 지원 종료를 *지난* 경우에만 주황색 **EOL**
  배지가 나타납니다. 공표된 종료일은 배지 툴팁에 표시됩니다.
- **빈칸과 unknown 의 구분** — 빈칸은 **추적 대상 제품이 아니라는** 뜻입니다
  (목록은 의도적으로 닫혀 있습니다: 롱테일 라이브러리는 없고, 스캐너는 절대
  추정하지 않습니다). 드로어의 지원 종료 행은 한 단계 더 구분합니다: `—` 에
  마우스를 올리면 "추적 대상 아님" 또는 "추적 대상이나 릴리즈 사이클을
  판정할 수 없음"이 표시됩니다.
- **Overview 칩** — 기준 스캔에 EOL 컴포넌트가 있으면 Overview 탭에 개수와
  함께 필터된 목록으로 바로 이동하는 링크가 나타납니다.
- 판정은 공유 컴포넌트 카탈로그에 저장되고 새 스냅숏이 도착하면(릴리즈
  업그레이드) 재평가되므로, 오래된 프로젝트도 재스캔 없이 새로 공표된
  지원 종료일을 반영합니다.

`EOL_ENABLED=false` 로 끄거나, air-gapped 설치에서는 `EOL_SNAPSHOT_PATH` 로
더 신선한 스냅숏 파일을 지정할 수 있습니다 —
[환경변수](../reference/env-variables.md) 참고.

### 버전 최신성 — 최신 패치보다 뒤처짐 {#version-currency}

[EOL 플래그](#end-of-life-flagging)의 자매 신호이지만 답하는 질문이
다릅니다. EOL 은 *"이 릴리즈 라인이 수명을 다했는가?"* 를 묻고, 버전
최신성은 *"이 버전이 — 아직 지원되는 — 자기 릴리즈 라인의 최신 패치보다
뒤처져 있는가?"* 를 묻습니다. `3.2` 라인이 멀쩡히 살아 있어도 `3.2.7` 보다
패치 세 개 뒤처진 채, 이미 나온 수정들을 조용히 놓치고 있을 수 있습니다.

이 신호는 EOL 플래그와 같은 번들 endoflife.date 스냅숏에서 유도됩니다
(추적되는 릴리즈 사이클마다 최신 패치 버전이 실려 있음). 그래서 똑같이
오프라인입니다 — 스캔 시 네트워크 호출이 없어 air-gapped 환경에서도
동작하고, 같은 닫힌 추적 대상 목록이 적용됩니다: 목록 밖의 컴포넌트는
최신성 데이터가 없다고만 표시될 뿐, 절대 추정하지 않습니다.

![Components 탭 — 뒤처진 것만 필터를 켠 상태와 최신성 컬럼 배지](/img/screenshots/user-components-outdated.png)

화면 읽는 방법:

- **최신성 컬럼/배지** — **뒤처짐** 배지. 의도적으로 EOL 보다 한 단계 낮은
  톤입니다: 뒤처짐은 위생 신호이지 리스크 판정이 아닙니다. 툴팁에 최신
  패치 버전이(스냅숏에 기록돼 있으면 공개일도 함께) 표시됩니다.
- **드로어 행** — 드로어의 **버전 최신성** 행은 네 가지 상태를 구분합니다:
  릴리즈 라인의 최신 패치 사용 중, 최신 패치보다 뒤처짐, 추적 대상이나
  최신 패치를 판정할 수 없음, 릴리즈 라인 정보 없음.
- **Overview 칩** — 기준 스캔에 뒤처진 컴포넌트가 있으면 Overview 탭에
  "최신 패치보다 뒤처진 컴포넌트 N개" 칩과 함께 사전 필터된 목록으로 바로
  이동하는 링크가 나타납니다.
- **필터** — **뒤처진 것만** 토글이 테이블을 뒤처진 행으로
  좁힙니다(`?outdated=true`).

알아 둘 경계 두 가지:

- 이 신호는 **릴리즈 라인** 기준 최신성입니다 — "*자기* 라인의 최신 패치보다
  뒤처짐"이지, "모든 라인을 통틀어 최신 버전보다 뒤처짐"이 아닙니다. 더
  새로운 major·minor 라인으로 옮기는 것은 호환성 파괴 가능성이 있는
  업그레이드 결정이고, 이 배지는 리스크가 낮은 패치 인상만 가리킵니다.
- 뒤처짐은 **취약점 판정이 아닙니다** — CVE 컬럼이 따로 말해 줍니다. 결과가
  0건인 뒤처진 컴포넌트는 싸게 최신화할 수 있다는 뜻일 뿐이고, 결과가
  *있는* 뒤처진 컴포넌트는 바로 그 패치 인상 안에 수정이 들어 있는 경우가
  많습니다([업그레이드 추천](./vulnerabilities.md#업그레이드-추천권장-버전)
  참고).

<!-- docs-uat: id=components-outdated-filter-api kind=api auth=admin url=/v1/projects/${PROJECT_ID}/components?outdated=true expect=status:200 tier=nightly -->
API 에서는 `GET /v1/projects/{id}/components?outdated=true` 가 뒤처진 행만
반환하며, 모든 컴포넌트에 `currency_state`(`current` / `outdated` /
`unknown`, 추적 대상이 아니면 `null`), `currency_latest`,
`currency_latest_release_date` 가 실립니다.

이 신호는 EOL 파이프라인에 함께 실립니다: `EOL_ENABLED=false` 는 두 플래그를
모두 끄고, air-gapped 설치에서는 `EOL_SNAPSHOT_PATH` 로 더 신선한 스냅숏을
지정합니다 — [환경변수](../reference/env-variables.md) 참고.

## 표 보기와 그래프 보기

Components 탭에는 **표 / 그래프** 토글이 있습니다(좌측 상단). 기본값은 위의 가상 스크롤 목록(**표**)입니다. **그래프**는 스캔이 해석한 **의존성 그래프**(스캐너가 기록한 모든 부모 → 자식 엣지)를 상호작용형 노드-링크 다이어그램(좌→우 배치)으로 렌더링합니다. 어떤 패키지가 단지 존재하는지가 아니라 어떻게 끌려 들어왔는지를 볼 수 있습니다. 각 노드는 가장 높은 심각도 결과에 따라 색으로 표시되며(색은 유일한 신호가 아닙니다 — 상세 패널과 트리 폴백에 심각도 라벨도 함께 표시), 검색 상자로 일치하는 패키지를 강조할 수 있습니다. 노드를 클릭하면 캔버스 옆에 상세가 열립니다.

선택은 `?view=graph`로 URL에 반영되어 리로드나 공유 링크에서 그래프가 유지됩니다. 그래프는 다른 탭과 마찬가지로 현재 핀된 `?scan=` 스냅샷을 대상으로 합니다.

대규모에서도 쓸 수 있도록 두 가지 폴백이 있습니다:

- 그래프가 서버 노드 상한(`DEPENDENCY_GRAPH_MAX_NODES`, 기본 5000)을 초과하는 스캔은 구체화하지 않고, 표 보기로 안내하는 배너를 표시합니다.
- 기록된 엣지가 없는 그래프(평면 컴포넌트 목록)나 클라이언트 렌더 상한을 초과하는 경우 접이식 **의존성 트리**로 폴백합니다.

## 드로어 — 컴포넌트 상세

행을 클릭하면 우측 슬라이드 드로어가 열립니다.

- **식별자 (Identity)** — `purl`(Package URL), 상위 홈페이지, 레포 URL. `purl` 아래 두 줄에 컴포넌트의 **타입 (Type)**(Direct / Transitive / `—`) 과 **Usage**(Required / Optional / `—`) 배지가 표시되며, 행에 나오는 값과 동일합니다.
- **모든 라이선스 결과** — 각 결과에 **출처 배지**(**Declared** / **Detected** / **Concluded**)가 표시됩니다. **Detected** 결과에는 scancode 가 라이선스를 발견한 first-party 파일의 `source_path` 도 함께 표시됩니다. [declared vs. detected](#declared-vs-detected) 참고.
- **의무사항** — 컴포넌트의 라이선스가 발생시킨 의무([의무사항](#의무사항) 참고).
- **CVE** — 미해결·해결된 결과, Vulnerability 상세로 딥링크.

드로어를 닫아도 테이블 위치를 유지 — 페이지 이동 없음.

조건부 라이선스 컴포넌트의 승인 상태는 프로젝트 레벨 [승인](./approvals.md) 페이지로 이동해 확인하세요(현재 릴리스에서는 드로어가 승인 상태를 노출하지 않음). concluded 라이선스의 수동 오버라이드 또한 이연되었습니다 — [로드맵](#로드맵) 참고.

## 직접 vs. 전이 (의존성 깊이) {#dependency-depth}

파이프라인은 `cdxgen` 이 기록하는 **의존성 그래프**(어느 패키지가 무엇에 의존하는지)를 수집하고, 각 컴포넌트의 **깊이(depth)** — 그래프 루트로부터의 최단 거리 — 를 산출합니다.

| 깊이 | 의미 | 라벨 |
|---|---|---|
| `1` | **직접(direct)** 의존성 — 프로젝트가 매니페스트/락파일에 직접 선언한 것. | **Direct** |
| `2` 이상 | **전이(transitive)** 의존성 — 직접 의존성(또는 그 의존성)이 요구해서 끌려온 것. | **Transitive** |
| *(빈 값)* | 이 스캔에서 깊이가 산출되지 않음 — 그래프 정보 없이 평평한 컴포넌트 목록만 만든 이전 스캔 또는 생태계. | — |

드로어는 컴포넌트의 깊이와 **Direct** / **Transitive** 라벨을 표시하며, 컴포넌트 목록도 동일한 값을 노출하여 어떤 결과가 직접 소유한 것인지 한눈에 파악할 수 있습니다.

:::note 깊이가 중요한 이유
**직접** 의존성의 취약점은 대개 본인이 고칠 몫입니다 — 선언한 버전을 올리면 됩니다. **전이** 의존성의 취약점은 그것을 끌어온 직접 의존성의 책임이며, 보통 "취약 버전을 더 이상 요구하지 않을 때까지 직접 부모를 업그레이드"하는 것이 해법입니다. 따라서 깊이는 리메디에이션 우선순위를 좌우합니다 — 얕고 직접 의존하는 컴포넌트가 가장 저렴하게 고칠 수 있습니다. 업그레이드 추천 기능이 이 신호 위에 만들어집니다.
:::

:::info 가장 얕은 경로가 우선
한 컴포넌트가 여러 경로로 동시에 도달될 수 있습니다("다이아몬드" — 두 의존성이 같은 패키지를 함께 끌어옴). 포털은 **가장 얕은** 경로를 보고합니다: `lodash` 가 직접 의존성이면서 동시에 전이 의존성이기도 하면 **Direct**(깊이 `1`)로 표시됩니다. 의존성 그래프 자체(모든 부모 → 자식 엣지)는 스캔별로 저장되어 향후 도구가 전체 경로를 보여줄 수 있습니다.
:::

## 라이선스 분류

**Compliance** 탭은 같은 데이터를 SPDX 식별자와 tier 별로 — Components 탭이 사용하는 같은 표 위에 가로 막대 차트 — 분리해서 보여줍니다 (`Has obligations` 토글을 켜면 동일 화면이 의무사항 뷰로 전환됩니다; [의무사항](#의무사항) 참고).

![프로젝트 상세 — tier 분포 막대와 라이선스별 행이 있는 Compliance 탭](/img/screenshots/user-licenses-donut.png)

모든 라이선스는 네 단계 중 하나로 분류됩니다. **코드 값** 컬럼은 API
응답·감사 로그·빌드 게이트에서 사용되는 값이고, **UI 라벨** 컬럼은
테이블·배지에 노출되는 라벨입니다.

| 단계 (코드 값) | UI 라벨 | 빌드 게이트 효과 | 예시 |
|---|---|---|---|
| `permissive` | **Allowed** | 빌드 게이트 영향 없음. | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, Unlicense |
| `conditional` | **Conditional** | [승인 워크플로우](./approvals.md) 트리거. 빌드 진행 — **반려(Rejected)** 결정 이후에도 동일. [승인 페이지의 caveat](./approvals.md#rejected-verdict) 참고. | LGPL-2.x, LGPL-3.x, MPL-2.0, EPL-1.x, EPL-2.0, CDDL-1.0 |
| `forbidden` | **Forbidden** | CI에서 빌드 게이트가 종료 코드 1 반환. | AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0, BUSL-1.1 |
| `unknown` | **Unknown** | 검토 대상으로 노출; 자동 차단 없음. 항상 사람의 검토 필요. | 라이선스 파싱 실패 또는 분류기 매핑에 없는 SPDX ID — [아래](#why-so-many-unknown) 참고. |

:::warning 분류 출처
법적 단계 분류(`forbidden` / `conditional` / `permissive` / `unknown`)는 내장 SPDX → 단계 카탈로그로 결정됩니다. 조직별 룰 커스터마이징이 나오기 전까지는([로드맵](#로드맵) 참고) super-admin 이 카탈로그 항목을 패치하고 워커를 재시작하는 operator 전용 경로를 사용하세요.
:::

### `unknown` 이 왜 이렇게 많은가? {#why-so-many-unknown}

:::info
분류는 정확 일치(exact-match) SPDX ID 를 사용합니다. 접미사 없는 변형(`LGPL-3.0-or-later` 대신 `LGPL-3.0`)은 `unknown` 으로 떨어집니다. 잘 알려진 SPDX ID 인데도 `unknown` 으로 표시된다면 출처가 deprecated alias 를 발신했을 가능성이 높습니다 — 계획된 fuzzy 정규화는 [로드맵](#로드맵) 참고.
:::

또 다른 흔한 원인은 의존성 이름만 있고 라이선스가 없는 매니페스트입니다 —
설치된 패키지가 없는 `requirements.txt` 나 `go.mod` 는 패키지를 나열하지만
라이선스 메타데이터를 담지 않아 분류할 대상이 없습니다. 이 공백을 메우려고
파이프라인은 컴포넌트의 공개 레지스트리(PyPI, Maven Central, crates.io,
pkg.go.dev, RubyGems, NuGet)에 패키지 좌표로 라이선스를 조회해 *concluded* finding 으로
기록합니다. 기본으로 켜져 있으며, **air-gapped** 배포는
`LICENSE_FETCH_ENABLED=false`([환경변수](../reference/env-variables.md) 참고)로
끄고, 그 경우 레지스트리에 접근할 수 없어 이런 컴포넌트는 `unknown` 으로
남습니다.

## AI 라이선스 검토 플래그 {#ai-license-review-flags}

일부 라이선스는 컴포넌트를 *어떻게* 사용할지를 위 4단계 법적 분류가 담지 못하는 방식으로 제한합니다 — 표준 오픈소스 컴플라이언스 도구가 자주 놓치는 지점입니다. TRUSCA는 이런 제약 부류 2종을 amber **Review needed** 플래그로 표시하며, 라이선스 단계 배지 옆에 함께 나타나고 Compliance 탭에서 필터링됩니다.

이 플래그는 의도적으로 좁습니다. 단계 모델(`permissive` / `conditional` / `forbidden` / `unknown`)이 이미 표현하는 제약은 제외하고, AI 관련 제약만 표시합니다. 일반 오픈소스 라이선스 — MIT, Apache-2.0, BSD 계열, GPL / LGPL — 는 이 플래그가 붙지 않습니다.

| 플래그 (코드 값) | UI 라벨 | 무엇을 표시하나 | 예시 라이선스 |
|---|---|---|---|
| `behavioral_use` | **행동 사용 제한** | 라이선스가 재배포를 제한하는 대신 소프트웨어·모델의 특정 *용도* — 예: 군사·감시·차별적 응용 — 를 금지합니다. | RAIL(Responsible AI License, 책임 있는 AI 라이선스)과 OpenRAIL 변형; Llama·Gemma·Falcon 커뮤니티 모델 라이선스. |
| `non_commercial` | **비상업 전용** | 라이선스가 연구·개인 용도는 허용하되 상업적 사용은 금지합니다. | CC-BY-NC(Creative Commons Attribution-NonCommercial, 크리에이티브 커먼즈 저작자표시-비영리)와 그 ShareAlike / NoDerivatives 변형; 기타 비상업 source-available 조항. |

:::note 플래그는 적용 여부가 아니라 존재를 표시
**Review needed** 플래그는 해당 부류의 제약이 라이선스에 존재한다는 사실을 알릴 뿐, 특정 사용이 그 제약을 위반한다는 뜻은 아닙니다. 행동 사용 조항이나 비상업 조항이 프로젝트의 컴포넌트 사용 방식에 실제로 적용되는지는 스캐너가 아니라 사람이 내리는 법적·사업적 판단입니다. TRUSCA는 BomLens `license-flags.jq` 규칙과 OpenChain AI SBOM 가이드의 원칙을 따릅니다 — 도구는 제약의 *부류*를 표시하고, 적용 여부는 사람이 판단합니다.
:::

행동 사용·비상업 제약은 일반 코드보다 AI 모델·데이터셋에 훨씬 자주 따라붙으며, SPDX 법적 단계의 재배포 중심 논리 바깥에 놓입니다 — 그래서 겉으로 permissive 해 보이는 단계만으로는 이 제약이 가려질 수 있습니다. 플래그는 제약을 판정하지 않으면서 눈에 보이게 만듭니다.

### 플래그 읽기

- **Compliance** 탭에서 플래그가 붙은 라이선스 행은 단계 배지 옆에 amber **Review needed** 배지를 표시합니다. 라이선스 카테고리 다중 선택과 함께 **Review needed** 필터로 그 행만 좁힐 수 있습니다.
- 플래그는 권고입니다. 빌드를 차단하지 않고 법적 단계를 바꾸지 않습니다. 컴포넌트가 `permissive` 단계이면서 동시에 **Review needed** 플래그를 달 수 있습니다.
- 플래그가 붙은 라이선스는 생성된 NOTICE 문서의 별도 "License review needed" 섹션에도 나타납니다 — [SBOM → NOTICE 파일](./sbom.md#notice-파일) 참고.

## Declared vs. detected {#declared-vs-detected}

각 라이선스 결과에는 라이선스가 어디서 왔는지 알려주는 **kind** 가 있습니다. kind 는 컴포넌트 테이블·Licenses 탭·컴포넌트 드로어에 출처 배지로 표시되며, Licenses 탭에서 kind 별로 필터링할 수 있습니다.

| Kind | 출처 | 의미 |
|---|---|---|
| **Declared** | `cdxgen` — 의존성의 공개 패키지 메타데이터(`package.json`, `pom.xml`, `setup.py` 등)에서 읽음. | 의존성 작성자가 *명시한* 라이선스. 빌드 게이트가 평가하는 값입니다. 대부분의 의존성 결과는 declared 입니다. |
| **Detected** | scancode — 프로젝트의 **first-party** 소스 파일을 직접 스캔. 각 detected 결과에 `source_path`(라이선스 텍스트가 발견된 파일)를 포함. | **내 코드**에 실제로 존재하는 라이선스. 메타데이터가 놓치는 경우를 잡아냅니다 — 예: declared 는 `MIT` 인데 `GPL-3.0` 라이선스 코드가 트리에 복사되어 들어온 경우. |
| **Concluded** | 다중 생태계 레지스트리 fetcher(Maven Central / PyPI / crates.io / pkg.go.dev / RubyGems / NuGet). `cdxgen` 이 의존성의 SPDX id 를 전혀 만들지 못했을 때**만** 폴백으로 사용. | 메타데이터가 침묵한 의존성에 대해 레지스트리에서 도출한 라이선스. declared 와 detected 를 화해한 결과가 *아닙니다* — v0.10.0 은 자동 화해(reconciliation)를 수행하지 않습니다. |

:::note "Detected" 는 의존성 소스가 아니라 first-party
scancode 는 **내** 소스 트리에서만 실행됩니다. 서드파티 의존성 소스는 의도적으로 다운로드하지 **않습니다** — 이는 스캔당 실행 시간을 예산 내로 유지하기 위함입니다. 따라서 의존성의 라이선스는 **declared**(또는 레지스트리 폴백을 통한 **concluded**)이며 결코 **detected** 가 아닙니다. **detected** 라이선스는 항상 내 저장소의 코드를 설명합니다.
:::

:::caution Declared 와 detected 는 불일치할 수 있음
한 컴포넌트가 **declared** 결과(예: 메타데이터의 `MIT`)와 **detected** 결과(예: 소스 파일의 `GPL-3.0`)를 모두 가질 수 있습니다. 현재 릴리스에서는 둘을 나란히 노출하며 하나의 결론으로 **자동 화해하지 않습니다** — 충돌은 직접 검토하세요. `MIT` 로 배포하는 프로젝트 안에서 탐지된 `GPL-3.0` 은 detected 스캔이 존재하는 바로 그 오염 사례입니다.
:::

### detected 라이선스가 누락되는 경우

scancode 는 **best-effort** 입니다. 다음의 경우 detected 라이선스가 누락될 수 있으며 — 이는 정상이고 비치명적이며, 스캔은 declared 라이선스로 여전히 성공합니다:

- 워커 이미지에 scancode 가 미설치.
- first-party 트리가 `SCANCODE_MAX_FILES` 한도를 초과했거나, scancode 가 타임아웃되었거나, 결과가 너무 큼.
- 해당 코드가 **제외** 디렉토리 안에 위치. 자원 예산 내에 머무르기 위해 scancode 는 `node_modules`, `vendor`, `bower_components`, `.venv`, `venv`, `virtualenv`, `site-packages`, `dist`, `build`, `target`, `out`, `.next`, `.nuxt`, `__pycache__`, `.gradle`, `.git`, `.hg`, `.svn`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.idea`, `.vscode` 이름의 디렉토리를 — 어떤 깊이에서든 — 건너뜁니다. 이 이름 아래에 커밋된 코드는 detected 라이선스를 만들지 않습니다.

## Vendored-OSS 식별 (SCANOSS) {#vendored-oss}

일부 프로젝트 — 특히 C / C++ 및 임베디드 트리 — 는 패키지 매니페스트 없이 오픈소스를 **소스 트리에 직접 복사**해 넣습니다(`src/` 아래에 커밋된 `liblzma`·`openssl`·`zlib` 폴더 등). 거기서는 `cdxgen` 이 이름 없는 `pkg:generic` 파일만 보고 실제 OSS 는 기록되지 않습니다. **SCANOSS** 가 이 격차를 메웁니다: 소스 파일을 핑거프린트하고 알려진 OSS 릴리스 지식 베이스에 매칭해, 복사된 파일을 정식 컴포넌트(이름 + 버전 + purl)와 그 detected 라이선스로 기록합니다.

TRUSCA 는 **full-file 매치만** 컴포넌트로 취급합니다 — snippet 매치(다른 곳에서 몇 줄 복사)는 노이즈라 건너뛰므로, 빌드 게이트·NOTICE 에 들어가는 컴포넌트가 깨끗하게 유지됩니다. 매칭된 컴포넌트는 Components 탭에 SCANOSS 출처로 표시되고, 그 라이선스는 scancode 와 똑같이 **Detected** 결과가 됩니다.

:::warning 기본 비활성 — 핑거프린트를 외부 서비스로 전송
SCANOSS 는 **운영자가 `SCANOSS_ENABLED=true` 로 설정하지 않는 한 비활성**입니다. 활성화하면 파일 **핑거프린트**(해시이며, 소스 코드 자체는 아님)를 `SCANOSS_API_URL`(기본 무료 `api.osskb.org`)로 전송합니다. 자체 호스팅 포털이 코드에 관한 데이터를 조용히 외부로 내보내서는 안 되므로 opt-in 입니다: 그 외부 매칭이 허용되는 경우에만 켜거나, `SCANOSS_API_URL` 을 **자체 호스팅 SCANOSS** 인스턴스로 향하게 해 모든 것을 사내에 두세요. [환경 변수 → 스캔 파이프라인](../reference/env-variables.md#scan-pipeline) 참고.
:::

scancode 처럼 이 단계도 **best-effort** 입니다: SCANOSS 가 꺼져 있거나, 도구가 없거나, 엔드포인트에 도달할 수 없거나, 매치가 없으면 스캔은 vendored-OSS 결과 없이 그대로 계속됩니다 — 이 단계 때문에 스캔이 실패하지 않습니다.

## 의무사항

각 라이선스는 **의무사항**을 가집니다 — 컴포넌트를 재배포할 때 이행해야 할 의무. 포털은 7가지 종류를 추적합니다([용어집](https://github.com/trustedoss/trusca/blob/main/docs/glossary.md) 참고).

- **저작자 표시** — 상위 저작권 고지를 보존.
- **NOTICE 보존** — 상위 `NOTICE` 파일 동봉(Apache-2.0 §4(d)).
- **소스 공개** — 요청 시 해당 소스를 제공.
- **카피레프트** — 파생물을 동일 라이선스로 공개.
- **변경 표시** — 변경된 파일에 두드러진 변경 표시.
- **동적 링킹** — LGPL류: 최종 사용자가 수정 라이브러리로 재링크 가능해야 함.
- **보증 금지** — 허락 없이 프로젝트 이름으로 파생물을 보증할 수 없음.

**Compliance** 탭의 **Has obligations** 토글을 켜면 컴포넌트 전반의 의무사항이 통합 표시됩니다. 툴바에서 NOTICE 포맷(**text** 또는 **HTML**)을 선택한 뒤 **Download NOTICE**를 클릭하면 모든 저작자 표시·라이선스를 요약한 NOTICE 문서를 저장합니다. 엔드포인트는 API를 통해 `markdown` 변형도 제공합니다. NOTICE 문서는 컴포넌트별 저작권 라인(SBOM에 저작권자가 없으면 레지스트리 URL로 대체)을 담고, 프로젝트에 등장한 모든 라이선스의 전문을 실은 **License Texts** 섹션으로 끝나므로 `license_text_inclusion_required` 의무를 NOTICE 자체로 충족합니다. 포맷 / MIME / 확장자 표와 섹션별 구성은 [SBOM → NOTICE 파일](./sbom.md#notice-파일) 참고.

![프로젝트 상세 — Has obligations 토글이 켜진 Compliance 탭. 컴포넌트별 의무사항 분포 표시](/img/screenshots/user-obligations-distribution.png)

:::note v0.10.0 의 의무사항 종류
의무사항 카탈로그는 위 일곱 가지를 다룹니다. AGPL / SSPL / BUSL 고유
의무 중 일부는 아직 별도 종류로 모델링되지 **않았습니다**.

- **네트워크 사용 공개**(AGPL §13, SSPL §13) — 최종 사용자가 수정된
  소프트웨어와 네트워크를 통해 상호작용할 때 요구됩니다.
- **특허 부여 종료**(Apache-2.0 §3, MPL-2.0 §5.2).
- **상표권 제한**(Apache-2.0 §6, BSD-4-clause).
- **사용 분야 제한**(BUSL-1.1).

이 항목은 컴포넌트 드로어에서 라이선스 원문을 통해
확인하세요([로드맵](#로드맵) 참고).
:::

## 한국어 라이선스 콘텐츠 {#korean-license-content}

인터페이스 언어가 한국어이면, 라이선스 **요약**(라이선스 드로어에 표시되는
"이 라이선스가 무엇을 요구하는가"를 평이하게 설명한 문장)과 **의무사항
본문**이 분류 카탈로그에 있는 모든 라이선스에 대해 한국어로 표시됩니다.
카탈로그 밖의 라이선스(`LicenseRef-*`, 카탈로그가 완전히 다루지 않는 복합
표현)는 영어로 대체 표시됩니다.

:::note 정본은 영어입니다
한국어 표기는 읽기를 돕는 참고용이며 법적 효력을 갖는 문서가 아닙니다.
번역된 요약과 의무사항마다 **영어 원문 보기** 항목을 두어 정본 문구를 확인할
수 있고, 정본 라이선스 전문(NOTICE 파일의 *License Texts* 섹션에 번들된 SPDX
전문)은 번역하지 않습니다. 법률 검토에는 영어 원문을 근거로 삼으십시오.
:::

## SPDX 표현

라이선스는 [SPDX 식별자](https://spdx.org/licenses/)로 식별됩니다. 복합 라이선스는 SPDX 표현 문법을 사용합니다.

- `(MIT OR Apache-2.0)` — 듀얼 라이선스; 둘 중 하나 허용.
- `(GPL-2.0+ WITH Classpath-exception-2.0)` — 예외가 있는 GPL.
- `LicenseRef-proprietary` — 비SPDX 라이선스, 파싱은 되나 분류되지 않음.

UI에서 표현 위에 마우스를 올리면 각 컴포넌트 라이선스의 SPDX URL이 표시됩니다.

## 정상 동작 확인

스캔 성공 후:

<!-- docs-uat: id=components-count-nonzero kind=ui harness=componentsHaveData(portal-web) tier=nightly -->
1. 컴포넌트 수가 예상과 일치(락파일의 고정된 의존성 수에 가까움).
<!-- docs-uat: id=components-classification-sums kind=manual tier=manual -->
2. Overview 탭의 분류 분포 가로 막대 차트가 100%로 합산됩니다.
<!-- docs-uat: id=licenses-forbidden-highlighted kind=ui harness=licensesGridPopulated(portal-web) tier=nightly -->
3. 금지 라이선스 컴포넌트가 있으면 빨간색 강조와 함께 [승인 큐](./approvals.md)로 가는 CTA가 보입니다.

## 트러블슈팅

### 많은 컴포넌트가 `Unknown` 라이선스로 표시

라이선스를 파싱할 수 없었거나 분류기의 정확 일치 사전에 SPDX ID 가 없었습니다([`unknown` 이 왜 이렇게 많은가?](#why-so-many-unknown) 참고). 일반적 원인:

- 패키지에 `LICENSE` 파일도, 메타데이터 선언도 없음(잘 관리되는 생태계에서는 드뭄).
- 분류기가 인식 못하는 커스텀 라이선스 문자열. 컴포넌트 드로어에 원본 문자열이 노출되어 법무 검토가 가능합니다.
- 출처가 deprecated SPDX alias 를 발신(예: `LGPL-3.0-or-later` 대신 `LGPL-3.0`); 정확 일치 사전은 아직 이를 정규화하지 않습니다.
- 해당 생태계 메타데이터 fetch 실패. `docker-compose logs worker`에서 `cdxgen`의 생태계별 경고를 확인.

### 분류가 잘못된 것 같음

분류는 내장 SPDX → 단계 카탈로그로 결정됩니다([위의 분류 출처](#라이선스-분류) 참고). 오늘 일회성 오버라이드가 필요하면 super-admin 이 카탈로그 항목을 패치하고 워커를 재시작하세요. 조직별 커스터마이징은 [로드맵](#로드맵)에서 추적합니다. 카탈로그 항목이 맞는데 detected 라이선스가 declared 와 불일치하면, 컴포넌트 드로어에서 두 결과를 모두 검토하세요([declared vs. detected](#declared-vs-detected) 참고).

### 락파일이 탐지되지 않음

`cdxgen`은 30개 이상의 생태계를 지원하지만 새 생태계는 지속 추가됩니다. 프로젝트 락파일이 레포 루트 또는 한 단계 아래에 있는지 확인하세요. `cdxgen`은 임의 깊이로 재귀하지 않습니다. 미지원 생태계라면 파이프라인 출력과 함께 이슈를 등록하세요.

## 로드맵

매뉴얼이 이전에 약속했으나 v0.10.0에 포함되지 않은 항목.

- 컴포넌트 테이블의 별도 **타입 (Type)**(생태계)·**분류 (Classification)** 컬럼 — 현재 릴리스에서는 타입이 드로어 식별자 행의 `purl`에 포함되며, 분류는 **심각도** 색상 범례로 표현됩니다.
- 정확 SPDX 표현 기반 **라이선스** 필터와 **미해결 CVE 보유** 토글 — 예정. 현재는 **라이선스 카테고리** 다중 선택과 검색 박스가 대부분의 워크플로우를 커버합니다.
- 컴포넌트 드로어 내 **승인 상태** 행 — 예정. 현재 정답은 프로젝트 레벨 [승인](./approvals.md) 페이지입니다.
- 드로어의 수동 **Concluded 라이선스 오버라이드** 동작(`team_admin`) — 예정.
- 접미사 없는 변형(`LGPL-3.0` → `LGPL-3.0-or-later`)을 위한 fuzzy SPDX 정규화 — 예정.
- 조직별 라이선스 분류 룰 커스터마이징 — 예정. 오늘은 내장 카탈로그가 분류를 결정합니다.
- 더 풍부한 의무사항 분류 체계(네트워크 사용 고지, 특허 부여 종료, 상표권 제한, 사용 분야 제한) — 예정. 오늘은 드로어의 라이선스 원문으로 확인합니다.

## 함께 보기

- [취약점](./vulnerabilities.md)
- [승인](./approvals.md)
- [SBOM](./sbom.md) — 특히 [v0.10.0 의 컴플라이언스 증거 체인](./sbom.md#compliance-evidence-trail)
