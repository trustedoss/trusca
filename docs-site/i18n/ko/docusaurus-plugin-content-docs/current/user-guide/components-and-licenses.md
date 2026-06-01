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
- **정렬 (Sort)** + **순서 (order)** — 컬럼 기반 정렬과 오름·내림 토글.

필터는 결합됩니다. URL(`?direct=…`, `?dependency_scope=…`, …)이 갱신되어 필터된 뷰를 공유할 수 있습니다.

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
법적 단계 분류(`forbidden` / `conditional` / `permissive` / `unknown`)는 내장 SPDX → 단계 카탈로그로 결정됩니다. 조직별 룰 커스터마이징은 로드맵 항목입니다. 오늘 일회성 오버라이드가 필요하면 super-admin 이 카탈로그 항목을 패치하고 워커를 재시작하는 operator 전용 경로를 사용하세요.
:::

### `unknown` 이 왜 이렇게 많은가? {#why-so-many-unknown}

:::info
분류는 정확 일치(exact-match) SPDX ID 를 사용합니다. 접미사 없는 변형(`LGPL-3.0-or-later` 대신 `LGPL-3.0`)은 `unknown` 으로 떨어집니다. 잘 알려진 SPDX ID 인데도 `unknown` 으로 표시된다면 출처가 deprecated alias 를 발신했을 가능성이 높습니다. fuzzy SPDX 정규화는 로드맵 항목입니다.
:::

## Declared vs. detected {#declared-vs-detected}

각 라이선스 결과에는 라이선스가 어디서 왔는지 알려주는 **kind** 가 있습니다. kind 는 컴포넌트 테이블·Licenses 탭·컴포넌트 드로어에 출처 배지로 표시되며, Licenses 탭에서 kind 별로 필터링할 수 있습니다.

| Kind | 출처 | 의미 |
|---|---|---|
| **Declared** | `cdxgen` — 의존성의 공개 패키지 메타데이터(`package.json`, `pom.xml`, `setup.py` 등)에서 읽음. | 의존성 작성자가 *명시한* 라이선스. 빌드 게이트가 평가하는 값입니다. 대부분의 의존성 결과는 declared 입니다. |
| **Detected** | scancode — 프로젝트의 **first-party** 소스 파일을 직접 스캔. 각 detected 결과에 `source_path`(라이선스 텍스트가 발견된 파일)를 포함. | **내 코드**에 실제로 존재하는 라이선스. 메타데이터가 놓치는 경우를 잡아냅니다 — 예: declared 는 `MIT` 인데 `GPL-3.0` 라이선스 코드가 트리에 복사되어 들어온 경우. |
| **Concluded** | 다중 생태계 레지스트리 fetcher(Maven Central / PyPI / crates.io / pkg.go.dev). `cdxgen` 이 의존성의 SPDX id 를 전혀 만들지 못했을 때**만** 폴백으로 사용. | 메타데이터가 침묵한 의존성에 대해 레지스트리에서 도출한 라이선스. declared 와 detected 를 화해한 결과가 *아닙니다* — v0.10.0 은 자동 화해(reconciliation)를 수행하지 않습니다. |

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

## 의무사항

각 라이선스는 **의무사항**을 가집니다 — 컴포넌트를 재배포할 때 이행해야 할 의무. 포털은 7가지 종류를 추적합니다([용어집](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/glossary.md) 참고).

- **저작자 표시** — 상위 저작권 고지를 보존.
- **NOTICE 보존** — 상위 `NOTICE` 파일 동봉(Apache-2.0 §4(d)).
- **소스 공개** — 요청 시 해당 소스를 제공.
- **카피레프트** — 파생물을 동일 라이선스로 공개.
- **변경 표시** — 변경된 파일에 두드러진 변경 표시.
- **동적 링킹** — LGPL류: 최종 사용자가 수정 라이브러리로 재링크 가능해야 함.
- **보증 금지** — 허락 없이 프로젝트 이름으로 파생물을 보증할 수 없음.

**Compliance** 탭의 **Has obligations** 토글을 켜면 컴포넌트 전반의 의무사항이 통합 표시됩니다. 툴바에서 NOTICE 포맷(**text** 또는 **HTML**)을 선택한 뒤 **Download NOTICE**를 클릭하면 모든 저작자 표시·라이선스를 요약한 NOTICE 문서를 저장합니다. 엔드포인트는 API를 통해 `markdown` 변형도 제공합니다. 포맷 / MIME / 확장자 표는 [SBOM → NOTICE 파일](./sbom.md#notice-파일) 참고.

![프로젝트 상세 — Has obligations 토글이 켜진 Compliance 탭. 컴포넌트별 의무사항 분포 표시](/img/screenshots/user-obligations-distribution.png)

:::note v0.10.0 의 의무사항 종류
의무사항 카탈로그는 위 일곱 가지를 다룹니다. AGPL / SSPL / BUSL 고유
의무 중 일부는 아직 별도 종류로 모델링되지 **않았습니다**.

- **네트워크 사용 공개**(AGPL §13, SSPL §13) — 최종 사용자가 수정된
  소프트웨어와 네트워크를 통해 상호작용할 때 요구됩니다.
- **특허 부여 종료**(Apache-2.0 §3, MPL-2.0 §5.2).
- **상표권 제한**(Apache-2.0 §6, BSD-4-clause).
- **사용 분야 제한**(BUSL-1.1).

이 항목은 컴포넌트 드로어에서 라이선스 원문을 통해 확인하세요. 더
풍부한 의무사항 분류 체계는 로드맵 항목입니다.
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

분류는 내장 SPDX → 단계 카탈로그로 결정됩니다([위의 분류 출처](#라이선스-분류) 참고). 오늘 일회성 오버라이드가 필요하면 super-admin 이 카탈로그 항목을 패치하고 워커를 재시작하세요; 조직별 커스터마이징 경로는 로드맵 항목입니다. 카탈로그 항목이 맞는데 detected 라이선스가 declared 와 불일치하면, 컴포넌트 드로어에서 두 결과를 모두 검토하세요([declared vs. detected](#declared-vs-detected) 참고).

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

## 함께 보기

- [취약점](./vulnerabilities.md)
- [승인](./approvals.md)
- [SBOM](./sbom.md) — 특히 [v0.10.0 의 컴플라이언스 증거 체인](./sbom.md#compliance-evidence-trail)
