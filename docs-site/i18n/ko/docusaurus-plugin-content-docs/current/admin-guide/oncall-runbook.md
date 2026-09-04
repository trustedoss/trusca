---
id: oncall-runbook
title: 온콜 런북
description: TRUSCA 운영을 겨냥한 PagerDuty / 프로덕션 알림에 대한 1차 대응 플레이북.
sidebar_label: 온콜 런북
sidebar_position: 99
---

# 온콜 런북

프로덕션 TRUSCA 스택에서 가장 자주 나오는 PagerDuty·알림에 대한 빠른 참조 플레이북입니다. 각 시나리오는 다음을 나열합니다:

- **증상** — 페이지를 트리거한 것
- **고객 영향** — 사용자가 지금 할 수 있는 / 할 수 없는 것
- **진단** — 실행할 정확한 명령(호스트 + 컨테이너)
- **복구** — 순서대로 수행할 조치
- **에스컬레이션** — 포털 개발팀을 깨워야 하는 시점

모든 명령은 `docker-compose` V1(하이픈)과 `bash` 호스트 셸을 가정합니다.

:::tip Super-admin 토큰 발급(대부분 curl 예시에서 사용)
<!-- docs-uat: id=oncall-auth-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-placeholder-creds -->
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
PagerDuty: `TRUSCA Trivy DB last refresh > 14 days` 또는 `TRUSCA Trivy DB missing on worker`. 곧 도착하는 `/admin/health → Vulnerability data` 카드(roadmap)가 이를 구동합니다.

### 고객 영향
- 신규 스캔 큐잉은 여전히 가능합니다 — `cdxgen` + scancode가 SBOM과 라이선스 finding을 계속 생성합니다.
- DB refresh가 성공할 때까지 신규 CVE 탐지가 멈춥니다.
- 기존 `vulnerability_findings` 행은 변경 없음 — 갭은 forward-only.

### 진단
<!-- docs-uat: id=oncall-trivy-db-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-worker -->
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
PagerDuty: `TRUSCA auto-backup task failure count = 3`.

### 고객 영향
- 호스트가 크래시하면 포털의 모든 데이터가 위험합니다(복원할 최근 백업 없음). 신선한 백업이 도착할 때까지 다운스트림 작업(컴플라이언스 동결 등)을 계획하세요.

### 진단
<!-- docs-uat: id=oncall-backup-beat-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-logs -->
```bash
# 1. Celery Beat 스케줄 하트비트
docker-compose logs --tail=500 beat | grep daily-auto-backup
# 2. 워커 로그에서 백업 태스크 실행
docker-compose logs --tail=2000 worker | grep -E 'backup\.(completed|failed)' | tail -20
# 3. 가장 최근 백업 행 + 상태
curl -fsS "https://<your-host>/v1/admin/backup/list" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.items[0:5]'
# 4. 백업 볼륨의 디스크 여유 공간(BACKUPS_ROOT는 백엔드 컨테이너에서
#    /opt/trustedoss/backups에 마운트됨)
docker-compose -f docker-compose.yml exec backend df -h /opt/trustedoss/backups
```

