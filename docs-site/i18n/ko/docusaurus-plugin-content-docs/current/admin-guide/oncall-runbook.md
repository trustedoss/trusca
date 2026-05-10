---
id: oncall-runbook
title: 온콜 런북
description: TrustedOSS Portal 운영을 겨냥한 PagerDuty / 프로덕션 알림에 대한 1차 대응 플레이북.
sidebar_label: 온콜 런북
sidebar_position: 99
---

# 온콜 런북

프로덕션 TrustedOSS Portal 스택에 대해 가장 빈번한 4개의 PagerDuty 알림에 대한 빠른 참조 플레이북입니다. 각 시나리오는 다음을 나열합니다:

- **증상** — 페이지를 트리거한 것
- **고객 영향** — 사용자가 지금 할 수 있는 / 할 수 없는 것
- **진단** — 실행할 정확한 명령(호스트 + 컨테이너)
- **복구** — 순서대로 수행할 조치
- **에스컬레이션** — 포털 개발팀을 깨워야 하는 시점

모든 명령은 `docker-compose` V1(하이픈) 과 `bash` 호스트 셸을 가정합니다.

:::tip Super-admin 토큰 발급(대부분 curl 예시에서 사용)
```bash
# EMAIL/PASSWORD 를 설치 시 생성한 super-admin 으로 교체하세요.
EMAIL=admin@example.com
PASSWORD=...
ACCESS_TOKEN=$(curl -fsS -X POST "https://<your-host>/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | jq -r '.access_token')
```
:::

## 시나리오 1 — DT 다운 15분 이상

### 증상
PagerDuty: `TrustedOSS DT health = down for 15+ min` (`/admin/dt` 프로브 또는 외부 모니터에서).

### 고객 영향
- 신규 스캔 큐잉은 여전히 가능합니다 — 차단기가 OPEN 일 때 정책 게이트는 캐시된 취약점 데이터로 폴백합니다.
- DT 가 복구될 때까지 신규 CVE 알림이 지연됩니다(신선한 취약점 미러 없음).
- 포털 UI, 로그인, 기존 프로젝트 데이터는 모두 영향이 없습니다.

### 진단
```bash
# 1. DT 컨테이너 살아있는가?
docker-compose ps dt
# 2. 최근 DT 로그(마지막 200 라인)
docker-compose logs --tail=200 dt | grep -iE 'error|fatal'
# 3. 포털이 본 DT health(구조화 로그)
docker-compose logs --tail=500 backend | grep dt_health_check | tail -10
# 4. 포털에서 차단기 상태 조회
curl -fsS "https://<your-host>/api/v1/admin/dt/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

### 복구(순서대로)
1. **컨테이너 재시작**(대다수 — OOM, 일시적인 JVM 행):
   ```bash
   docker-compose restart dt
   sleep 30
   curl -fsS https://<your-host>/api/v1/admin/dt/health-check \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **차단기 수동 리셋**(DT 복구 후에도 차단기가 OPEN 으로 남아 있을 때):
   ```bash
   curl -fsS -X POST "https://<your-host>/api/v1/admin/dt/breaker/reset" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
3. **미러 재동기화**(복구 후 취약점 데이터가 stale 해 보일 때): 한 시간 단위 beat 사이클(`celery_app.py`의 `dt_findings_resync` 태스크)을 한 번 기다립니다.

### 에스컬레이션
- 2회 재시작 후에도 DT 컨테이너가 떠 있지 못하거나,
- `health-check` 가 녹색인데도 차단기가 OPEN 으로 남아 있거나,
- `dt_health_check` 로그가 DB 측 오류(DT 에서 Postgres 도달 불가) 를 보일 때.

포털 개발팀에 호출하면서 다음을 첨부: 컨테이너 로그(`docker-compose logs --tail=2000 dt`), `/admin/dt/status` 의 차단기 이력, 최근 5분간 `backend` 로그.

## 시나리오 2 — 자동 백업 3일 연속 실패

### 증상
PagerDuty: `TrustedOSS auto-backup task failure count = 3`.

### 고객 영향
- 호스트가 크래시하면 포털의 모든 데이터가 위험합니다(복원할 최근 백업 없음). 신선한 백업이 도착할 때까지 다운스트림 작업(컴플라이언스 동결 등)을 계획하세요.

### 진단
```bash
# 1. Celery Beat 스케줄 하트비트
docker-compose logs --tail=500 beat | grep daily-auto-backup
# 2. 워커 로그에서 백업 태스크 실행
docker-compose logs --tail=2000 worker | grep -E 'backup\.(completed|failed)' | tail -20
# 3. 가장 최근 백업 행 + 상태
curl -fsS "https://<your-host>/api/v1/admin/backup/list" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.items[0:5]'
# 4. 백업 볼륨의 디스크 여유 공간
docker-compose exec backend df -h /backups
```

### 복구
1. **수동 트리거**(UI: `/admin/backup` → **Run manual backup now**, 또는):
   ```bash
   curl -fsS -X POST "https://<your-host>/api/v1/admin/backup/trigger" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **수동도 실패하면 — `pg_dump` 를 직접 확인**:
   ```bash
   docker-compose exec backend bash -c \
     'BACKUP_NAME=debug-$(date +%Y%m%dT%H%M%SZ); \
      bash /app/scripts/backup.sh --name "$BACKUP_NAME" 2>&1'
   ```
   - Permission denied → `BACKUPS_ROOT` 볼륨 마운트 문제(compose 의 `backups:/backups` 매핑 확인).
   - Server version mismatch → 워커 이미지에 `postgresql-client-17` 미설치(회귀 — 에스컬레이션).
   - 디스크 가득참 → 시나리오 4 참고.

