---
id: scheduled-scans
title: Scheduled scans
description: 조직 또는 프로젝트 단위로 일간·주간 주기를 설정해 프로젝트가 스스로 스캔을 시작하게 합니다.
sidebar_label: Scheduled scans
sidebar_position: 5
---

# 예약 스캔

예약 스캔은 사람이 **Scan** 버튼을 누르지 않고, CI 작업이나 Webhook 전송도 거치지 않은 채 TRUSCA가 정해진 주기에 따라 스스로 시작하는 스캔입니다. 수동 트리거, [Webhook](./webhooks.md)/CI 트리거에 이어 `source` 스캔을 시작하는 세 번째 방법입니다.

계약은 양방향으로 옵트인입니다. 어디에도 일정이 설정되어 있지 않으면 자동으로 시작되는 스캔은 없으며, 이 기능이 있기 전의 신규 설치와 동작이 같습니다. 이번 릴리스에서 설정은 **API 전용**이며, 아직 설정 화면 UI는 없습니다.

:::note 대상 독자
조직 전체 기본 주기를 설정하는 `super_admin`, 그리고 자신이 속한 팀 프로젝트의 주기를 설정하거나 해제하는 `team_admin`(이상). `developer` 이상은 프로젝트에 실제로 적용되는 일정을 조회할 수 있습니다.
:::

## 사전 준비

- 각 엔드포인트가 요구하는 권한의 JWT 세션 또는 [API Key](../admin-guide/api-keys.md).
- 프로젝트 또는 조직의 UUID.
- 일정을 UTC가 아닌 시간대로 돌리려면 IANA 시간대 이름(예: `Asia/Seoul`).

## 일정을 구성하는 값

일정은 cron 표현식이 아니라 시계 위의 고정된 한 지점입니다. 하루 중 한 시각(`daily`), 또는 한 주 중 한 요일의 한 시각(`weekly`)만 표현합니다.