### 복구
1. **수동 트리거**(UI: `/admin/backup` → **Run manual backup now**, 또는):
   ```bash
   curl -fsS -X POST "https://<your-host>/v1/admin/backup/trigger" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **수동도 실패하면 — 호스트 백업 스크립트를 직접 실행**:

   `scripts/backup.sh`는 **호스트** 스크립트입니다. `pg_dump`를
   `docker-compose ... exec`로 호출하고 워크스페이스 마운트를 tar로 묶으므로,
   컨테이너 안이 아니라 호스트에서 실행하십시오. `BACKUP_DIR`이 설정되면 그
   경로에, 아니면 레포 루트의 `backups/<stamp>`(`/opt/trustedoss/backups`에
   마운트됨)에 기록합니다.
   ```bash
   # docker-compose.yml + .env가 있는 호스트의 배포 디렉터리에서 실행합니다.
   BACKUP_DIR=backups/debug-$(date +%Y%m%d-%H%M%S) bash scripts/backup.sh --no-prune 2>&1
   ```
   - `.env not found` → 배포 디렉터리에서 실행하거나, 설치가 완료되지 않았습니다.
   - Server version mismatch → postgres 이미지에 `postgresql-client-17` 미설치(회귀 — 에스컬레이션).
   - 디스크 가득참 → 시나리오 4 참고.

### 에스컬레이션
- `bash scripts/backup.sh`가 디스크·권한 외 사유로 실패하거나,
- 가장 최근 성공 백업이 7일 이상 지난 경우(자동 정리 윈도 — 복원 옵션이 좁아짐).

## 시나리오 3 — 스캔이 `running`에서 4시간 이상 멈춤

### 증상
PagerDuty: `TRUSCA scan running > 4h for project X`.

### 고객 영향
- 해당 프로젝트: 신규 스캔이 차단됩니다(한 번에 1건 실행 정책).
- 다른 프로젝트: 워커 동시성=1 인 경우(기본값 2)가 아니면 영향 없음.

### 진단
<!-- docs-uat: id=oncall-scan-stuck-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose -->
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
   같은 워커에서 실행 중이던 다른 스캔은 failed로 기록되며 수동 재실행이 필요합니다.

### 에스컬레이션
- 동일 프로젝트가 같은 단계에서 연속 2회 멈출 때(콘텐츠 측 문제 — 거대한 git 이력, 잘못된 lockfile, `trivy sbom` 타임아웃 등 시사). `<scan_id>`와 해당 태스크로 필터링한 마지막 200 라인 `worker` 로그를 첨부해 포털 개발팀에 호출.

## 시나리오 4 — 호스트 디스크 95% 이상

### 증상
PagerDuty: `TRUSCA disk = 95%+`.

### 고객 영향
- 실행 중 스캔은 계속 진행됩니다. 신규 스캔은 `DISK_HARD_LIMIT_PCT` 임계(기본 95%)에서 **차단**됩니다 — `/admin/scans`에 무한 큐 상태로 표시됩니다.

### 진단
<!-- docs-uat: id=oncall-disk-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-host-df -->
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
3. **Trivy DB 볼륨**(`/admin/disk`가 `trivy_db`를 원인으로 표시): Trivy DB는 약 500 MB이며 더 이상 자라선 안 됩니다. 만약 커졌다면 캐시를 비우고 재다운로드 (`docker-compose -f docker-compose.yml exec worker rm -rf /var/lib/trivy/db && docker-compose restart worker`).
4. **일시적인 임계 상향**(임시 방편일 뿐, 근본 해결책이 아닙니다):
   ```bash
   # .env 편집: DISK_HARD_LIMIT_PCT=98
   docker-compose up -d backend worker
   ```

### 에스컬레이션
- workspace 정리 후에도 디스크가 90% 초과로 남아 있거나,
- `audit_logs`가 24시간마다 두 배로 늘어나는 Postgres 증가세(근본 원인 필요 — 폭주하는 통합이 이벤트를 쏟아내는 가능성).

## 시나리오 5 — 큐 적체 알림 발생

### 증상
`trustedoss.scan` 또는 `trustedoss.default`에 대해 "Queue backlog alert" 제목의 Slack/Teams 메시지(기존 알림 채널이며 새로 붙인 연동이 아닙니다). `QUEUE_BACKLOG_ALERT_ENABLED`와 `QUEUE_BACKLOG_METRICS_ENABLED`가 둘 다 켜져 있어야 발생합니다. [환경변수 - 큐 적체 알림](../reference/env-variables.md)과 [Docker Compose - 스캔 용량](../installation/docker-compose.md#scan-capacity-sizing-and-scaling)을 참고하세요.

### 고객 영향
- `trustedoss.scan`: 새 스캔이 기존 스캔 뒤로 밀려 시작까지 더 오래 걸립니다. 아무것도 실패하지는 않습니다 - 오류가 아니라 용량 신호입니다.
- `trustedoss.default`: 알림, 백업, 감사 반출, 티켓 웹훅이 지연됩니다. 오래 지속된다면 단순 과부하보다 워커가 멈춘 상황을 먼저 의심하세요(진단 참고).

### 진단
<!-- docs-uat: id=oncall-queue-backlog-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-worker -->
```bash
# 1. 현재 적체량과 가장 오래 기다린 스캔의 대기 시간(METRICS_ENABLED도 필요)
curl -fsS "https://<your-host>/metrics" | grep -E 'trusca_broker_queue_backlog|trusca_scan_queue_wait_seconds'
# 2. worker-scan 레플리카가 실제로 떠서 소비하고 있는가?
docker-compose -f docker-compose.yml ps worker-scan
docker-compose -f docker-compose.yml exec worker-scan celery -A apps.backend.tasks.celery_app inspect active
# 3. 최근 스캔 처리량 - 스캔이 끝나고는 있는가, 아니면 쌓이기만 하는가?
curl -fsS "https://<your-host>/v1/admin/scans?status=queued" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.total'
```

### 복구
1. **도착률이 용량을 넘어선 경우(흔한 경우)**: 알림이 지목한 워커 서비스를 확장하세요. 스캔 처리량은 `worker-scan`, 나머지는 `worker-default`입니다 - 엉뚱한 쪽을 확장하면 아무것도 나아지지 않습니다(위에 링크한 용량 가이드 참고):
   ```bash
   docker-compose -f docker-compose.yml up -d --scale worker-scan=4
   ```
2. **실제 부하가 아니라 워커가 멈춘 경우**: `celery inspect active`에 정상 스캔보다 훨씬 오래 도는 태스크가 보이면(`SCAN_HARD_TIME_LIMIT_SECONDS`와 비교) 그 스캔부터 시나리오 3의 복구 절차를 따르세요. 멈춘 태스크를 정리하면 필요하지도 않은 용량을 영구히 늘리지 않고도 슬롯이 풀립니다.
3. **정상화 확인**: 여전히 적체 상태라면 알림은 쿨다운 간격(`QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS`, 기본 1시간)마다 다시 발생하고, 이후 어느 beat 틱(5분 간격)에서 적체량이 임계값 이하로 내려가면 멈춥니다.

### 에스컬레이션
- `worker-scan`을 확장해도 스캔 한 건 소요 시간만큼의 주기 안에 적체가 줄지 않을 때(디스크·Postgres·브로커 자체가 병목일 가능성), 또는
- 같은 큐에 대해 쿨다운을 넘겨서도 며칠에 걸쳐 알림이 계속 재발할 때.

## 시나리오 6 — 워커가 부팅하자마자 `task_registry.empty`로 재시작합니다

### 증상
워커 컨테이너가 뜨지 못하고 재시작을 반복합니다. 로그 끝에 `task_registry.empty`가
있거나, 프로세스 stderr에 다음 줄이 나옵니다.

```
FATAL task_registry.empty: this worker registered none of the portal's tasks
```

Compose에서는 재시작 반복으로, Kubernetes에서는 CrashLoopBackOff로 나타납니다.

### 고객 영향
그 워커가 맡을 스캔이 돌지 않습니다. 다만 큐에 남아 있고 사라지지는 않습니다.
워커가 멈추는 이유가 그것입니다. 태스크가 없는 워커가 계속 돌면 그 메시지들을
가져가서 실행하지 못하고 버리고, 나중에 정상 워커를 붙여도 할 일이 남아 있지
않습니다.

같은 종류의 다른 워커가 정상이면 그쪽이 일을 가져가므로 고객 영향은 없고
재시작하는 컨테이너만 남습니다.

### 진단
워커의 태스크 목록은 애플리케이션 안의 include 목록으로 만들어집니다. 그래서 이것은
설정 문제가 아니라 배포나 패키징 문제입니다. 목록이 비었거나, 목록의 어떤 모듈도
import되지 않았다는 뜻입니다.

```bash
# 1. 내려가면서 워커가 남긴 말
docker-compose -f docker-compose.yml logs --tail=50 worker-scan | grep -i task_registry
# 2. 그 이미지 안에서 모듈이 import되는지
docker-compose -f docker-compose.yml run --rm --entrypoint python worker-scan \
  -c "from tasks.celery_app import celery_app; celery_app.loader.import_default_modules(); \
      print(len([t for t in celery_app.tasks if t.startswith('trustedoss.')]))"
