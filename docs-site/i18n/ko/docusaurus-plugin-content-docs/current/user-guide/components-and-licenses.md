---
id: components-and-licenses
title: 컴포넌트와 라이선스
description: 스캔이 발견한 컴포넌트를 탐색하고 declared·concluded 라이선스를 검토하며 허용·조건부·금지 분류에 따라 행동합니다.
sidebar_label: 컴포넌트·라이선스
sidebar_position: 3
---

# 컴포넌트와 라이선스

스캔이 끝나면 프로젝트의 **Components** 탭에 파이프라인이 발견한 모든 패키지와 ORT가 부여한 라이선스가 나열됩니다. 본 문서는 테이블 읽기, 라이선스 분류 모델, 포털이 추적하는 의무사항을 다룹니다.

:::note 대상 독자
의존성 위생 분류를 수행하는 엔지니어; 라이선스를 검토하는 법무·컴플라이언스 리뷰어. 읽기는 팀 멤버십, 변경(억제·수동 concluded 라이선스)은 `developer` 이상 필요.
:::

## 컴포넌트 테이블

![프로젝트 상세 — 가상 스크롤 행, severity 필터, 라이선스 카테고리 배지가 있는 Components 탭](/img/screenshots/user-components-list.png)

컬럼:

- **컴포넌트 (Component)** — 패키지 이름(예: `lodash`, `org.springframework:spring-web`).
- **버전 (Version)** — 매니페스트나 락파일에서 고정된 버전.
- **라이선스 (License)** — declared·detected를 화해해 ORT가 선택한 concluded 라이선스. 빌드 게이트가 사용하는 값.
- **심각도 (Severity)** — 본 컴포넌트의 미해결 CVE 중 가장 높은 심각도(범례를 통해 라이선스 분류 색상도 함께 표시).
- **CVEs** — 본 컴포넌트의 미해결 취약점 수(클릭 시 사전 필터링된 Vulnerabilities 탭으로 이동).

테이블은 가상화되어 수천 개의 컴포넌트도 부드럽게 스크롤됩니다.

### 필터

상단 인라인 필터 바:

- **검색 (Search)** — `name@version` 부분 일치.
- **심각도 (Severity)** — 다중 선택 배지(Critical / High / Medium / Low / Info).
- **라이선스 카테고리 (License category)** — 다중 선택(`Allowed` / `Conditional` / `Forbidden` / `Unknown`).
- **정렬 (Sort)** + **순서 (order)** — 컬럼 기반 정렬과 오름·내림 토글.

필터는 결합됩니다. URL이 갱신되어 필터된 뷰를 공유할 수 있습니다.

## 드로어 — 컴포넌트 상세

행을 클릭하면 우측 슬라이드 드로어가 열립니다.