| 필드 | 타입 | 필수 여부 | 설명 |
|---|---|---|---|
| `is_active` | boolean | 아니오 (기본 `true`) | `false`는 이 행이 명시적으로 아무것도 스캔하지 않는다는 뜻입니다. [프로젝트 일정과 조직 기본값](#프로젝트-일정과-조직-기본값) 참고. |
| `cadence` | `"daily"` \| `"weekly"` \| `null` | 아니오 | `null`이면 이 행은 아직 아무것도 결정하지 않은 상태입니다. |
| `hour` | 0-23 정수 | `cadence`를 설정하면 필수 | `timezone` 기준 로컬 시각(0-23시)이며, 이 시각에 일정이 실행됩니다. |
| `day_of_week` | 0-6 정수 | `cadence`가 `"weekly"`면 필수, `"daily"`면 금지 | `0`=월요일 … `6`=일요일. |
| `timezone` | IANA 시간대 이름 | 아니오 (기본 `"UTC"`) | `hour`와 `day_of_week`는 서버가 아니라 이 시간대를 기준으로 해석됩니다. |

다음 조합은 `422 Unprocessable Entity`로 거부됩니다.

- `cadence: "weekly"`인데 `day_of_week`가 없는 경우.
- `cadence: "daily"`인데 `day_of_week`가 있는 경우.
- `cadence`는 설정했는데 `hour`가 없는 경우.
- `timezone`이 알려진 IANA 이름이 아닌 경우.

## 프로젝트 일정과 조직 기본값

두 범위가 있고, 한 프로젝트에는 항상 둘 중 하나만 적용됩니다.

- **조직 기본값**: 조직당 한 행(`project_id`가 null)입니다. 슈퍼 관리자가 한 번 설정하면 자신만의 결정을 내리지 않은 모든 프로젝트에 적용됩니다.
- **프로젝트 일정**: 프로젝트당 한 행이며, 그 프로젝트 팀의 `team_admin` 이상이 설정합니다. 이 행이 존재하는 순간부터 조직 기본값보다 우선합니다.

이 우선순위는 필드 단위가 아니라 행 전체 단위로 적용됩니다. 프로젝트가 자신의 일정을 이미 정했다면, **그 일정이 `is_active: false`이더라도** 항상 조직 기본값보다 우선합니다. "여기서는 자동 스캔을 하지 않는다"도 하나의 결정이며, 조직 기본값과 섞이거나 조직 기본값에 덮이는 일은 없습니다. 오직 **자신의 행이 없는** 프로젝트만 조직 기본값을 물려받습니다. 자신의 행도 없고 물려받을 조직 기본값도 없는 프로젝트는 예약 스캔이 전혀 없는 상태입니다.

어떤 프로젝트에 실제로 무엇이 적용되는지는 [`GET /v1/scan-schedules/effective/{project_id}`](#실제-적용되는-일정-조회)로 확인합니다. 응답의 `source` 필드가 `"project"`, `"organization"`, `"none"` 중 하나로 답합니다.

## 일정이 실행되면 일어나는 일

일정이 도래하면 Webhook 전송과 같은 방식으로 스캔이 큐에 들어갑니다.

- 스캔은 항상 `kind: "source"`입니다.
- 다른 모든 스캔과 동일한 보호 장치를 그대로 통과합니다. 팀 동시 스캔 상한, 작업 볼륨 디스크 사용량 가드, 프로젝트당 활성 스캔 유일성 규칙입니다. 그 프로젝트에 이미 대기 중이거나 실행 중인 스캔이 있으면 이번 틱은 건너뛰며, 두 번째 스캔을 만들지 않습니다.
- 아카이빙된 프로젝트는 일정이 평가되기 전에 이미 제외됩니다. 프로젝트를 아카이빙하면 일정 행을 건드리지 않고도 예약 스캔이 멈춥니다.

## 완료 알림

예약으로 시작된 스캔이 종료 상태(`succeeded` 또는 `failed`)에 도달하면 그 프로젝트를 소유한 팀의 구성원 전원에게 알림이 갑니다. 받은편지함이 없는 서비스 계정은 제외됩니다. 이 점이 예약 스캔과 수동, Webhook, CI 트리거 스캔의 유일한 차이입니다. 후자는 이미 그 스캔을 시작한 사람이나 시스템이 결과를 보고 있으므로 이 추가 알림이 더해지지 않습니다.

이 알림은 [알림 → 트리거](../user-guide/notifications.md#트리거)에 정리된 `scan_completed` / `scan_failed` 트리거를 그대로 사용합니다. 예약 스캔은 다만 스캔을 시작한 사람 한 명이 아니라 팀 구성원 전원에게 무조건 도달할 뿐입니다. 실제 전달은 각자의 채널 [환경설정](../user-guide/notifications.md#환경설정)을 그대로 따르며, 조직이나 팀에 [수신 라우팅 규칙](../user-guide/notifications.md#routing-rules)이 설정돼 있다면 그 규칙에 따라 추가 수신자나 채널로도 전달됩니다.

## 폴러

Celery beat 작업 하나가 **15분 고정 주기**로 돌며 모든 일정을 매 틱마다 평가합니다. 설정된 일정 건수는 이 구조를 바꾸지 않습니다. 프로젝트 하나에만 일정이 있든 배포 전체 프로젝트에 일정이 있든 폴러는 항상 작업 하나입니다.

도래한 일정이 그 도래 구간 안에서 매 틱마다 다시 실행되지는 않습니다. 폴러는 각 행이 마지막으로 실행된 시각을 기록해, 로컬 기준 하루(`daily`)나 한 주(`weekly`)에 한 번만 실행합니다.

## API 엔드포인트

모든 경로는 `/v1/scan-schedules` 아래에 있습니다.

| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| `PUT` | `/org/{organization_id}` | `super_admin` | 조직의 기본 일정을 생성하거나 교체합니다. |
| `PUT` | `/projects/{project_id}` | 그 프로젝트 팀의 `team_admin` 이상 | 프로젝트 자신의 일정을 생성하거나 교체합니다. |
| `GET` | `/projects/{project_id}` | 팀 구성원(`developer` 이상) | 프로젝트 자체 일정 행을 조회합니다. 자체 일정이 없으면 `404`입니다. "여기 실제로 무엇이 적용되는가"는 [`effective`](#실제-적용되는-일정-조회)로 확인하세요. |
| `DELETE` | `/projects/{project_id}` | 그 프로젝트 팀의 `team_admin` 이상 | 프로젝트 자체 일정을 제거해 다시 조직 기본값을 따르게 합니다. 성공 시 `204`, 자체 일정이 없었다면 `404`입니다. |
| `GET` | `/effective/{project_id}` | 팀 구성원(`developer` 이상) | 이 프로젝트에 실제로 적용되는 일정과, 그 일정이 어느 범위(`project` / `organization` / `none`)에서 왔는지. |

## 조직 기본값 설정

<!-- docs-uat: id=scheduled-scans-org-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/scan-schedules/org/<organization-uuid>" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"is_active": true, "cadence": "daily", "hour": 3, "timezone": "UTC"}'
```

이 조직에서 자체 일정을 설정하지 않은 모든 프로젝트는 매일 UTC 03:00에 `source` 스캔이 실행됩니다.

## 프로젝트 자체 일정 설정

<!-- docs-uat: id=scheduled-scans-project-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/scan-schedules/projects/<project-uuid>" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"is_active": true, "cadence": "weekly", "hour": 9, "day_of_week": 0, "timezone": "Asia/Seoul"}'
```

이 프로젝트는 조직 기본값과 무관하게 매주 월요일 `Asia/Seoul` 09:00에 스캔합니다.

프로젝트를 조직 기본값에서 제외하되 아무것도 삭제하고 싶지 않다면, 같은 형식으로 `"is_active": false`를 보내세요(`cadence`는 그대로 남겨도, `null`로 비워도 됩니다. 어느 쪽이든 이 행이 우선합니다).

## 실제 적용되는 일정 조회

<!-- docs-uat: id=scheduled-scans-effective-get kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -H "Authorization: Bearer ${JWT}" \
  "https://trustedoss.example.com/v1/scan-schedules/effective/<project-uuid>"
```

```json
{
  "project_id": "<project-uuid>",
  "is_active": true,
  "cadence": "weekly",
  "hour": 9,
  "day_of_week": 0,
  "timezone": "Asia/Seoul",
  "source": "project"
}
```

프로젝트 자체 행이 없어 조직 기본값이 적용될 때 `source`는 `"organization"`이고, 둘 다 없을 때는 `"none"`입니다.

## 정상 동작 확인

<!-- docs-uat: id=scheduled-scans-verify-effective kind=manual tier=manual -->
1. `GET /v1/scan-schedules/effective/{project_id}`가 기대한 주기를 반환하고, `source`가 그 주기를 제공한 범위를 정확히 가리킵니다.
<!-- docs-uat: id=scheduled-scans-verify-scan-appears kind=manual tier=manual -->
2. 설정한 로컬 시각이 되면 그 프로젝트에 `kind: "source"`, `scan_metadata.trigger`가 `"schedule"`인 새 스캔이 나타납니다.
<!-- docs-uat: id=scheduled-scans-verify-notification kind=manual tier=manual -->
3. 그 스캔이 끝나면 서비스 계정을 제외한 프로젝트 팀 구성원 전원이 `scan_completed` 또는 `scan_failed` 알림을 받습니다.

## 트러블슈팅

### 일정이 전혀 실행되지 않음

먼저 실제 적용 뷰를 확인하세요. 프로젝트 쓰기의 오타나, 조직 기본값을 예상치 못하게 가리는 프로젝트 행 모두 [`GET /effective/{project_id}`](#실제-적용되는-일정-조회)에 드러납니다.

- `source: "none"`: 프로젝트에도 조직에도 활성 주기가 없습니다. 둘 중 하나를 설정하세요.
- `is_active: false`: 이 범위가 예약을 명시적으로 꺼 둔 상태입니다. 조직 기본값이 적용되길 기대했다면 이 프로젝트에 자체 행이 있는지(`GET /projects/{project_id}`) 먼저 확인하세요. 있다면 삭제해야 조직 기본값으로 돌아갑니다.
- `cadence: null`: 행은 있지만 아직 주기를 쓴 적이 없는 상태입니다.

실제 적용 뷰가 맞다면 시간대를 확인하세요. `hour`와 `day_of_week`는 서버나 브라우저가 아니라 그 일정 자체의 `timezone`을 기준으로 읽힙니다.

### 시각은 지났는데 스캔이 나타나지 않음

폴러는 그 프로젝트가 스캔 가능한 상태일 때만 스캔을 시작합니다. Webhook 전송이 통과하는 것과 같은 보호 장치입니다.

- 폴링 시점에 그 프로젝트의 스캔이 이미 대기 중이거나 실행 중이었다면 이번 구간은 건너뜁니다. 다음 도래 구간(다음 날 또는 다음 주)에 다시 실행되며, 같은 구간 안에서 나중에 실행되지는 않습니다.
- 소유 팀이 동시 스캔 상한에 도달했거나 작업 볼륨 디스크 가드가 걸렸습니다. 여유가 생기거나 디스크 사용량이 내려가면 저절로 풀리며, 일정을 다시 설정할 필요는 없습니다.
- 프로젝트가 아카이빙되었습니다. 아카이빙된 프로젝트는 일정 행과 무관하게 모든 폴링에서 제외됩니다.

### `PUT` 시 `422 Unprocessable Entity`

[일정을 구성하는 값](#일정을-구성하는-값)에 정리한 네 가지 검사가 전부입니다. `weekly` 주기인데 `day_of_week`가 없거나, `daily` 주기인데 `day_of_week`가 있거나, `cadence`만 있고 `hour`가 없거나, `timezone`이 유효한 IANA 이름이 아닌 경우입니다. 응답 본문이 어느 검사에서 실패했는지 알려줍니다.

### `PUT`이나 `DELETE`에서 `403 Forbidden`

조직 엔드포인트는 `super_admin` 전용입니다. 프로젝트 엔드포인트는 **그 프로젝트 자신의 팀**에서 `team_admin` 이상을 요구합니다. 다른 팀 소속이거나, 맞는 팀이라도 `developer` 등급이면 모두 `403`입니다.

### `GET /projects/{project_id}`에서 `404 Not Found`

이 엔드포인트는 프로젝트 **자체** 행만 반환하며, 그런 행이 없으면 404를 답합니다. 이는 오류가 아니라 조직 기본값이(있다면) 적용된다는 뜻일 뿐입니다. 프로젝트가 스스로 무엇을 썼는지가 아니라 실제로 무엇이 적용되는지 보려면 [`GET /effective/{project_id}`](#실제-적용되는-일정-조회)를 쓰세요.

## 함께 보기

- [Webhooks](./webhooks.md): 예약 스캔과 짝을 이루는 이벤트 트리거 방식. 둘 다 같은 스캔 파이프라인과 보호 장치를 공유합니다.
- [Notifications](../user-guide/notifications.md): 완료 알림 위에 적용되는 채널 환경설정과 조직·팀 수신 라우팅 규칙.
- [API keys](../admin-guide/api-keys.md): 로그인 세션 대신 스크립트에서 이 엔드포인트를 호출할 때 쓰는 비대화형 자격 증명 발급.