```

두 번째 명령이 가드가 보는 개수를 그대로 찍습니다. 정상 이미지는 20~30대의 수를
찍습니다. 0이거나 트레이스백이면 그것이 원인입니다.

### 복구
1. 이미지가 어긋났거나 일부만 담긴 경우입니다. 태그 이미지를 다시 받아 서비스를
   재생성합니다. 불완전한 트리에서 빌드된 이미지가 흔한 원인입니다.
2. import에 실패하는 모듈이 있는 경우입니다. 위 두 번째 명령이 트레이스백을
   보여 줍니다. 설정으로 우회할 것이 아니라 릴리스의 결함이므로 이전 태그로
   되돌립니다.

### 에스컬레이션
정식 릴리스 이미지에서 두 번째 명령이 트레이스백을 보이면 즉시 올립니다. 그 태그를
쓰는 모든 배포의 해당 워커가 전부 같은 상태입니다.

## 표준 에스컬레이션 양식

포털 개발팀에 호출 시 다음을 첨부:

- 시나리오 번호(1-5)와 PagerDuty 알림 URL.
- 포털 버전: `docker-compose -f docker-compose.yml exec backend python -c "from main import app; print(app.version)"`
- 관련 컨테이너의 마지막 2000 라인: `docker-compose logs --tail=2000 <svc>`
- Trivy DB 이슈: 워커의 `/var/lib/trivy/db/metadata.json` 내용 + `docker-compose logs --tail=500 worker | grep trivy_db`.
- 스캔 이슈: `<scan_id>`와 `/v1/scans/<scan_id>` 전체 JSON.

## 함께 보기

- [취약점 데이터 (Trivy DB)](./vulnerability-data.md) — DB 라이프사이클과 트러블슈팅.
- [백업·복원](./backup-and-restore.md) — 백업 보존 + 복원 흐름.
- [디스크·health](./disk-and-health.md) — 디스크 임계 모델 + Health 대시보드.
- [Docker Compose - 스캔 용량](../installation/docker-compose.md#scan-capacity-sizing-and-scaling) — 슬롯 용량 계산식과 `worker-scan`/`worker-default` 확장.
