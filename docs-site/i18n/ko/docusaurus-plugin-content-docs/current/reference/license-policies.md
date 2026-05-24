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
이번 릴리스는 정책 **데이터 모델 + CRUD API**를 제공합니다. 빌드 게이트와 스캔
분류가 이 정책을 참조하도록(복합 SPDX 해석 포함) 연결하는 작업은 후속 릴리스에서
다룹니다. 그 전까지 정책은 저장·조회는 되지만 게이트 판정을 아직 바꾸지 않습니다.
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
