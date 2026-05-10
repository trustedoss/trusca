---
id: disk-and-health
title: 디스크 및 시스템 health
description: 시스템 health 대시보드 읽기, 디스크 압박 가드 설정, 스캔이 실패하기 전 조기 경고에 대응합니다.
sidebar_label: 디스크·health
sidebar_position: 3
---

# 디스크 및 시스템 health

포털은 두 운영자 대시보드를 `/admin` 아래 노출합니다.

- **/admin/health** — 모든 컨테이너 서비스와 DT 커넥터의 현재 상태.
- **/admin/disk** — workspace와 데이터베이스 저장소 사용량(설정 가능한 hard limit 포함).

이 둘이 함께 사용자가 알아채기 전에 문제를 잡습니다.

![Admin 시스템 Health — postgres·redis·celery·dependency-track 4-카드 개요](/img/screenshots/admin-health-cards.png)

![Admin 디스크 사용량 — workspace·db 카드와 사용률 게이지](/img/screenshots/admin-disk-list.png)

:::note 대상 독자
호스트를 운영하는 `super_admin`. `docker-compose ps`와 기본 셸에 익숙해야 합니다.
:::

## 시스템 health 대시보드 {#health}

**/admin/health** 페이지는 포털이 의존하는 모든 컴포넌트를 나열합니다. 각 행:

- **컴포넌트** — `postgres`, `redis`, `celery`, `dt`, `disk`, `active_scans`, `last_24h_errors` 중 하나.
- **상태** — `ok`(녹색), `degraded`(노랑), `down`(빨강). UI 라벨은 로케일에 따라 다르나(EN 로케일은 "OK / Degraded / Down" 표시) API 계약은 위 소문자 enum을 발신합니다.
- **마지막 체크** — 가장 최근 프로브 타임스탬프.
- **상세** — `ok`가 아닐 때의 오류 메시지 또는 텔레메트리 요약.

대시보드는 React Query 폴링으로 자동 갱신됩니다(기본 30초; 사용자는 페이지 헤더에서 폴링을 일시 정지 가능). WebSocket 스트림이 아닙니다 — 벽 디스플레이를 원하는 운영자는 탭을 열어 두면 폴링 갱신에 의존할 수 있습니다.

### Health 프로브

각 행은 `services/admin_health_service.py`의 실제 프로브에 매핑됩니다.

