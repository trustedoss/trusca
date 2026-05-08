---
id: backup-and-restore
title: 백업과 복원
description: 자동 백업 스케줄링, 수동 스냅샷 생성, 알려진 정상 백업 디렉터리에서 복원합니다.
sidebar_label: 백업·복원
sidebar_position: 5
---

# 백업과 복원

포털은 두 스크립트를 제공합니다 — `scripts/backup.sh`와 `scripts/restore.sh`. PostgreSQL 데이터베이스와 workspace 볼륨이라는 두 상태 산출물을 다루며, 백업 시점의 Alembic head를 기록한 manifest를 함께 둡니다.

:::note 대상 독자
호스트 `sudo` 권한 보유 `super_admin`. `pg_dump`, `tar`, `cron`에 익숙해야 합니다.
:::

## 백업 내용

```
backups/2026-05-09-030000/
├── postgres.sql.gz     # pg_dump --clean --if-exists | gzip
├── workspace.tar.gz    # $WORKSPACE_HOST_PATH의 tar -czf
└── manifest.json       # 타임스탬프, alembic head, db 크기, workspace 경로
```

- **`postgres.sql.gz`** — `--clean --if-exists` 포함 전체 논리적 덤프. 재적용 시 객체를 drop+recreate한 후 데이터 재삽입.
- **`workspace.tar.gz`** — 워커에 `/workspace`로 마운트된 호스트 디렉터리. 프로젝트별 클론과 ORT 분석기 출력 포함.
- **`manifest.json`** — `timestamp`, `alembic_head`, `db_size`, `workspace_path`. 복원 스크립트가 라이브 상태와 `alembic_head`를 검증.

