---
id: license-policies
title: 라이선스 정책
description: 팀·조직별 동적 라이선스 정책 — 카테고리 오버라이드·예외·게이트 자세와 REST API.
sidebar_label: 라이선스 정책
sidebar_position: 5
---

# 라이선스 정책

**라이선스 정책**은 팀(또는 조직 전체)이 SPDX 라이선스 식별자를 위험 카테고리
`allowed`·`conditional`·`forbidden`에 어떻게 매핑할지 직접 설정하게 해 줍니다.
정책이 없으면 포털은 내장된 고정 카탈로그로 라이선스를 분류합니다. 정책을 두면 그
분류가 런타임에 편집 가능한 *데이터*가 됩니다 — 재배포가 필요 없습니다.

:::note 상태
정책 **데이터 모델 + CRUD API**, **동적 빌드 게이트 평가**(하드닝된 복합 SPDX
해석 포함), 그리고 **앱 내 편집기**가 이제 연결되었습니다. 유효하고 enabled 인
정책은 해당 팀의 게이트 금지-라이선스 판정을 바꿉니다. 아래
[포털에서 정책 편집](#포털에서-정책-편집)과 [동적 게이트 평가](#동적-게이트-평가)를
참고하세요.
:::

## 범위(Scope)

정책은 다음 두 범위 중 정확히 하나로 존재합니다.

| 범위 | `team_id` | 적용 대상 | 작성 권한 |
| --- | --- | --- | --- |
| **팀** | 설정됨 | 해당 팀 하나 | 팀의 `team_admin` 또는 `super_admin` |
| **조직 기본** | `null` | 팀 정책이 없는 조직 내 모든 팀 | `super_admin` 전용 |

조직마다 조직 기본 정책은 최대 1개, 팀마다 정책은 최대 1개입니다. 같은 범위에 다시
`PUT` 하면 기존 행을 **업데이트**합니다(멱등 upsert).

### 유효 정책 해석

팀을 평가할 때 유효 정책은 다음 순서로 결정됩니다.

1. 팀 자체 정책 — **존재하고 enabled 이면**, 아니면
2. 조직 기본 정책 — **존재하고 enabled 이면**, 아니면
3. 없음 — 팀은 내장 정적 카탈로그로 폴백합니다.

`enabled: false` 는 정책을 삭제하지 않고 비활성화하므로, 팀은 정책을 다시 작성하지
않고도 동적 정책을 껐다 켤 수 있습니다.

## 정책 필드

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `name` | string \| null | UI 표시 라벨. |
| `category_overrides` | object | SPDX id → `allowed` \| `conditional` \| `forbidden`. 해당 id의 카탈로그 판정을 대체. |
| `license_exceptions` | array | 명시적 예외 — 매칭된 라이선스를 `allowed` 로 강제. |
| `unknown_license_category` | enum | 카탈로그·오버라이드 맵에 없는 라이선스의 자세. 기본 `conditional`. |
| `compound_operator_strategy` | object | 복합 SPDX 식(`A AND B`·`A OR B`·`A WITH exc`) 해석 방식. |
| `enabled` | bool | 마스터 토글. `false` → 해석 시 정책 무시. |

### `category_overrides`

```json
{
  "MPL-2.0": "forbidden",
  "EPL-2.0": "conditional",
  "MIT": "allowed"
}
```

### `license_exceptions`

각 항목은 `spdx_id` 와 `reason` 이 필요합니다. `expires_at`(RFC 3339, 선택)으로
게이트가 만료된 예외로 처리하게 할 수 있고, `component_purl`(선택)은 예외를 라이선스를
가진 모든 컴포넌트가 아닌 단일 컴포넌트로 한정합니다.

```json
[
  {
    "spdx_id": "GPL-3.0-only",
    "reason": "법무 승인 예외 TICKET-123",
    "expires_at": "2026-12-31T00:00:00Z",
    "component_purl": "pkg:pypi/somepkg@1.2.3"
  }
]
```

### `compound_operator_strategy`

```json
{
  "AND": "most_restrictive",
  "OR": "least_restrictive",
  "WITH": "most_restrictive"
}
```

값은 `most_restrictive` 또는 `least_restrictive` 입니다. 기본값은 `AND`·`WITH` 에서
가장 제한적인 하위 라이선스를, `OR` 에서는 가장 덜 제한적인 쪽을 유지합니다(이중
라이선스 의존성의 통상적 해석). 부분 객체는 기본값과 병합되므로 바꾸려는 연산자만
보내면 됩니다.

## 포털에서 정책 편집

**Policies** 화면(사이드바 → **정책**, 경로 `/policies`)은 정책을 코드 없이
작성하는 방법으로, 아래에 문서화된 동일한 REST API를 사용합니다.

1. **범위 선택.** 툴바의 **팀** 선택기에서 팀을 고르고 **팀 정책 편집**을
   누르거나, 정책 표의 행을 클릭해 해당 범위를 편집합니다. `super_admin` 은 조직
   전체 폴백을 위한 **조직 기본값 편집** 버튼도 볼 수 있습니다. 선택한 범위는
   URL(`/policies?policy=team:<id>` / `?policy=org:<id>`)에 인코딩되므로 북마크나
   강력 새로고침 후에도 같은 편집기가 다시 열립니다.
2. **드로어에서 편집.** 편집기가 오른쪽에서 슬라이드되며 모든 필드를 노출합니다.
   - **정책 사용** — 마스터 토글. 끄면 정책 내용을 유지한 채 팀이 조직 기본값 또는
     정적 카탈로그로 폴백합니다.
   - **이름** — 선택적 표시 라벨.
   - **미분류 라이선스 처리** — 카탈로그·오버라이드 어디에도 없는 라이선스의 분류.
   - **복합 표현식 전략** — `AND` / `OR` / `WITH` 해석 방식.
   - **분류 재정의** — SPDX id를 분류에 매핑하는 행을 추가·편집·삭제.
   - **라이선스 예외** — 면제 추가·삭제(SPDX id·사유 필수, 만료일·컴포넌트 PURL
     선택).
3. **저장 또는 초기화.** **정책 저장**은 upsert `PUT` 을 보내고, **기본값으로
   초기화**(팀 범위 전용)는 `DELETE` 로 팀 정책을 삭제해 조직 기본값/정적
   카탈로그로 되돌립니다. 서버 검증 실패(과대 맵, 잘못된 식별자)는 오류 토스트로
   표시됩니다.

### 편집 권한

| 역할 | 팀 정책 | 조직 기본값 |
| --- | --- | --- |
| `super_admin` | 모든 팀 | 가능 |
| `team_admin`(해당 팀) | 해당 팀 | 불가 |
| 팀 구성원(관리자 아님) | **읽기 전용** | 불가 |

`team_admin` 이 아닌 팀 구성원은 편집기를 열어 **유효** 정책을 볼 수 있지만 모든
컨트롤이 비활성화되고 읽기 전용 안내가 표시됩니다 — 내부 읽기가 `403` 을 반환하며
UI가 우아하게 저하됩니다.

## 동적 게이트 평가

빌드 차단 게이트([CI 연동](../ci-integration/github-actions.md) 참고)는 프로젝트에
**금지 라이선스** 컴포넌트가 하나라도 있으면 빌드를 차단합니다. 유효 정책이 **없으면**
"금지"는 스캐너가 스캔 시점에 내장 카탈로그로 저장한 라이선스 카테고리를 뜻합니다 —
동작은 변하지 않습니다.

프로젝트 소유 팀에 **유효하고 enabled 인** 정책이 있으면, 게이트는 집계 전에 각
컴포넌트의 라이선스 식을 **동적으로** 재분류합니다.

1. 각 컴포넌트의 저장된 SPDX 식을 하드닝된 복합 SPDX 평가기가 파싱합니다(단일 id,
   `A AND B`, `A OR B`, `A WITH exc`, 괄호, 중첩).
2. 각 피연산자 id를 정책 순서로 해석합니다 — 매칭되고 만료되지 않은 **예외**(`allowed`
   강제) → 명시적 **오버라이드** → 내장 카탈로그 → 카탈로그에 없는 경우
   **`unknown_license_category`** 자세.
3. 피연산자는 연산자별 **`compound_operator_strategy`**(기본: `AND`·`WITH` 가장
   제한적, `OR` 가장 덜 제한적)로 접습니다.
4. 식이 `forbidden` 으로 해석되는 컴포넌트를 집계하고, 1개 이상이면 게이트가 실패합니다.

따라서 팀은 평소 허용되는 라이선스를 **금지**하거나, 평소 금지되는 라이선스를 특정
의존성에 대해 **예외 허용**하거나, 이중 라이선스 `A OR B` 를 관대하게 읽도록 할 수
있습니다 — 모두 재배포 없이. 정책을 비활성화(`enabled: false`)하거나 삭제하면 게이트는
정적 카탈로그로 되돌아갑니다.

### 견고성(Robustness)

라이선스 식은 스캐너 출력과 의존성 메타데이터에서 오는 신뢰할 수 없는 입력입니다.
평가기는 경계가 정해져 있고 안전하게 실패합니다 — 멈추지 않고 게이트를 오류로 끝내지
않습니다. 너무 길거나, 너무 깊게 중첩되거나, 토큰이 너무 많거나, 괄호가 맞지 않거나,
제어 문자를 포함한 식은 **파싱하지 않고** 해당 컴포넌트를 정책의
`unknown_license_category` 자세로 처리하며 경고를 로깅합니다. 경계는 최대 **4096**
문자, 최대 **64** 괄호 중첩, 최대 **1024** 토큰입니다.

## API

모든 엔드포인트는 `/v1/license-policies` 하위에 있으며 JWT가 필요하고, 오류 시 RFC
7807 `application/problem+json` 을 반환합니다.

| 메서드 | 경로 | 권한 | 용도 |
| --- | --- | --- | --- |
| `PUT` | `/v1/license-policies/teams/{team_id}` | `team_admin` | 팀 정책 생성/수정. |
| `GET` | `/v1/license-policies/teams/{team_id}` | 팀 멤버 | 팀의 **유효** 정책 조회. |
| `DELETE` | `/v1/license-policies/teams/{team_id}` | `team_admin` | 팀 정책 초기화(삭제). |
| `PUT` | `/v1/license-policies/org/{organization_id}` | `super_admin` | 조직 기본 정책 생성/수정. |
| `GET` | `/v1/license-policies/org/{organization_id}` | `super_admin` | 조직 기본 정책 조회. |
| `GET` | `/v1/license-policies` | 인증됨 | 조회 가능한 정책 페이지네이션 목록. |

팀 `GET` 은 **유효** 정책(팀 오버라이드, 없으면 조직 기본)을 반환하고, 둘 다 없으면
`404` 를 반환합니다 — 이 `404` 는 "정책 없음, 정적 카탈로그로 폴백" 을 뜻하며 오류가
아닙니다. 조직 엔드포인트는 super-admin 전용이며 존재를 숨깁니다(비-super-admin 은
`404`).

### 예시

```bash
curl -X PUT https://<portal>/v1/license-policies/teams/$TEAM_ID \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Engineering policy",
        "category_overrides": {"MPL-2.0": "forbidden"},
        "license_exceptions": [
          {"spdx_id": "GPL-3.0-only", "reason": "법무 승인 예외 TICKET-123"}
        ],
        "unknown_license_category": "conditional",
        "enabled": true
      }'
```

전체 요청/응답 스키마(예시 포함)는 `/api/docs` 의 라이브 OpenAPI 문서에 있습니다.
정책이 오버라이드하는 내장 카탈로그는 [라이선스 분류 표](../comparison.md)도 참고하세요.
