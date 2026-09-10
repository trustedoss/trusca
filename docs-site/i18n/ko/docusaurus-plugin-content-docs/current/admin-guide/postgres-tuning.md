---
id: postgres-tuning
title: Postgres 크기 산정과 커넥션 튜닝
description: 백엔드, 워커, beat 프로세스 전체에 걸쳐 Postgres max_connections를 TRUSCA 자체의 풀 설정과 함께, 부팅 시 검증하는 것과 같은 공식으로 산정합니다.
sidebar_label: Postgres 크기 산정과 튜닝
sidebar_position: 8.6
---

# Postgres 크기 산정과 커넥션 튜닝

:::note 대상 독자
프로덕션 배포용 Postgres 크기를 산정하거나, 기존 배포에서 커넥션 고갈 문제를 진단하는 `super_admin` 운영자. `.env` / Helm 값과 Postgres 운영에 대한 기본 지식이 있어야 합니다.
:::

TRUSCA는 Postgres에 대해 커넥션 풀 하나만 쓰지 않습니다. 프로세스마다 하나씩, 여러 개를 돌리며, 각각은 자기만의 `pool_size` + `max_overflow`를 가진 별도의 SQLAlchemy 엔진입니다. Postgres를 제대로 산정한다는 것은 이걸 전부 더하는 것이지, 어느 하나만 따로 튜닝하는 것이 아닙니다. 이 페이지는 그 계산법과, TRUSCA 쪽 튜닝이 끝나고 여러분의 Postgres 인스턴스 자체의 튜닝이 시작되는 지점을 다룹니다.

## 어떤 프로세스가 풀을 갖는가

| 프로세스 | 풀 설정 | 곱해지는 값 |
|---|---|---|
| 백엔드(FastAPI, async) | `DB_POOL_SIZE`(기본 5) + `DB_MAX_OVERFLOW`(기본 3) | 컨테이너당 uvicorn 워커 수(`UVICORN_WORKERS`, 기본 4) x 백엔드 컨테이너/파드 수 |
| Celery 워커(sync) | `DB_SYNC_POOL_SIZE`(기본 3) + `DB_SYNC_MAX_OVERFLOW`(기본 3) | 워커 컨테이너/파드 수. 분리된 큐를 쓴다면 `worker-scan`과 `worker-default`를 합산 |
| Celery beat(sync) | 워커와 같은 `DB_SYNC_*` 설정 | 항상 1 - beat는 설계상 싱글턴이며 절대 스케일하지 않습니다 |
| 마이그레이션 job(`alembic upgrade head`) | 없음 - SQLAlchemy `NullPool`로 한 번에 연결 하나만 사용 | 풀 공식에 들어가지 않고, 아래 고정 admin 여유분에서 다룹니다 |

uvicorn 워커 하나하나가 자기만의 엔진과 풀을 가진 별도 OS 프로세스이기 때문에, 백엔드 행은 워커 수와 컨테이너 수 둘 다를 곱합니다. 기본값인 uvicorn 워커 4개짜리 백엔드 컨테이너 하나만으로도 이미 `4 x (5 + 3) = 32`개의 커넥션을 엽니다. 두 번째 컨테이너나 Celery 프로세스가 끼기도 전에 말입니다.

## 공식

```
backend_conns = backend_replicas x uvicorn_workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW)
worker_conns  = worker_replicas  x (DB_SYNC_POOL_SIZE + DB_SYNC_MAX_OVERFLOW)
beat_conns    = 1                x (DB_SYNC_POOL_SIZE + DB_SYNC_MAX_OVERFLOW)

total = backend_conns + worker_conns + beat_conns + 5 (admin/마이그레이션 여유분)

요구 조건: total <= Postgres max_connections
```

`worker_replicas`는 실행 중인 모든 Celery 워커 컨테이너의 합입니다. `worker-scan`과 `worker-default`는 같은 `DB_SYNC_*` 설정을 공유하므로 프로세스당 커넥션 수가 동일하고, 그래서 이 공식은 두 큐를 따로 추적하지 않고 레플리카 풀 하나로 취급합니다. 고정된 5개짜리 admin 여유분은 마이그레이션 job의 `NullPool` 커넥션 하나와 운영자가 여는 `psql` 세션 한두 개를 위한 것이며, 그 자체가 설정 항목은 아닙니다.

이것은 백엔드 자체의 부팅 시 검사가 Postgres의 실제 `SHOW max_connections`를 대상으로 돌리는 것과 같은 공식이고, Helm 차트의 `NOTES.txt`가 템플릿 산술로 계산하는 것과도 같습니다. 테스트가 둘이 계속 일치하는지 교차 검증하므로, 이 페이지가 별도의, 문서에만 있는 모델을 설명하는 것이 아닙니다.

## 계산 예시

**기본 프로덕션 Compose** (백엔드 컨테이너 1개 x uvicorn 워커 4개, `worker-scan` 레플리카 1개 + `worker-default` 레플리카 1개):