- **식별자 (Identity)** — `purl`(Package URL), 상위 홈페이지, 레포 URL.
- **모든 라이선스 결과** — declared, detected, concluded와 ORT가 각각을 귀속시킨 소스 파일.
- **의무사항** — concluded 라이선스가 발생시킨 의무([의무사항](#의무사항) 참고).
- **CVE** — 미해결·해결된 결과, Vulnerability 상세로 딥링크.

드로어를 닫아도 테이블 위치를 유지 — 페이지 이동 없음.

조건부 라이선스 컴포넌트의 승인 상태는 프로젝트 레벨 [승인](./approvals.md) 페이지로 이동해 확인하세요(v2.0.0 시점에서 드로어는 승인 상태를 노출하지 않음). concluded 라이선스의 수동 오버라이드 또한 이연되었습니다 — [로드맵](#로드맵-v2x) 참고.

## 라이선스 분류

**Licenses** 탭은 같은 데이터를 SPDX 식별자와 tier 별로 — Components 탭이 사용하는 같은 표 위에 가로 막대 차트 — 분리해서 보여줍니다:

![프로젝트 상세 — tier 가로 막대 차트와 라이선스별 분포가 있는 Licenses 탭](/img/screenshots/user-licenses-donut.png)


ORT는 모든 라이선스를 세 단계로 분류합니다.

| 단계 | 심각도 | 예시 | 효과 |
|---|---|---|---|
| **Allowed** | — | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, Unlicense | 빌드 게이트 영향 없음. |
| **Conditional** | WARNING | LGPL-2.x, LGPL-3.x, MPL-2.0, EPL-1.x, EPL-2.0, CDDL-1.0 | [승인 워크플로우](./approvals.md) 트리거. 빌드 진행 — **반려(Rejected)** 결정 이후에도 동일. [승인 페이지의 caveat](./approvals.md#rejected-verdict-at-v200) 참고. |
| **Forbidden** | ERROR | AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0, BUSL-1.1 | CI에서 빌드 게이트가 종료 코드 1 반환. |

`Unknown`(라이선스 파싱 실패 또는 SPDX ID 가 분류기 매핑에 없음 — [아래](#why-so-many-unknown) 참고)은 노란 배지의 4번째 단계로 표시되며 항상 사람의 검토가 필요합니다.

:::warning v2.0.0 의 분류 출처
법적 단계 분류(`forbidden` / `conditional` / `permissive` / `unknown`)는 현재 `apps/backend/tasks/scan_source.py` 의 하드코딩된 SPDX → 단계 사전(`_LICENSE_CATEGORY_DEFAULTS`)으로 결정됩니다. 레포의 `ort/rules.kts` 파일은 placeholder 이며, v2.0.0 에서는 이를 수정해도 분류가 **변경되지 않습니다**. ORT 기반의 조직별 룰 커스터마이징은 v2.2 로드맵 항목입니다. 오늘 일회성 오버라이드가 필요하면 super-admin 이 사전을 패치하고 워커를 재시작하는 Operator 전용 경로를 사용하세요.
:::

### `unknown` 이 왜 이렇게 많은가? {#why-so-many-unknown}

:::info
분류는 정확 일치(exact-match) SPDX ID 를 사용합니다. 접미사 없는 변형(`LGPL-3.0-or-later` 대신 `LGPL-3.0`)은 `unknown` 으로 떨어집니다. ORT 는 보통 `-or-later` / `-only` 접미사를 붙여 탐지하지만, 잘 알려진 SPDX ID 인데도 `unknown` 으로 표시된다면 탐지기가 deprecated alias 를 발신했을 가능성이 높습니다. fuzzy SPDX 정규화는 v2.1 로드맵 항목입니다.
:::

## Declared vs. detected vs. concluded

ORT는 세 가지 신뢰 수준을 구분합니다.

- **Declared** — 패키지 자체 메타데이터에 명시된 라이선스(`package.json`, `pom.xml`, `setup.py` 등).
- **Detected** — 패키지 소스 파일을 스캔하여 발견된 라이선스.
- **Concluded** — 둘을 화해한 후 ORT가 결정한 최종 라이선스. 충돌(예: declared `MIT`, detected `GPL-3.0`)은 표시되며 결론이 확정되기 전 사람의 검토가 필요합니다.

빌드 게이트가 평가하는 값은 concluded입니다. 드로어는 셋 모두를 보여 추적 가능성을 제공합니다.

## 의무사항

각 라이선스는 **의무사항**을 가집니다 — 컴포넌트를 재배포할 때 이행해야 할 의무. 포털은 7가지 종류를 추적합니다([용어집](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/glossary.md) 참고).

- **저작자 표시** — 상위 저작권 고지를 보존.
- **NOTICE 보존** — 상위 `NOTICE` 파일 동봉(Apache-2.0 §4(d)).
- **소스 공개** — 요청 시 해당 소스를 제공.
- **카피레프트** — 파생물을 동일 라이선스로 공개.
- **변경 표시** — 변경된 파일에 두드러진 변경 표시.
- **동적 링킹** — LGPL류: 최종 사용자가 수정 라이브러리로 재링크 가능해야 함.
- **보증 금지** — 허락 없이 프로젝트 이름으로 파생물을 보증할 수 없음.

프로젝트 페이지의 **Obligations** 탭은 컴포넌트 전반의 의무사항을 통합합니다. **Download NOTICE**를 클릭하여 모든 저작자 표시·라이선스를 요약한 `NOTICE.txt`를 다운로드 — [SBOM](./sbom.md#notice-파일).

![프로젝트 상세 — 컴포넌트별 의무사항 분포가 있는 Obligations 탭](/img/screenshots/user-obligations-distribution.png)

:::note v2.0.0 의 의무사항 종류
의무사항 카탈로그는 위 일곱 가지를 다룹니다. AGPL / SSPL / BUSL 고유
의무 중 일부는 아직 별도 종류로 모델링되지 **않았습니다**.

- **네트워크 사용 공개**(AGPL §13, SSPL §13) — 최종 사용자가 수정된
  소프트웨어와 네트워크를 통해 상호작용할 때 요구됩니다.
- **특허 부여 종료**(Apache-2.0 §3, MPL-2.0 §5.2).
- **상표권 제한**(Apache-2.0 §6, BSD-4-clause).
- **사용 분야 제한**(BUSL-1.1).

이 항목은 컴포넌트 드로어에서 라이선스 원문을 통해 확인하세요. 더
풍부한 의무사항 분류 체계는 v2.2 로드맵 항목입니다.
:::

## SPDX 표현

라이선스는 [SPDX 식별자](https://spdx.org/licenses/)로 식별됩니다. 복합 라이선스는 SPDX 표현 문법을 사용합니다.

- `(MIT OR Apache-2.0)` — 듀얼 라이선스; 둘 중 하나 허용.
- `(GPL-2.0+ WITH Classpath-exception-2.0)` — 예외가 있는 GPL.
- `LicenseRef-proprietary` — 비SPDX 라이선스, 파싱은 되나 분류되지 않음.

UI에서 표현 위에 마우스를 올리면 각 컴포넌트 라이선스의 SPDX URL이 표시됩니다.

## 정상 동작 확인

스캔 성공 후:

1. 컴포넌트 수가 예상과 일치(락파일의 고정된 의존성 수에 가까움).
2. Overview 탭의 분류 분포 가로 막대 차트가 100%로 합산됩니다.
3. 금지 라이선스 컴포넌트가 있으면 빨간색 강조와 함께 [승인 큐](./approvals.md)로 가는 CTA가 보입니다.

## 트러블슈팅

### 많은 컴포넌트가 `Unknown` 라이선스로 표시

ORT 가 메타데이터를 파싱할 수 없었거나 분류기의 정확 일치 사전에 SPDX ID 가 없었습니다([`unknown` 이 왜 이렇게 많은가?](#why-so-many-unknown) 참고). 일반적 원인:

- 패키지에 `LICENSE` 파일도, 메타데이터 선언도 없음(잘 관리되는 생태계에서는 드뭄).
- ORT 가 인식 못하는 커스텀 라이선스 문자열. 컴포넌트 드로어에 원본 문자열이 노출되어 법무 검토가 가능합니다.
- 탐지기가 deprecated SPDX alias 를 발신(예: `LGPL-3.0-or-later` 대신 `LGPL-3.0`); 정확 일치 사전은 아직 이를 정규화하지 않습니다.
- 해당 생태계 소스 fetch 실패. `docker-compose logs worker`에서 ORT의 생태계별 경고를 확인.

### 분류가 잘못된 것 같음

v2.0.0 의 분류는 `apps/backend/tasks/scan_source.py` 의 하드코딩된 `_LICENSE_CATEGORY_DEFAULTS` 사전으로 결정됩니다([위의 분류 출처](#라이선스-분류) 참고). 레포의 `ort/rules.kts` placeholder 는 효과가 없습니다. 오늘 일회성 오버라이드가 필요하면 super-admin 이 사전을 패치하고 워커를 재시작하세요; ORT 기반의 조직별 커스터마이징 경로는 v2.2 로드맵 항목입니다. 사전 항목이 맞는데 concluded 라이선스가 잘못이면 컴포넌트 드로어에서 결론을 오버라이드하세요.

### 락파일이 탐지되지 않음

`cdxgen`은 30개 이상의 생태계를 지원하지만 새 생태계는 지속 추가됩니다. 프로젝트 락파일이 레포 루트 또는 한 단계 아래에 있는지 확인하세요. `cdxgen`은 임의 깊이로 재귀하지 않습니다. 미지원 생태계라면 파이프라인 출력과 함께 이슈를 등록하세요.

## 로드맵 (v2.x)

매뉴얼이 이전에 약속했으나 v2.0.0에 포함되지 않은 항목.

- 컴포넌트 테이블의 별도 **타입 (Type)**(생태계)·**분류 (Classification)** 컬럼 — v2.0.0에서는 타입이 드로어 식별자 행의 `purl`에 포함되며, 분류는 **심각도** 색상 범례로 표현됩니다.
- 정확 SPDX 표현 기반 **라이선스** 필터와 **미해결 CVE 보유** 토글 — v2.1 예정. 현재는 **라이선스 카테고리** 다중 선택과 검색 박스가 대부분의 워크플로우를 커버합니다.
- 컴포넌트 드로어 내 **승인 상태** 행 — v2.1 예정. 현재 정답은 프로젝트 레벨 [승인](./approvals.md) 페이지입니다.
- 드로어의 수동 **Concluded 라이선스 오버라이드** 동작(`team_admin`) — v2.2 예정.
- 접미사 없는 변형(`LGPL-3.0` → `LGPL-3.0-or-later`)을 위한 fuzzy SPDX 정규화 — v2.1 예정.
- `ort/rules.kts` 를 통한 조직별 ORT 룰 커스터마이징 — v2.2 예정. 오늘은 `apps/backend/tasks/scan_source.py` 의 하드코딩된 `_LICENSE_CATEGORY_DEFAULTS` 사전이 분류를 결정합니다.

## 함께 보기

- [취약점](./vulnerabilities.md)
- [승인](./approvals.md)
- [SBOM](./sbom.md) — 특히 [v2.0.0 의 컴플라이언스 증거 체인](./sbom.md#compliance-evidence-trail-at-v200)
