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

컬럼:

- **이름** — 패키지 이름(예: `lodash`, `org.springframework:spring-web`).
- **버전** — 매니페스트나 락파일에서 고정된 버전.
- **타입** — 생태계(`npm`, `maven`, `pypi`, `golang`, `cargo`, `nuget`, `gem` 등).
- **Concluded 라이선스** — declared·detected를 화해해 ORT가 선택한 라이선스. 빌드 게이트가 사용하는 값.
- **분류** — `Allowed` / `Conditional` / `Forbidden`.
- **결과** — 이 컴포넌트의 미해결 취약점 수(클릭 시 사전 필터링된 Vulnerabilities 탭으로 이동).

테이블은 가상화되어 수천 개의 컴포넌트도 부드럽게 스크롤됩니다.

### 필터

상단 인라인 필터 바:

- **분류** (Allowed / Conditional / Forbidden / Unknown).
- **라이선스** — 정확한 SPDX 표현(예: `MIT`, `LGPL-2.1-only`).
- **미해결 CVE 보유** — 토글.
- **검색** — `name@version` 부분 일치.

필터는 결합됩니다. URL이 갱신되어 필터된 뷰를 공유할 수 있습니다.

## 드로어 — 컴포넌트 상세

행을 클릭하면 우측 슬라이드 드로어가 열립니다.

- **식별자** — `purl`(Package URL), 상위 홈페이지, 레포 URL.
- **모든 라이선스 결과** — declared, detected, concluded와 ORT가 각각을 귀속시킨 소스 파일.
- **의무사항** — concluded 라이선스가 발생시킨 의무([의무사항](#의무사항) 참고).
- **CVE** — 미해결·해결된 결과, Vulnerability 상세로 딥링크.
- **승인 상태** — `Pending` / `Under Review` / `Approved` / `Rejected`([승인](./approvals.md) 참고).
- **Concluded 라이선스 오버라이드** — 자동 결론이 잘못된 경우 `team_admin`이 라이선스를 고정 가능. 오버라이드는 사유와 함께 감사 로그에 기록됩니다.

드로어를 닫아도 테이블 위치를 유지 — 페이지 이동 없음.

## 라이선스 분류

ORT는 모든 라이선스를 세 단계로 분류하며 정의는 `ort/rules.kts`에 있습니다.

| 단계 | 심각도 | 예시 | 효과 |
|---|---|---|---|
| **Allowed** | — | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, Unlicense | 빌드 게이트 영향 없음. |
| **Conditional** | WARNING | LGPL-2.x, LGPL-3.x, MPL-2.0, EPL-1.x, EPL-2.0, CDDL-1.0 | [승인 워크플로우](./approvals.md) 트리거. 빌드 진행. |
| **Forbidden** | ERROR | AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0, BUSL-1.1 | CI에서 빌드 게이트가 종료 코드 1 반환. |

`Unknown`(라이선스 파싱 실패)은 노란 배지의 4번째 단계로 표시되며 항상 사람의 검토가 필요합니다.

분류는 `ort/rules.kts`를 수정하고 재스캔하면 조직별로 조정 가능합니다. 룰 형식은 [아키텍처 참고 → ORT 룰](../reference/architecture.md#ort-rules)을 보세요.

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

프로젝트 페이지의 **Obligations** 탭은 컴포넌트 전반의 의무사항을 통합합니다. **Generate NOTICE**를 클릭하여 모든 저작자 표시·라이선스를 요약한 `NOTICE.txt`를 다운로드 — [SBOM 및 보고서](./sbom.md#notice-파일).

## SPDX 표현

라이선스는 [SPDX 식별자](https://spdx.org/licenses/)로 식별됩니다. 복합 라이선스는 SPDX 표현 문법을 사용합니다.

- `(MIT OR Apache-2.0)` — 듀얼 라이선스; 둘 중 하나 허용.
- `(GPL-2.0+ WITH Classpath-exception-2.0)` — 예외가 있는 GPL.
- `LicenseRef-proprietary` — 비SPDX 라이선스, 파싱은 되나 분류되지 않음.

UI에서 표현 위에 마우스를 올리면 각 컴포넌트 라이선스의 SPDX URL이 표시됩니다.

## 정상 동작 확인

스캔 성공 후:

1. 컴포넌트 수가 예상과 일치(락파일의 고정된 의존성 수에 가까움).
2. Overview 탭의 분류 분포 도넛이 100%로 합산됩니다.
3. 금지 라이선스 컴포넌트가 있으면 빨간색 강조와 함께 [승인 큐](./approvals.md)로 가는 CTA가 보입니다.

## 트러블슈팅

### 많은 컴포넌트가 `Unknown` 라이선스로 표시

ORT가 메타데이터를 파싱할 수 없었습니다. 일반적 원인:

- 패키지에 `LICENSE` 파일도, 메타데이터 선언도 없음(잘 관리되는 생태계에서는 드뭄).
- ORT가 인식 못하는 커스텀 라이선스 문자열. `ort/rules.kts`를 확인하고 매핑 추가를 검토하세요.
- 해당 생태계 소스 fetch 실패. `docker-compose logs worker`에서 ORT의 생태계별 경고를 확인.

### 분류가 잘못된 것 같음

분류는 룰 기반입니다. `ort/rules.kts`를 수정하고 워커를 재시작 후 재스캔. 룰 자체가 맞는데 concluded 라이선스가 잘못이면 컴포넌트 드로어에서 결론을 오버라이드하세요.

### 락파일이 탐지되지 않음

`cdxgen`은 30개 이상의 생태계를 지원하지만 새 생태계는 지속 추가됩니다. 프로젝트 락파일이 레포 루트 또는 한 단계 아래에 있는지 확인하세요. `cdxgen`은 임의 깊이로 재귀하지 않습니다. 미지원 생태계라면 파이프라인 출력과 함께 이슈를 등록하세요.

## 함께 보기

- [취약점](./vulnerabilities.md)
- [승인](./approvals.md)
- [SBOM 및 보고서](./sbom.md)
- [아키텍처 — ORT 룰](../reference/architecture.md#ort-rules)