```
backend = 1 x 4 x (5 + 3) = 32
worker  = (1 + 1) x (3 + 3) = 12
beat    = 1 x (3 + 3) = 6
total   = 32 + 12 + 6 + 5 = 55   (Postgres 기본값 max_connections=100 안에 들어옵니다)
```

**기본 Helm 차트** (백엔드 파드 2개 x uvicorn 워커 4개, `worker.scan.replicaCount=2` + `worker.default.replicaCount=1`):

```
backend = 2 x 4 x (5 + 3) = 64
worker  = (2 + 1) x (3 + 3) = 18
beat    = 1 x (3 + 3) = 6
total   = 64 + 18 + 6 + 5 = 93   (max_connections=100 안에 들어오지만 여유가 많지 않습니다)
```

**Dev Compose** (백엔드 컨테이너 1개, `--workers` 플래그 없음, `celery-worker` 서비스 하나만 공유 - dev는 `worker-scan`/`worker-default`로 분리돼 있지 않습니다):

```
backend = 1 x 1 x (5 + 3) = 8
worker  = 1 x (3 + 3) = 6
beat    = 1 x (3 + 3) = 6
total   = 8 + 6 + 6 + 5 = 25   (max_connections=100 안에 여유 있게 들어옵니다)
```

## 여러분의 배포를 산정하기

1. 실제 프로세스 구성을 세십시오: 백엔드 컨테이너/파드 수, `UVICORN_WORKERS`, 그리고 실행 중인 모든 Celery 워커 컨테이너의 합(두 큐로 나눠 쓴다면 둘 다).
2. 위 공식에 여러분의 `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_SYNC_POOL_SIZE` / `DB_SYNC_MAX_OVERFLOW`(설정하지 않았다면 기본값)를 넣어 계산하십시오.
3. `total`을 Postgres 인스턴스의 `max_connections`와 비교하십시오. 맞지 않으면 `max_connections`를 올리거나(커넥션당 대략 10 MB RAM) 더 확장하기 전에 구성 규모를 먼저 줄이십시오.
4. 자신의 레플리카 수를 환경 변수로 읽을 방법이 없다면(일반 Compose에는 그런 변수가 없습니다) `CONN_BUDGET_BACKEND_REPLICAS` / `CONN_BUDGET_WORKER_REPLICAS`를 실제 배포 형태에 맞게 설정하십시오. 그래야 백엔드 자체의 부팅 시 경고가 컨테이너 1개짜리 기본값을 가정하는 대신 정확해집니다. 이 두 값은 그 추정치에만 반영되며, 실제로 몇 개의 컨테이너가 도는지를 바꾸지는 않습니다.

각 풀 설정은 독립적으로 상한이 걸려 있어(풀 크기 200, overflow 200) 값 하나를 잘못 입력했다고 `max_connections`가 통째로 고갈되지는 않습니다. 이 상한을 넘겨 값을 올리면 그대로 적용되는 대신 WARNING으로 로깅되고 상한으로 눌립니다.

## TRUSCA가 튜닝하는 것과 여러분 몫으로 남기는 것

프로덕션 `docker-compose.yml`도 Helm 차트의 번들 Postgres도 `shared_buffers` / `work_mem` / `effective_cache_size` 튜닝을 적용하지 않습니다. 둘 다 컨테이너 리소스 제한(`docker-compose.yml`: CPU 2개 / 메모리 2 GB, 차트의 `postgres.resources`: CPU 250m~2개 / 메모리 256Mi~2Gi)으로만 크기가 정해진 Postgres 업스트림 기본값 그대로 돌아갑니다. 이 저장소에서 튜닝된 `command:`가 있는 곳은 **데모 오버레이**(`docker-compose.demo.yml`) 하나뿐입니다. 4 GB짜리 평가용 박스에 맞춰 `shared_buffers=256MB`, `effective_cache_size=768MB`, `max_connections=60`을 설정합니다. 이 값들은 그 오버레이 자체의 리소스 제한에 맞춘 것이지 일반적인 권장값이 아니며, RAM이 다른 프로덕션 인스턴스에 그대로 옮기라고 있는 값이 아닙니다.

Postgres 자체의 메모리 파라미터를 여러분의 인스턴스에 맞게 산정하는 것은 이 애플리케이션만의 특수한 작업이 아니라 표준적인 Postgres 운영입니다. 흔히 쓰는 경험칙은 `shared_buffers`를 가용 RAM의 4분의 1 정도, `effective_cache_size`를 2분의 1에서 4분의 3 정도로 잡고 실제 관측한 워크로드를 기준으로 다시 조정하는 것입니다. 이건 벤치마크해 볼 출발점으로만 받아들이고, 이 프로젝트가 여러분의 배포에 맞다고 단언하는 숫자로 받아들이지 마십시오.

### 관리형 / 외부 Postgres

