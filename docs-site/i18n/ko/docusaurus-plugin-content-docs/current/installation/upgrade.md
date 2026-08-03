---
id: upgrade
title: 업그레이드
description: 번들 래퍼 스크립트로 기존 TRUSCA 설치를 업그레이드 — 백업, 이미지 풀, alembic 마이그레이션, health 프로브.
sidebar_label: 업그레이드
sidebar_position: 2
---

# 업그레이드

`scripts/upgrade.sh` 래퍼는 동작 중인 설치를 **in-place**로 업그레이드합니다. 어떠한 작업 전에도 사전 백업을 먼저 수행하며, `docker-compose pull` + `up -d` 흐름을 사용해 이미지 해시가 변경된 서비스만 재생성합니다.

:::note 대상 독자
포털 호스트의 `sudo` 권한 운영자. `docker-compose ps`/`logs`에 익숙해야 합니다.
:::

## 호환성 및 정책

- **Forward-only Alembic 마이그레이션.** 본 포털은 `alembic downgrade`를 지원하지 않습니다. 되돌리려면 사전 백업을 복원하세요([롤백](#롤백)).
- **마이너·패치 업그레이드**는 동일 메이저 버전 내에서 항상 in-place 지원됩니다. **메이저 업그레이드**(예: 2.x → 3.x)는 별도 릴리스 노트로 안내되며 `scripts/upgrade.sh`를 무작정 사용하면 안 됩니다.
- **다운타임 예상**: 이미지가 변경된 서비스가 재생성되는 동안 포털이 잠시 중단됩니다. 일반적으로 30초 이내.

## 사전 요구사항

- 직전 정상 설치 상태 (즉, `docker-compose -f docker-compose.yml ps` 출력에서 모든 서비스가 healthy).
- PATH의 `docker-compose` (V1).
- 새 이미지 레이어와 사전 백업을 위해 5 GB 이상 여유 디스크.
- 의도한 `IMAGE_TAG`가 `.env`에 명시 (혹은 마법사 기본값 `0.11.0` 사용). 사설 레지스트리를 운영한다면 `IMAGE_TAG`는 그곳에 게시된 매니페스트와 일치해야 합니다.

## 1단계 — 업그레이드 시점 점검

조용한 시점을 선택합니다(진행 중인 스캔 없는 시점). 대시보드 `/scans`에서 전역 큐가 모두 비기를 기다리세요.

```bash
docker-compose -f docker-compose.yml ps
```

모든 행이 `Up (healthy)`여야 합니다. 재시작 중이거나 unhealthy인 서비스가 있으면 먼저 그것을 해결하세요 — 깨진 설치 위에 업그레이드를 쌓지 마세요.

## 2단계 — 업그레이드 래퍼 실행

```bash
bash scripts/upgrade.sh
```

흐름:

1. **사전 백업** — `bash scripts/backup.sh` (필수, 건너뛰는 플래그 없음).
2. **`docker-compose pull`** — 새 이미지 가져오기.
3. **`docker-compose up -d`** — 이미지 해시가 변경된 서비스만 재생성.
4. **`alembic upgrade head`** — 새로운 마이그레이션 적용.
5. **Health 프로브** — `/health`를 최대 60초간 폴링.

성공 시 출력:

```text
✓ backend is healthy
Upgrade complete
  If something looks off, restore the pre-upgrade backup:
  bash scripts/restore.sh $(ls -td backups/* | head -1)
```

## 3단계 — 업그레이드 검증

1. 포털에 로그인.
2. **/admin/health** 방문 — 모든 컴포넌트가 녹색이어야 합니다.
3. 알려진 프로젝트(혹은 가장 최근 스캔된 프로젝트)에 작은 스캔을 실행. WebSocket 진행률이 **Completed**까지 이동하는지 확인.
4. 릴리스 노트가 새 admin 화면이나 설정을 안내한다면 한 번씩 점검합니다.

## 롤백

업그레이드 후 포털이 망가졌다면 사전 백업을 복원합니다.

```bash
bash scripts/restore.sh "$(ls -td backups/* | head -1)"
```

`scripts/restore.sh` 동작:

1. 파괴적 작업을 대화형으로 확인 (**y** 입력).
2. 애플리케이션 컨테이너(`backend`, `frontend`, `worker`, `beat`) 중지. PostgreSQL과 Redis는 유지.
3. PostgreSQL 덤프(`postgres.sql.gz`) 복원.
4. workspace tar(`workspace.tar.gz`)이 있다면 복원.
5. 애플리케이션 컨테이너 재시작.
6. Alembic head가 백업의 `manifest.json`과 일치하는지 검증.

`manifest.json`이 없거나 head가 일치하지 않으면 경고를 출력하며 `alembic upgrade head`를 수동 실행해야 합니다.

:::warning 데이터 손실
`restore.sh`는 라이브 데이터베이스 내용과 `WORKSPACE_HOST_PATH` 디렉터리를 **교체**합니다. 되돌릴 수 없습니다. 가리키는 백업이 올바른지(`ls -td backups/*`는 최신 순) 반드시 확인하세요.
:::

## 버전 점프

각 중간 버전이 forward-only 마이그레이션 경로를 가지는 한, 한 번의 `upgrade.sh` 실행으로 여러 버전 점프가 가능합니다. 마이그레이션 체인은 CI에서 end-to-end 검증되므로 한 번에 2.0.0 → 2.0.5는 지원됩니다.

메이저 점프는 래퍼 호출 전에 릴리스 노트의 "Migration steps" 섹션을 따르세요.

## 흔한 문제

### `alembic upgrade head`가 제약 조건 위반으로 실패

대개 실제 데이터 문제입니다 — 기본값보다 앞선 행이 있는데 NOT NULL 컬럼이 추가된 경우 등. 백업을 복원하고 문제 행을 분석한 후 이슈 트래커에 마이그레이션 문제를 보고하세요.

### Health 프로브가 60초 후 타임아웃

풀이 더 긴 워밍업이 필요한 서비스를 다운시켰을 수 있습니다(예: JRE 업그레이드 후 워커). 로그를 확인하세요.

```bash
docker-compose -f docker-compose.yml logs --tail=200 backend worker
```

backend는 떠 있는데 worker가 떠 있지 않으면 스캔이 큐에 쌓이기만 합니다. 워커를 수동으로 재시작:

```bash
docker-compose -f docker-compose.yml restart worker beat
```

### 이미지 풀 거부 (403 / 401)

레지스트리 자격증명이 만료되었습니다. 재인증:

```bash
docker login <your-registry>
```

이후 `bash scripts/upgrade.sh`를 다시 실행합니다.

## 함께 보기

- [백업·복원](../admin-guide/backup-and-restore.md)
- [시스템 health 대시보드](../admin-guide/disk-and-health.md)
- [릴리스 노트](https://github.com/trustedoss/trusca/releases)