포털은 `.env`(비밀값 포함 — 별도 비밀 관리 도구로 보관)와 Traefik의 ACME 상태(Let's Encrypt가 몇 분 내 재발급)는 백업하지 **않습니다**.

## 수동 백업 실행

```bash
bash scripts/backup.sh
```

출력:

```text
Backup → backups/2026-05-09-030000

✓ wrote backups/2026-05-09-030000/postgres.sql.gz (12M)
✓ wrote backups/2026-05-09-030000/workspace.tar.gz (840M)
✓ wrote backups/2026-05-09-030000/manifest.json (alembic head = 9f1c8d2a3b4e)

Backup complete
  backups/2026-05-09-030000
```

스크립트는 종료 시점에 `BACKUP_RETENTION_DAYS`(기본 7)보다 오래된 백업을 정리합니다. `--no-prune`으로 정리 생략 가능.

## 자동 백업 스케줄링

`cron`이 가장 단순한 경로입니다.

```bash
sudo crontab -e
# Minute Hour DoM Month DoW Command
0 3 * * *  cd /opt/trustedoss-portal && bash scripts/backup.sh >> /var/log/trustedoss-backup.log 2>&1
```

호스트 로컬 시간 03:00에 매일 실행됩니다. 스택의 한가한 시간대로 시간을 조정하세요.

관리형 스케줄러(systemd 타이머)는 아래 [systemd 타이머 레시피](#systemd-타이머-레시피)를 보세요.

## 호스트 외부 저장

로컬 백업은 데이터베이스 손상을 보호하지만 호스트 손실은 보호하지 않습니다. 보존 정책의 일부로 백업을 호스트 외부로 이동하세요.

```bash
# 예: AWS S3 야간 동기화(backup.sh 실행 후)
aws s3 sync /opt/trustedoss-portal/backups/ \
  s3://acme-trustedoss-backups/ \
  --exclude "*" --include "*.sql.gz" --include "*.tar.gz" --include "manifest.json" \
  --storage-class STANDARD_IA
```

다른 대상도 동일: `rclone copy`(Backblaze B2, Wasabi, GCS), `rsync`(NFS / SSH), 기존 백업 에이전트.

## 백업에서 복원

```bash
bash scripts/restore.sh backups/2026-05-09-030000
```

확인 프롬프트:

```text
About to restore from backups/2026-05-09-030000
! This will:
!   - REPLACE the current database content
!   - REPLACE /opt/trustedoss/workspace (if workspace.tar.gz present)
Continue? [y/N]
```

**`y`** 입력으로 진행.

스크립트 동작:

1. `backend`, `frontend`, `worker`, `beat` 중지. Postgres + Redis 유지.
2. `postgres.sql.gz`를 라이브 데이터베이스로 복원(`pg_dump --clean`이 객체 drop 선행).
3. `workspace.tar.gz`를 `WORKSPACE_HOST_PATH`로 복원(기존 파일은 먼저 제거).
4. 애플리케이션 컨테이너 재시작.
5. 라이브 Alembic head가 `manifest.json`과 일치하는지 검증, 불일치 시 경고.

성공 시 출력:

```text
✓ database restored
✓ workspace restored
✓ application restarted
✓ alembic head matches manifest (9f1c8d2a3b4e)

Restore complete
```

## 재해 복구 런북

호스트 전체가 손실되면:

1. **동일 OS·커널·Docker 버전의 대체 호스트 프로비저닝**.
2. `bash scripts/install.sh`로 **포털 설치**. 가능하면 동일 공개 URL 사용(DNS 재포인팅).
3. 상태를 깔끔히 교체하기 위해 **스택 중지**:

   ```bash
   docker-compose -f docker-compose.yml stop backend frontend worker beat
   ```

4. 호스트 외부 저장소에서 **백업 복사**:

   ```bash
   aws s3 cp s3://acme-trustedoss-backups/backups/2026-05-09-030000 \
     /opt/trustedoss-portal/backups/2026-05-09-030000 --recursive
   ```

5. **복원**:

   ```bash
   bash scripts/restore.sh backups/2026-05-09-030000
   ```

6. 원래 super-admin으로 **로그인**. 프로젝트·스캔·감사 로그 검증.

S3에 백업이 있는 작은 설치라면 전체 DR(호스트 손실 → 복원된 포털)이 30분 내에 진행됩니다.

## Forward-only 마이그레이션과 복원

포털은 `alembic downgrade`를 지원하지 않습니다. **이전** 백업이 직접 소비할 수 없는 상태로 마이그레이션이 스키마를 둔 새 릴리스로 업그레이드한 경우, 복원 스크립트의 manifest 체크가 경고합니다.

```text
! alembic head mismatch. expected=9f1c8d2a3b4e current=ab12cd34ef56
! Run: docker-compose -f docker-compose.yml exec backend alembic upgrade head
```

해결: 복원된 데이터베이스는 **이전** head이고 현재 컨테이너 코드는 **새** head입니다. 두 옵션:

1. **코드를 롤백** — `.env`의 `IMAGE_TAG`를 백업을 만든 버전으로 변경 후 `docker-compose -f docker-compose.yml up -d`. 스키마와 코드가 일치합니다.
2. **Forward 마이그레이션 재적용** — 복원된 데이터베이스에서 `alembic upgrade head`. Forward-only 데이터 마이그레이션은 멱등이라 깔끔히 재실행되어야 합니다. **스테이징에서 먼저 테스트하세요.**

사고 복구 시 옵션 (1) 권장, 의도된 계획 단계에서만 옵션 (2) 권장.

## 암호화된 백업

덤프는 평문 SQL입니다. 저장 시 암호화:

```bash
bash scripts/backup.sh
gpg --symmetric --cipher-algo AES256 \
  backups/2026-05-09-030000/postgres.sql.gz
gpg --symmetric --cipher-algo AES256 \
  backups/2026-05-09-030000/workspace.tar.gz
shred -u backups/2026-05-09-030000/{postgres.sql.gz,workspace.tar.gz}
```

복원 시 `gpg --decrypt`를 먼저, 그 다음 표준 복원 흐름. 분기별로 복호화 경로를 테스트하세요.

## systemd 타이머 레시피 {#systemd-타이머-레시피}

cron 대신 systemd 타이머를 선호한다면:

```ini
# /etc/systemd/system/trustedoss-backup.service
[Unit]
Description=TrustedOSS Portal nightly backup

[Service]
Type=oneshot
WorkingDirectory=/opt/trustedoss-portal
ExecStart=/usr/bin/env bash scripts/backup.sh
StandardOutput=journal
StandardError=journal

# /etc/systemd/system/trustedoss-backup.timer
[Unit]
Description=TrustedOSS Portal nightly backup timer

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

활성화:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trustedoss-backup.timer
```

## 정상 동작 확인

백업 후:

1. `backups/` 아래 새 디렉터리에 예상한 세 파일이 존재.
2. `manifest.json`이 JSON으로 디코드되며 비어 있지 않은 `alembic_head`를 포함.
3. `gunzip -t backups/.../postgres.sql.gz` 성공(gzip 무결성 체크).

복원 후:

1. 백업 시점의 자격증명으로 포털에 깔끔히 로그인.
2. 프로젝트 수·스캔 수·감사 로그 행 수가 예상과 일치.
3. **/admin/health**가 모두 녹색.

## 트러블슈팅

### `pg_dump`가 권한 거부 오류

스크립트는 postgres 컨테이너 안에서 `pg_dump`를 실행합니다 — 호스트 권한 문제는 없어야 합니다. `.env`의 `POSTGRES_USER`가 라이브 사용자와 일치하는지 확인:

```bash
docker-compose -f docker-compose.yml exec postgres \
  psql -U postgres -c '\du'
```

### Workspace 단계에서 복원 중단

스크립트는 tar 추출 전에 `rm -rf "$WORKSPACE_HOST_PATH"`를 실행합니다. 디렉터리가 read-only 마운트이거나 다른 프로세스가 사용 중이면 rm이 실패합니다. 마운트를 풀고 재실행하세요.

### "alembic head mismatch" 경고

[Forward-only 마이그레이션과 복원](#forward-only-마이그레이션과-복원) 참고.

### 백업 스크립트가 빈 workspace tar로 조용히 성공

`tar`는 archive 동안 변경되는 파일을 건너뜁니다. workspace가 활발히 변하면 백업 전 워커를 중지하세요.

```bash
docker-compose -f docker-compose.yml stop worker
bash scripts/backup.sh
docker-compose -f docker-compose.yml start worker
```

이는 30초 스캔-일시 정지 윈도와 일관성 보장된 workspace tar를 교환합니다.

## 함께 보기

- [설치](../installation/docker-compose.md)
- [업그레이드](../installation/upgrade.md)
- [디스크·시스템 health](./disk-and-health.md)