### 에스컬레이션
- `bash scripts/backup.sh` 가 디스크·권한 외 사유로 실패하거나,
- 가장 최근 성공 백업이 7일 이상 지난 경우(자동 정리 윈도 — 복원 옵션이 좁아짐).

## 시나리오 3 — 스캔이 `running` 에서 4시간 이상 멈춤

### 증상
PagerDuty: `TrustedOSS scan running > 4h for project X`.

### 고객 영향
- 해당 프로젝트: 신규 스캔이 차단됩니다(한 번에 1건 실행 정책).
- 다른 프로젝트: 워커 동시성=1 인 경우(기본값 2)가 아니면 영향 없음.

### 진단
```bash
# 1. 어느 단계에서 멈췄는가?
curl -fsS "https://<your-host>/api/v1/scans/<scan_id>" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.progress_payload, .latest_log_frame'
# 2. Celery active task 목록
docker-compose exec worker celery -A apps.backend.tasks.celery_app inspect active
# 3. 워커 프로세스 트리(고아 서브프로세스 확인)
docker-compose exec worker ps -ef | grep -E 'cdxgen|ort|trivy'
```

### 복구
1. **스캔 강제 취소**(권장 — 워커 전반 영향 없음):
   ```bash
   curl -fsS -X POST "https://<your-host>/api/v1/admin/scans/<scan_id>/cancel" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **취소로도 태스크가 해제되지 않으면(워커가 진짜로 행 상태)**:
   ```bash
   # 최후의 수단 — 이 워커의 실행 중 모든 태스크를 죽입니다.
   docker-compose restart worker
   ```
   같은 워커에서 실행 중이던 다른 스캔은 failed 로 기록되며 수동 재실행이 필요합니다.

### 에스컬레이션
- 동일 프로젝트가 같은 단계에서 연속 2회 멈출 때(콘텐츠 측 문제 — 거대한 git 이력, 잘못된 lockfile, DT 타임아웃 등 시사). `<scan_id>` 와 해당 태스크로 필터링한 마지막 200 라인 `worker` 로그를 첨부해 포털 개발팀에 호출.

## 시나리오 4 — 호스트 디스크 95% 이상

### 증상
PagerDuty: `TrustedOSS portal disk = 95%+`.

### 고객 영향
- 실행 중 스캔은 계속 진행됩니다. 신규 스캔은 `DISK_HARD_LIMIT_PCT` 임계(기본 95%) 에서 **차단**됩니다 — `/admin/scans` 에 무한 큐 상태로 표시됩니다.

### 진단
```bash
# 1. 호스트 전체
df -h /opt/trustedoss
docker system df
# 2. 포털을 통한 카드별 분해
curl -fsS "https://<your-host>/api/v1/admin/disk" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
# 3. Workspace 분해(가장 흔한 원인)
docker-compose exec worker du -sh /workspace/* | sort -h | tail -10
# 4. Postgres 데이터베이스 크기
docker-compose exec postgres psql -U trustedoss -d trustedoss \
  -c "SELECT pg_size_pretty(pg_database_size('trustedoss'));"
```

### 복구
1. **Workspace 정리**(거의 항상 정답):
   ```bash
   docker-compose exec worker find /workspace -mindepth 1 -mtime +30 -delete
   ```
2. **Postgres bloat**(`pg_database_size` > 2 GB 이고 최근 급증한 경우): 무거운 테이블을 VACUUM.
   ```bash
   docker-compose exec postgres psql -U trustedoss -d trustedoss \
     -c "VACUUM FULL audit_logs, vulnerability_findings;"
   ```
3. **DT 볼륨**(`/admin/disk` 가 `dt_volume` 을 원인으로 표시): DT 재시작으로 인덱스 임시 파일을 비웁니다(`docker-compose restart dt`).
4. **일시적인 임계 상향**(임시 방편일 뿐, 근본 해결책이 아닙니다):
   ```bash
   # .env 편집: DISK_HARD_LIMIT_PCT=98
   docker-compose up -d backend worker
   ```

### 에스컬레이션
- workspace 정리 후에도 디스크가 90% 초과로 남아 있거나,
- `audit_logs` 가 24시간마다 두 배로 늘어나는 Postgres 증가세(근본 원인 필요 — 폭주하는 통합이 이벤트를 쏟아내는 가능성).

## 표준 에스컬레이션 양식

포털 개발팀에 호출 시 다음을 첨부:

- 시나리오 번호(1-4)와 PagerDuty 알림 URL.
- 포털 버전: `docker-compose exec backend python -c "from main import APP_VERSION; print(APP_VERSION)"`
- 관련 컨테이너의 마지막 2000 라인: `docker-compose logs --tail=2000 <svc>`
- DT 이슈: `/admin/dt/status` 전체 JSON.
- 스캔 이슈: `<scan_id>` 와 `/api/v1/scans/<scan_id>` 전체 JSON.

## 함께 보기

- [DT 커넥터](./dt-connector.md) — 회로 차단기 모델 + 리셋 절차.
- [백업·복원](./backup-and-restore.md) — 백업 보존 + 복원 흐름.
- [디스크·health](./disk-and-health.md) — 디스크 임계 모델 + Health 대시보드.
