---
id: uat-checklist
title: 설치 UAT 체크리스트
description: 운영자 fresh-OS 검증 — Ubuntu 22.04 / Rocky Linux 9에서 설치 + 백업 + 호스트간 복원 라운드트립.
sidebar_label: 설치 UAT 체크리스트
sidebar_position: 5
---

# 설치 UAT 체크리스트

본 체크리스트는 `.github/workflows/install-uat.yml`의 **수동 대응본**입니다. CI
워크플로는 cron마다 래퍼 스크립트 로직을 검증하지만, 본 체크리스트는 운영자가
실제 고객 호스트에서 환경을 GA-ready로 선언하기 전에 직접 따라가는 절차입니다.
새 프로덕션 호스트를 띄울 때마다, 그리고 `scripts/install.sh`,
`scripts/backup.sh`, `scripts/restore.sh`, `scripts/upgrade.sh`를 변경할
때마다 다시 수행하세요.

:::note 대상 독자
clean Ubuntu 22.04 LTS 또는 Rocky Linux 9 호스트에 `sudo` 권한이 있는
플랫폼/SRE 엔지니어. `docker-compose`, `psql`, SSH에 익숙해야 합니다.
:::

## 0. 목표

- 인스톨 번들이 **이전에 손대지 않은 호스트**(잔여 상태/캐시 없음)에서
  동작하는지 검증합니다.
- 백업+복원 라운드트립이 호스트 교체 후에도 살아남는지 확인합니다(백업을
  뜬 호스트와 복원하는 호스트가 반드시 같지 않아도 됩니다).
- `docker-compose.yml`(프로덕션, Traefik fronted)과 `docker-compose.dev.yml`
  (CI가 사용하는 파일)의 drift를 노출시킵니다.

## 1. 호스트 준비

| 항목 | 권장 |
|------|------|
| OS   | Ubuntu 22.04 LTS 또는 Rocky Linux 9 |
| CPU  | 8 vCPU |
| RAM  | 16 GB |
| Disk | `/opt` 하위 100 GB 여유 |
| 네트워크 | GitHub, Docker Hub, 사내 레지스트리에 대한 outbound HTTPS |

Docker Engine + `docker-compose` **V1** 설치 (CLAUDE.md 핵심 규칙 #10이
V2 플러그인을 금지합니다):

```bash
# Docker Engine — 배포판별. https://docs.docker.com/engine/install/ 참고.
# Compose V1, 재현성을 위해 버전 고정:
sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version    # → docker-compose version 1.29.2
```

워크스페이스 디렉터리 생성:

```bash
sudo mkdir -p /opt/trustedoss/workspace /opt/trustedoss/backups
sudo chown -R "$USER":"$USER" /opt/trustedoss
```

## 2. `install.sh` 실행

저장소를 clone하고 마법사를 실행합니다. (a) 대화형 또는 (b) 비대화형 중
**하나**를 선택하세요. 후자는 CI가 실행하는 방식과 동일합니다.

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
```

### (a) 대화형

```bash
bash scripts/install.sh
```

마법사가 묻는 항목: 공개 URL, super-admin 이메일, super-admin 비밀번호
(두 번, 비표시). 합리적 기본값을 제공합니다.

### (b) 비대화형

```bash
INSTALL_HOST=http://portal.example.com \
INSTALL_ADMIN_EMAIL=admin@example.com \
INSTALL_ADMIN_PASSWORD='ReplaceWithStrongPassphrase!' \
bash scripts/install.sh --no-prompt
```

`INSTALL_ADMIN_PASSWORD`를 비우면 스크립트가 임의 비밀번호를 생성하여
stdout에 한 번 출력합니다 — 터미널이 스크롤되기 전에 캡처하세요. 첫 로그인
시 즉시 교체합니다.

### 기대 결과

```text
✓ docker-compose found: docker-compose version 1.29.2
✓ openssl found
✓ curl found
✓ wrote .env from .env.example
✓ generated SECRET_KEY (64 hex chars) and Postgres password
✓ wrote CORS_ALLOWED_ORIGINS=… + DOMAIN to .env
✓ containers started
✓ backend is healthy
✓ schema is at HEAD
✓ super admin account ready

Installation complete
✓ TrustedOSS Portal is running at: http://portal.example.com
```

`docker-compose -f docker-compose.yml ps`의 모든 행이 `Up (healthy)`이어야
합니다.

## 3. 첫 로그인 + 프로젝트 생성

1. 공개 URL을 브라우저로 열고 부트스트랩 super-admin으로 로그인.
2. Team 생성 (`/admin/teams` → **New team**).
3. 실제 Git URL로 프로젝트 생성 (`/projects/new`).
4. 스캔을 트리거. `/scans`에서 WebSocket 진행 피드가 작은 저장소 기준
   ~5분 안에 **Completed**로 이동해야 합니다.

위 4단계 중 하나라도 실패하면 **여기서 멈추세요**. 호스트가 UAT-clean이
아니므로, 지금 백업을 떠도 실패가 그대로 따라옵니다.

## 4. 백업 수행

```bash
bash scripts/backup.sh
```

기대:

```text
Backup → backups/2026-05-09-143022
✓ wrote backups/2026-05-09-143022/postgres.sql.gz (12K)
✓ wrote backups/2026-05-09-143022/workspace.tar.gz (3.2M)
✓ wrote backups/2026-05-09-143022/manifest.json (alembic head = abcd1234)
Backup complete
  backups/2026-05-09-143022