Helm 차트의 번들 Postgres는 복제 없는 단일 Pod입니다. 평가용으로는 적합하지만 프로덕션 고가용성 용도는 아닙니다(`postgres.bundled: true`, [Kubernetes에 Helm으로 설치](../installation/helm.md#self-hosted-ha) 참고). 프로덕션에서는 같은 가이드가 관리형 인스턴스(Cloud SQL, RDS) 또는 클러스터 내 전용 오퍼레이터(CloudNativePG, Zalando 오퍼레이터)를 대상으로 한 `postgres.bundled: false`를 권장합니다. 이 구성에서는 메모리 튜닝, 스토리지 IOPS, 복제, 장애 조치가 이 차트가 아니라 관리형 서비스나 오퍼레이터의 몫입니다. 어느 쪽이든 TRUSCA 몫으로 남는 것은 위의 커넥션 예산 계산입니다. 계산한 `total` 값을 여러분의 DBA(또는 관리형 인스턴스 콘솔)에 전달하고 그 값 이상, 향후 레플리카 증가분까지 감안한 여유를 두고 `max_connections`를 요청하십시오.

## 정상 동작 확인

- `docker-compose -f docker-compose.yml logs --tail=100 backend | grep connection_budget` - 정상적으로 부팅됐다면 이 검사에서 아무것도 로깅되지 않습니다. 예산을 넘긴 배포라면 계산에 쓴 모든 입력값과 함께 `connection_budget.over_max_connections`가 로깅되므로, 어느 항목이 너무 큰지 바로 알 수 있습니다.
- Postgres에 접속해 원하는 상한이 맞는지 확인하십시오.

  ```sql
  SHOW max_connections;
  ```

- 평상시 부하에서는 활성 + 유휴 커넥션 수가 그 상한보다 충분히 낮아야 합니다.

  ```sql
  SELECT count(*) FROM pg_stat_activity;
  ```

  이 값이 `max_connections`에 가깝게 붙어 있다면 배포 구성이 마지막으로 산정했던 때보다 커진 것입니다. 현재 레플리카 수로 공식을 다시 돌리십시오.

## 트러블슈팅

### 부팅 로그에 `connection_budget.over_max_connections`가 찍힙니다

로깅된 필드(`backend_conns`, `worker_conns`, `beat_conns`, `total_with_headroom`, `max_connections`)가 어느 항목이 total을 밀어 올렸는지 정확히 보여 줍니다. 더 확장하기 전에 Postgres의 `max_connections`를 올리거나 문제가 된 레플리카 수 / 풀 크기를 줄이십시오. 이건 경고이지 부팅 실패가 아닙니다. 이 추정치는 실시간 카운트가 아니라 운영자가 설정한 `CONN_BUDGET_*` 힌트를 근거로 하므로, 힌트 값이 틀렸다고 배포 자체를 내려서는 안 되기 때문입니다.

### 런타임에 `QueuePool limit of size X overflow Y reached`가 뜹니다

한 프로세스의 애플리케이션 레벨 풀이 실제로 고갈된 상태입니다. 모든 커넥션이 사용 중이고 대기열이 `DB_POOL_TIMEOUT`(기본 30초)을 넘겨 기다리고 있습니다. 먼저 Postgres 자체도 `max_connections`에 가까운지 확인하십시오(위 정상 동작 확인 참고). Postgres 쪽에 여유가 있다면 해당 프로세스의 `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`를 올리십시오. Postgres 쪽에 여유가 없다면 풀을 올려 봤자 고갈 지점이 애플리케이션 대기열에서 Postgres 자체의 커넥션 한도로 옮겨 갈 뿐입니다.

### 롤링 업그레이드 중 마이그레이션 job이 연결에 실패합니다

마이그레이션 job은 `NullPool`(풀이 아니라 커넥션 하나)을 쓰므로 고갈되는 쪽인 경우는 드뭅니다. 다만 job이 시작될 때 애플리케이션 fleet이 이미 상한에 걸려 있다면 Postgres에 내줄 커넥션이 정말 없을 수 있습니다. 이미 포화 상태인 인스턴스에 마이그레이션이 겹칠 때는 admin 여유분을 올리는 대신 애플리케이션 레플리카를 잠시 줄이거나 멈추십시오.

## 함께 보기

- [환경 변수 - 데이터베이스](../reference/env-variables.md) - `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_SYNC_POOL_SIZE`, `DB_SYNC_MAX_OVERFLOW`, 그리고 `DATABASE_URL` 대신 쓸 수 있는 합성 DSN 방식.
- [Kubernetes에 Helm으로 설치](../installation/helm.md#self-hosted-ha) - 번들 Postgres와 외부 Postgres의 차이, 셀프 호스트 고가용성을 위한 오퍼레이터 경로.
- [Docker Compose - 스캔 용량](../installation/docker-compose.md#scan-capacity-sizing-and-scaling) - 스캔 처리량을 위한 `worker-scan` 레플리카 산정. 이 페이지의 `worker_conns` 항목도 같은 레플리카 수를 씁니다.
- [하드닝](./hardening.md) - L1 데이터베이스 역할 분리 모델(`DATABASE_URL_OWNER` / `DATABASE_URL_APP`). 이 페이지의 크기 산정 모델 바로 옆에 있지만, 커넥션 수가 아니라 권한에 대한 내용입니다.
- [백업과 복원](./backup-and-restore.md)