| 컴포넌트 | 프로브 |
|---|---|
| `postgres` | 애플리케이션의 asyncpg 풀로 `SELECT 1`. |
| `redis` | asyncio 클라이언트를 통한 `redis-cli ping` 동등 호출. |
| `celery` | Celery `inspect ping`이 설정 타임아웃 이내 응답. |
| `dt` | DT health 프로브([DT 커넥터 → health 모니터](./dt-connector.md#운영-레이어) 참고). DT 가 fail-count 카운터를 가진 유일한 컴포넌트입니다 — 연속 3회 미스 시 `down`으로 전환되며, 나머지는 단일 평가입니다. |
| `disk` | Workspace 볼륨 사용량을 warn / critical 임계와 비교. |
| `active_scans` | 현재 `running` 상태인 스캔 수 — 정보성, 큐 길이가 내부 임계를 넘으면 `degraded` 노출. |
| `last_24h_errors` | 최근 24시간 동안 `ERROR` 레벨 구조화 로그 이벤트 수 — 정보성. |

포털은 `backend`, `worker`, `beat`, `frontend`, `traefik`을 별도 프로브하지 않습니다. 이들의 liveness 는 암묵적입니다 — 대시보드가 렌더되면 backend 가동 중, `celery` 행이 `ok`이면 worker(와 worker 가 의존하는 broker)도 도달 가능.

## 디스크 대시보드 {#disk}

**/admin/disk**는 포털이 신경 쓰는 파일시스템마다 카드 하나를 렌더합니다. v2.0.0 의 실제 카드는 **workspace**, **dt_volume**, **postgres**, **redis** 입니다(API 가 `items: AdminDiskItem[]` 으로 반환하고 페이지가 항목당 카드 하나를 렌더).

각 카드는 warn 임계와 critical 임계가 있습니다.

| 임계 | 기본 | 효과 |
|---|---|---|
| **Warn** | 80% | 노란 카드, 대시보드 배너, 다른 부수 효과 없음. |
| **Critical** | 90% | 빨간 카드, 대시보드 배너, admin 알림 발신. |

`.env`에서 변경:

```bash
DISK_THRESHOLD_WARNING_PCT=80
DISK_THRESHOLD_CRITICAL_PCT=90
```

별개로 **스캔 disk-guard**는 단일 `DISK_HARD_LIMIT_PCT`(기본 `95`)를 사용해 workspace 볼륨이 그 라인을 넘으면 **신규 스캔을 차단**합니다. 분리는 의도된 설계입니다 — 대시보드는 더 이르게(80% / 90%) 경고하고 스캔 가드는 더 늦게(95%) 동작해 운영자에게 놀람을 주지 않으면서 출혈을 막습니다.

```bash
DISK_HARD_LIMIT_PCT=95
```

### "스캔 차단"의 의미

`DISK_HARD_LIMIT_PCT`가 트립되면 `POST /v1/projects/{id}/scans`가 다음을 반환합니다.

```json
{
  "type": "about:blank",
  "title": "Workspace Disk Full",
  "status": 503,
  "detail": "Workspace is at 96% (hard limit 95%). Free space and try again.",
  "instance": "/v1/projects/01H…/scans"
}
```

진행 중 스캔은 **종료되지 않습니다**; 새 제출만 거부됩니다. 작업 손실을 피하면서 출혈은 막습니다.

## 디스크가 가득 찼을 때 할 일

### 1. 원인 식별

```bash
docker-compose -f docker-compose.yml exec backend \
  du -sh /workspace/*  | sort -h | tail -20
```

대개 단일 프로젝트의 레포 + ORT 분석기 출력이 workspace를 지배합니다. `cdxgen` 캐시도 시간이 지나면서 커집니다.

### 2. 공간 확보

```bash
# 30일 이상 지난 ORT 분석기 출력 삭제(안전 — 다음 스캔에서 재생성).
docker-compose -f docker-compose.yml exec backend \
  find /workspace -name "analyzer-result.yml" -mtime +30 -delete

# 아카이브된 프로젝트의 workspace 디렉터리 통째 삭제.
docker-compose -f docker-compose.yml exec backend \
  rm -rf /workspace/<archived-project-id>/
```

### 3. 검증

정리 후 **/admin/disk**가 ~10초 이내에 갱신됩니다. Hard 임계 아래로 내려가면 스캔이 자동으로 다시 수락됩니다 — 서비스 재시작 불필요.

### 4. 장기 대응

- `WORKSPACE_HOST_PATH`를 더 큰 볼륨으로 이동(`.env` 편집, `backend`·`worker` 재시작).
- 로컬 백업이 공간을 잡아먹으면 `BACKUP_RETENTION_DAYS` 축소.
- 백업을 호스트 외부(S3, NFS)로 이동하고 로컬 정리 생략.

## 알림 트리거

디스크 압박은 현재 알림을 생성하지 않습니다 — 운영자는 `/admin/disk`를 직접 모니터링해야 합니다. `disk_pressure` 알림 종류는 로드맵 항목입니다.

## /admin/scans — 스캔 큐와 워커 모니터링

`/admin/scans` 페이지(super-admin 전용)는 조직 전체의 실행 중·대기·성공·실패 스캔을 나열합니다. 운영자는 다음을 수행할 수 있습니다.

- 임의 태스크의 전체 progress 페이로드 + 마지막 로그 프레임 검사.
- 멈춘 스캔 강제 취소(`POST /v1/admin/scans/{scan_id}/cancel`).
- 상태·종류·프로젝트·할당 워커로 필터링.

백엔드: `apps/backend/api/v1/admin/scans.py`. UI: `apps/frontend/src/features/admin/scans/AdminScansPage.tsx`.

## 정상 동작 확인

변경 후:

1. **/admin/health**가 모두 녹색.
2. **/admin/disk**가 warn 라인 아래.
3. 임의 프로젝트 대상 테스트 스캔이 end-to-end 성공.

## 트러블슈팅

### Health 페이지는 모두 `healthy`인데 사용자가 불만

대시보드는 liveness 스냅샷이지 전체 기능 보장이 아닙니다. Liveness가 통과하더라도 다음이 가능:

- 워커가 작업을 받았으나 하위 프로세스에서 멈춤(매우 드뭄). 워커 재시작.
- DT가 `healthy`이지만 NVD 미러가 stale. 수동 동기화 트리거 — [DT 커넥터](./dt-connector.md#트러블슈팅) 참고.

### 디스크 게이지가 잘못됨

게이지는 backend 컨테이너 내부에서 호스트 마운트 볼륨을 읽습니다. 최근에 `WORKSPACE_HOST_PATH`를 변경하고 재시작을 잊으면 게이지가 이전 볼륨을 가리킵니다. backend 재시작.

### Hard limit가 너무 공격적

높이세요. 95%는 호스트 디스크가 바닥나기 전에 운영자가 대응할 여유를 주는 `DISK_HARD_LIMIT_PCT`의 보수적 기본값입니다. 모니터링이 더 이르게 잡으면 낮출 수 있습니다. Warn 임계(80%) 이상에서 일상 운영하는 것은 디스크 추가 신호입니다.

## 로드맵 (v2.x)

다음 기능들은 초기 문서에 언급되었으나 v2.0.0 에는 **반영되지 않았습니다**.

- Health 대시보드의 `backend`, `worker`, `beat`, `frontend`, `traefik` 컴포넌트별 liveness 프로브(현재는 대시보드 렌더링과 `celery` 행에서 추론).
- WebSocket 스트리밍 health 갱신(현재는 React Query 폴링 사용).
- 비-DT 컴포넌트의 다중 샷 연속-미스 상태 머신(현재는 `dt`만 fail-count 카운터 보유).

## 함께 보기

- [DT 커넥터](./dt-connector.md)
- [백업·복원](./backup-and-restore.md)
- [환경변수](../reference/env-variables.md)
