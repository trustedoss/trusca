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

:::note 대상 독자
호스트를 운영하는 `super_admin`. `docker-compose ps`와 기본 셸에 익숙해야 합니다.
:::

## 시스템 health 대시보드 {#health}

**/admin/health** 페이지는 포털이 의존하는 모든 컴포넌트를 나열합니다. 각 행:

- **컴포넌트** — `backend`, `postgres`, `redis`, `worker`, `beat`, `frontend`, `traefik`, `dt` 중 하나.
- **상태** — `healthy`(녹색), `degraded`(노랑), `down`(빨강).
- **마지막 체크** — 가장 최근 프로브 타임스탬프.
- **상세** — `healthy`가 아닐 때의 오류 메시지.

대시보드는 WebSocket을 통해 매 5초 자동 갱신됩니다. 운영자가 벽 디스플레이에 고정해 둘 수 있습니다.

### Health 프로브

각 행은 실제 프로브에 매핑됩니다.

| 컴포넌트 | 프로브 |
|---|---|
| `backend` | `curl /health`가 5초 이내 200. |
| `postgres` | `pg_isready -U $POSTGRES_USER`. |
| `redis` | `redis-cli ping`이 `PONG` 반환. |
| `worker` | Celery `inspect ping`이 5초 이내 응답. |
| `beat` | Beat 스케줄러가 최근 90초 내 heartbeat 발신. |
| `frontend` | nginx 사이드카의 `curl /healthz`가 200. |
| `traefik` | 엣지 entrypoint가 `:80`에서 도달 가능. |
| `dt` | [DT 커넥터 → health 모니터](./dt-connector.md#운영-레이어) 참고. |

행은 단일 프로브 미스 후 `degraded`, 연속 3회 미스 후 `down`이 됩니다.

## 디스크 대시보드 {#disk}

**/admin/disk**는 두 게이지를 표시합니다.

- **Workspace** — `WORKSPACE_HOST_PATH`를 백업하는 볼륨의 사용량 / 용량.
- **PostgreSQL** — `pg_database_size('trustedoss')` / 볼륨 용량.

두 게이지에 hard 임계와 warn 임계가 있습니다.

| 임계 | 기본 | 효과 |
|---|---|---|
| **Warn** | 70% | 노란 게이지, 대시보드 배너, 다른 부수 효과 없음. |
| **Hard** | 90% | 빨간 게이지, **스캔 차단**, admin 알림 발신. |

`.env`에서 변경:

```bash
DISK_WARN_LIMIT_PCT=70
DISK_HARD_LIMIT_PCT=90
```

### "스캔 차단"의 의미

Hard limit가 트립되면 `POST /api/v1/projects/{id}/scans`가 다음을 반환합니다.

```json
{
  "type": "https://trustedoss.io/problems/disk-pressure",
  "title": "Scans temporarily disabled — disk usage above hard limit",
  "status": 503,
  "detail": "Workspace is at 92% (hard limit 90%). Free space and try again.",
  "instance": "/api/v1/projects/01H…/scans"
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

디스크 사용량이 hard limit를 넘어가면 포털이 **디스크 압박** 알림을 발신합니다.

- 모든 `super_admin`에게 이메일(SMTP 설정 시).
- Slack Webhook(`SLACK_WEBHOOK_URL` 설정 시).
- MS Teams Webhook(`TEAMS_WEBHOOK_URL` 설정 시).

같은 알림은 매 프로브마다 발신되지 않고 임계 교차당 한 번 발신됩니다. Warn 라인 아래로 다시 내려가면 "복구" 알림을 발신합니다.

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

높이세요. 90%는 호스트 디스크가 바닥나기 전에 운영자가 대응할 여유를 주는 보수적 기본값입니다. 모니터링이 더 이르게 잡으면 95%까지 올릴 수 있습니다. 95% 이상 일상 운영은 디스크 추가 신호입니다.

## 함께 보기

- [DT 커넥터](./dt-connector.md)
- [백업·복원](./backup-and-restore.md)
- [환경변수](../reference/env-variables.md)