```

`manifest.json`이 기대한 Alembic head를 담고 있는지 확인:

```bash
cat backups/2026-05-09-143022/manifest.json
```

## 5. 호스트간 복원

가장 어려운 UAT 단계이자 가장 자주 건너뛰어지는 단계입니다. 동일한
**두 번째** VM(`vm-b`)을 띄우고, 1+2단계(Docker + `docker-compose` V1
설치)만 반복합니다. **아직** `install.sh`는 실행하지 마세요.

백업 전송:

```bash
# vm-a 에서
scp -r backups/2026-05-09-143022 vm-b:/tmp/backups/
```

`vm-b` 에서:

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
bash scripts/install.sh --no-prompt
mkdir -p backups
mv /tmp/backups/2026-05-09-143022 backups/
bash scripts/restore.sh backups/2026-05-09-143022
```

`restore.sh`가 destructive-action 프롬프트를 출력하면 **y**를 입력. 자동화
시 `--confirm` argv flag 전달: `bash scripts/restore.sh --confirm <backup-dir>`.
(legacy `BACKUP_RESTORE_CONFIRM=yes` env var 는 marathon bundle 4 에서 제거 — argv flag 는
`ps` 출력에 보이지만 env var 는 보이지 않음.)

**기대**: `restore.sh`가 `✓ alembic head matches manifest`로 종료.
`vm-b`의 포털에 로그인하면 `vm-a`의 프로젝트/스캔/사용자가 그대로 보여야
합니다.

## 6. PG 버전간 마이그레이션 (선택)

Postgres 메이저 업그레이드를 계획할 때만 수행:

1. `vm-a` (Postgres 16) 에서: `bash scripts/backup.sh`.
2. `vm-b` (Postgres 17 — 본 릴리스 기본): install.sh 실행 **전에**
   `docker-compose.yml`을 17 이미지로 편집.
3. §5를 그대로 따릅니다. 복원 단계가 덤프를 `psql`로 흘려보내며,
   이는 한 메이저 버전 차이까지는 forward-compatible입니다.

## 7. 정리

```bash
docker-compose -f docker-compose.yml down -v
sudo rm -rf /opt/trustedoss/workspace
```

컨테이너, 볼륨, 워크스페이스 디렉터리를 제거합니다. `backups/` 트리는
유지되니, 보존이 필요하면 호스트 외부로 백업하세요.

## 8. 결과 보고

GitHub 이슈에 다음을 첨부:

- OS + 버전 (`cat /etc/os-release`)
- Docker 버전 (`docker version --format '{{.Server.Version}}'`)
- Compose 버전 (`docker-compose --version`)
- TrustedOSS Portal 커밋 SHA (`git rev-parse HEAD`)
- 어떤 섹션이 통과/실패했는지
- 실패한 경우 `docker-compose -f docker-compose.yml logs --tail=300` 출력 첨부

## 문제 해결

### `install.sh`가 "backend is healthy"에서 실패

대기 윈도우에 비해 Postgres 덤프가 너무 크거나, Alembic이 긴 마이그레이션에
걸려 있을 수 있습니다. 스크립트 실행 중 로그를 tail:

```bash
docker-compose -f docker-compose.yml logs -f backend
```

### `restore.sh`가 confirm 프롬프트에서 종료

`y` / `Y` 외의 입력을 했습니다. 재실행하세요. 스크립트 자동화 시
`--confirm` argv flag 를 전달 (`bash scripts/restore.sh --confirm <dir>`).

### 호스트간 복원이 `alembic head mismatch`로 실패

대상 트리가 원본과 다른 코드 리비전입니다. `vm-b`를 같은 커밋으로 업그레이드
하거나, 복원 후 `vm-b`에서 `alembic upgrade head`를 실행하세요. 스크립트가
정확한 복구 명령을 출력합니다.

## 함께 보기

- [Docker Compose 설치](./docker-compose.md) — 인스톨 번들의 기준 문서.
- [백업·복원](../admin-guide/backup-and-restore.md) — 관리 UI 흐름 + 스케줄링.
- [업그레이드](./upgrade.md) — `scripts/upgrade.sh` 사용법.
- `.github/workflows/install-uat.yml` — 본 체크리스트의 CI 대응본
  (cron마다 §2 + §4 + §5를 실행).
