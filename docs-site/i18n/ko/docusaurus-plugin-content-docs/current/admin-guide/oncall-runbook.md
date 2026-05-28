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

## 시나리오 1 — Trivy DB stale 또는 누락

### 증상
PagerDuty: `TrustedOSS Trivy DB last refresh > 14 days` 또는 `TrustedOSS Trivy DB missing on worker`. 곧 도착하는 `/admin/health → Vulnerability data` 카드(roadmap)가 이를 구동합니다.

### 고객 영향
- 신규 스캔 큐잉은 여전히 가능합니다 — `cdxgen` + scancode가 SBOM과 라이선스 finding을 계속 생성합니다.
- DB refresh가 성공할 때까지 신규 CVE 탐지가 멈춥니다.
- 기존 `vulnerability_findings` 행은 변경 없음 — 갭은 forward-only.

### 진단
```bash
# 1. DB가 디스크에 있는가?
docker-compose -f docker-compose.yml exec worker \
  ls -lh /var/lib/trivy/db/
# 2. DB 메타데이터(Created 타임스탬프)
docker-compose -f docker-compose.yml exec worker \
  cat /var/lib/trivy/db/metadata.json
# 3. 최근 download / refresh 로그
docker-compose -f docker-compose.yml logs --tail=500 worker | grep trivy_db
docker-compose -f docker-compose.yml logs --tail=500 beat | grep trivy_db_refresh
# 4. ghcr.io로 outbound HTTPS 도달 가능?
docker-compose -f docker-compose.yml exec worker \
  curl -fsS https://ghcr.io/v2/ -o /dev/null -w "%{http_code}\n"
```

### 복구(순서대로)
1. **일회성 refresh 강제**(권장 — 단일 명령, 재시작 없음):
   ```bash
   docker-compose -f docker-compose.yml exec worker \
     celery -A apps.backend.tasks.celery_app call tasks.trivy_db.refresh
   sleep 30
   docker-compose -f docker-compose.yml exec worker \
     cat /var/lib/trivy/db/metadata.json | jq '.Created'
   ```
2. **비우고 재다운로드**(메타데이터 손상 시):
   ```bash
   docker-compose -f docker-compose.yml exec worker \
     rm -rf /var/lib/trivy/db
   docker-compose -f docker-compose.yml restart worker
   ```
   부팅 시 `trivy --download-db-only`가 실행되어 1~3분 내 디렉터리를 재채움.
3. **미러 폴백**(워커에서 `ghcr.io` 도달 불가 시): `TRIVY_DB_REPOSITORY`를 사내 미러로 설정 — [취약점 데이터 — Air-gapped 운영](./vulnerability-data.md#air-gapped) 참조.

복구 후 자동 재매칭 beat이 다음 사이클에서 기존 스캔에 대해 누락된 CVE를 가져옵니다 — 운영자 액션 불필요.

### 에스컬레이션
- 두 번의 refresh 시도가 같은 오류로 실패하거나,
- 최근 `trivy registry login` 후에도 사내 미러가 `unauthorized`를 반환하거나,
- `metadata.json`은 존재하지만 여러 생태계의 spot 스캔에서 `Results`가 빈 경우(스키마 불일치 시사).

포털 개발팀 호출 시 첨부: 워커 로그(`docker-compose logs --tail=2000 worker`), `metadata.json` 내용, 워커 내부에서 `trivy --version` 출력.

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
curl -fsS "https://<your-host>/v1/admin/backup/list" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.items[0:5]'
# 4. 백업 볼륨의 디스크 여유 공간
docker-compose exec backend df -h /backups
```

### 복구
1. **수동 트리거**(UI: `/admin/backup` → **Run manual backup now**, 또는):
   ```bash
   curl -fsS -X POST "https://<your-host>/v1/admin/backup/trigger" \
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
curl -fsS "https://<your-host>/v1/scans/<scan_id>" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.progress_payload, .latest_log_frame'
# 2. Celery active task 목록
docker-compose exec worker celery -A apps.backend.tasks.celery_app inspect active
# 3. 워커 프로세스 트리(고아 서브프로세스 확인)
docker-compose exec worker ps -ef | grep -E 'cdxgen|ort|trivy'
```

### 복구
1. **스캔 강제 취소**(권장 — 워커 전반 영향 없음):
   ```bash
   curl -fsS -X POST "https://<your-host>/v1/admin/scans/<scan_id>/cancel" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **취소로도 태스크가 해제되지 않으면(워커가 진짜로 행 상태)**:
   ```bash
   # 최후의 수단 — 이 워커의 실행 중 모든 태스크를 죽입니다.
   docker-compose restart worker
   ```
   같은 워커에서 실행 중이던 다른 스캔은 failed 로 기록되며 수동 재실행이 필요합니다.

### 에스컬레이션
- 동일 프로젝트가 같은 단계에서 연속 2회 멈출 때(콘텐츠 측 문제 — 거대한 git 이력, 잘못된 lockfile, `trivy sbom` 타임아웃 등 시사). `<scan_id>` 와 해당 태스크로 필터링한 마지막 200 라인 `worker` 로그를 첨부해 포털 개발팀에 호출.

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
curl -fsS "https://<your-host>/v1/admin/disk" \
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
3. **Trivy DB 볼륨**(`/admin/disk` 가 `trivy_db` 를 원인으로 표시): Trivy DB는 약 500 MB이며 더 이상 자라선 안 됩니다. 만약 커졌다면 캐시를 비우고 재다운로드 (`docker-compose -f docker-compose.yml exec worker rm -rf /var/lib/trivy/db && docker-compose restart worker`).
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
- Trivy DB 이슈: 워커의 `/var/lib/trivy/db/metadata.json` 내용 + `docker-compose logs --tail=500 worker | grep trivy_db`.
- 스캔 이슈: `<scan_id>` 와 `/v1/scans/<scan_id>` 전체 JSON.

## 함께 보기

- [취약점 데이터 (Trivy DB)](./vulnerability-data.md) — DB 라이프사이클과 트러블슈팅.
- [백업·복원](./backup-and-restore.md) — 백업 보존 + 복원 흐름.
- [디스크·health](./disk-and-health.md) — 디스크 임계 모델 + Health 대시보드.
