---
id: docker-compose
title: Docker Compose 설치
description: docker-compose V1과 번들 설치 마법사로 Linux 호스트에 TRUSCA를 설치하는 단계별 안내.
sidebar_label: Docker Compose
sidebar_position: 1
---

# Docker Compose 설치

자체 호스팅 환경에 권장하는 설치 경로입니다. `scripts/install.sh` 마법사가 이미지 풀, 비밀값 생성, 첫 `super_admin` 사용자 생성을 일괄 수행합니다. Alembic 마이그레이션은 backend 컨테이너가 기동 시 자동 적용하므로(`AUTO_MIGRATE`, 기본 `true`), 아래 두 경로 모두 수동 `alembic upgrade head` 가 필요 없습니다. Docker 캐시가 따뜻한 상태라면 보통 10분 이내에 끝납니다.

:::note 대상 독자
Linux 호스트에서 `sudo` 권한을 가진 운영자. `docker-compose`와 기본 셸 사용에 익숙해야 합니다. 최종 사용자 대상은 아닙니다 — 설치 완료 후 URL을 안내하세요.
:::

## 사전 요구사항

- **Linux 호스트** (Ubuntu 22.04 LTS, Debian 12, RHEL 9에서 검증). macOS는 개발용으로만 작동하며 프로덕션 대상이 아닙니다.
- **Docker Compose.** `docker-compose`(V1, 하이픈)가 프로젝트 표준이며, `install.sh` 마법사는 이를 우선 사용하되 V1이 없으면 **`docker compose`(V2) 플러그인으로 폴백**합니다 — 따라서 최신 호스트에서도 그대로 동작합니다. [V1/V2 안내](#왜-docker-compose-v1-인가) 참고.
- **`openssl`** — SECRET_KEY와 데이터베이스 비밀번호 생성에 사용.
- **`curl`** — 설치 후 health 프로브(및 위의 클론 없는 빠른 설치)에 사용.
- **외부 HTTPS 접근** — 포털 이미지 **및** Trivy DB가 게시되는 GitHub Container Registry(`ghcr.io`)에 도달 가능해야 합니다. air-gapped 운영은 Trivy DB를 사내 OCI 레지스트리에 미러링 — [취약점 데이터 — Air-gapped 운영](../admin-guide/vulnerability-data.md#air-gapped) 참조.
- **디스크**: 이미지·workspace 마운트·최소 7일치 백업을 위해 20 GB 이상 여유.
- **CPU/RAM**: 최소 4 vCPU / 8 GB RAM. 실제 소스 스캔(cdxgen + scancode)은 워커에서 ~6 GB까지 사용하므로 여유를 확보해 두세요.

환경 검증:

```bash
docker-compose --version           # Compose 1.x (권장)
# …V2 플러그인만 있다면 마법사가 다음으로 폴백합니다:
docker compose version             # Compose v2.x
openssl version
curl --version
df -h /                            # 20 GB 이상 여유
```

## 평가용 설치 (dev 스택)

프로덕션 호스트를 준비하기 전에 TRUSCA를 *체험*하고 싶으신가요?
**dev 스택**(`docker-compose.dev.yml`)은 클론에서 포털을 띄우고 현실적인 데모
데이터를 시드합니다. 클론·마이그레이션·`up`·시드·로그인 계정까지의 절차는
[Quickstart](../quickstart.md)가 단일 기준입니다 — **2 vCPU / 4 GB RAM**
호스트에서 약 5분이 걸리고 [실제 저장소 첫
스캔](../quickstart.md#first-real-scan)으로 끝납니다. 본 페이지는 Quickstart가
다루지 않는 것만 담습니다: 프로덕션 사양, TLS, 그리고 아래의 설치 마법사.

:::note 언제 사용하나
노트북, 일회용 클라우드 VM, 또는 **2 vCPU / 4 GB RAM** 호스트. 실제 배포에는
[설치 마법사](#2단계--설치-마법사-실행)를 사용하세요 — dev 스택은 프로덕션
하드닝(TLS, 역할 분리, 6 GB 스캔 워커)을 첫 체험의 낮은 마찰과 맞바꿉니다. 공개
인터넷에 노출하지 마세요.
:::

### 취약점이 보이는 경로

시드된 데모 데이터셋은 finding을 직접 제공하며, 워커는 인터넷 egress가 있으면
첫 부팅에 Trivy DB를 다운로드합니다. 호스트는 외부 취약점 엔진이 필요 없습니다 —
Trivy와 DB는 모두 워커 컨테이너 내부에 존재합니다.

air-gapped 평가(`ghcr.io` egress 없음)는 [취약점 데이터 — Air-gapped 운영](../admin-guide/vulnerability-data.md#air-gapped) 참조.

:::warning dev 스택 ≠ 프로덕션
실제 소스 스캔(cdxgen + scancode)은 워커에서 ~6 GB까지 치솟습니다. dev 스택은
**시드된 데이터 탐색**용이지 프로덕션 스캔용이 아닙니다. 작은 스캔은 가능하지만
큰 레포에서는 어렵습니다. dev 스택은 TLS와 L1 DB 역할 분리도 생략하므로 공개
인터넷에 노출하지 마세요. 첫 체험 이상의 용도에는
[설치 마법사](#2단계--설치-마법사-실행)를 사용하세요.
:::

다 사용한 뒤의 정리는 [Quickstart — 스택 종료](../quickstart.md#스택-종료)를
참고하세요.

## HTTPS 배포의 사전 요구사항

마법사를 실행하기 전에 호스트가 다음 세 가지 조건을 만족하는지 확인하세요.
마법사는 이를 검증하지 않으며, 하나라도 누락되면 Traefik이 조용히 실패합니다.

- **DNS**: 사용할 도메인(예: `oss.acme.com`)의 `A` 레코드(또는 `CNAME`)가
  호스트의 공개 IP를 가리켜야 합니다. `dig +short oss.acme.com` 으로 확인합니다.
- **방화벽**: `80`번과 `443`번 포트가 공개 인터넷에서 도달 가능해야 합니다.
  Traefik은 `:80`에서 HTTP-01 챌린지를 사용해 Let's Encrypt 인증서를 발급하며,
  성공 후에는 모든 트래픽을 `:443`으로 리다이렉트합니다. UFW · 클라우드 제공자
  방화벽 · 보안 그룹 모두 두 포트가 열려 있어야 합니다.
- **TLS_EMAIL**: 마법사는 공개 URL이 `https://...` 일 때 이 값을 수집합니다.
  Let's Encrypt 가 만료 경고와 레이트 리밋 상승을 이 주소로 보내므로,
  실제로 확인하는 메일함을 사용하세요.

HTTP-only / `localhost` 설치(개발, 망분리 UAT)에는 위 셋이 모두 적용되지
않습니다 — 마법사는 TLS_EMAIL을 건너뛰고 Traefik은 ACME 흐름에 진입하지
않습니다.

## 빠른 설치 (클론 없이)

스택을 바로 띄우기만 하면 되고 보조 스크립트가 필요 없다면, 레포를 클론하지 않고 게시된 이미지로 곧장 설치할 수 있습니다 — 단일 파일 설치 경험입니다. 프로덕션 이미지는 GitHub Container Registry(`ghcr.io/trustedoss/trusca-backend`, `…/trusca-backend-worker`, `…/trusca-frontend`)에 게시되며 익명 pull이 가능합니다.

compose 스택에 필요한 세 파일(compose 파일, env 템플릿, 1회용 Postgres 역할 초기화 스크립트)을 받고 `.env`를 편집한 뒤 기동합니다:

```bash
mkdir -p trustedoss && cd trustedoss
BASE=https://raw.githubusercontent.com/trustedoss/trusca/v0.10.0

# 1. 자기완결적 프로덕션 compose 파일(`build:` 섹션 없음 — ghcr.io에서 이미지 pull)
#    과 env 템플릿.
curl -fsSLO "$BASE/docker-compose.yml"
curl -fsSL  "$BASE/.env.example" -o .env

# 2. compose 파일은 첫 부팅 역할 프로비저닝을 위해 레포 파일 하나를 Postgres에
#    마운트합니다. compose가 기대하는 경로로 받습니다.
mkdir -p scripts
curl -fsSL "$BASE/scripts/postgres-init.sh" -o scripts/postgres-init.sh
chmod +x scripts/postgres-init.sh

# 3. .env 편집 — 최소한 SECRET_KEY(openssl rand -hex 32), 강력한
#    POSTGRES_PASSWORD / POSTGRES_APP_PASSWORD, DOMAIN, TLS_EMAIL,
#    CORS_ALLOWED_ORIGINS=https://<도메인> 을 설정. 원하는 릴리스로 IMAGE_TAG
#    고정(기본 2.0.0).
$EDITOR .env

# 4. pull 후 기동.
docker-compose -f docker-compose.yml pull
docker-compose -f docker-compose.yml up -d
```

게시된 backend 이미지의 entrypoint는 **기동 시 Alembic 마이그레이션을 자동 적용**(`AUTO_MIGRATE`, 기본 `true`)한 뒤 uvicorn을 시작합니다 — backend가 healthy로 보고될 때 스키마는 이미 HEAD입니다. 수동 `alembic upgrade head` 는 필요 없습니다. 다만 자동 마이그레이션은 사용자를 생성하지 않으므로, 첫 관리자는 한 번 부트스트랩합니다:

```bash
# 비밀번호를 화면에 노출하지 않고 셸 변수로 읽은 뒤, `-e` 에는 변수 "이름만"
# 넘깁니다. 값은 호출 셸에서 상속되므로 argv(`ps -ef` 노출)나 셸 히스토리에
# 남지 않습니다.
read -rs ADMIN_PASSWORD; export ADMIN_PASSWORD   # 12자 이상 비밀번호 입력 후 Enter

# 첫 super_admin 생성 (스키마는 이미 HEAD).
docker-compose -f docker-compose.yml exec -T \
  -e ADMIN_EMAIL=you@example.com \
  -e ADMIN_PASSWORD \
  backend python -m scripts.create_super_admin

unset ADMIN_PASSWORD   # 사용자 생성 후 셸에서 제거
```

:::warning 비밀번호를 인라인으로 넣지 마세요
`-e ADMIN_PASSWORD='리터럴'` 은 피하세요: 명령 실행 중 `ps -ef` 를 실행하는
모든 사용자에게 리터럴이 노출되고 셸 히스토리에도 기록됩니다. 이름만
넘기면(`-e ADMIN_PASSWORD`) Docker 가 환경에서 값을 상속합니다.
:::

:::note 스키마를 외부에서 관리하는 경우
단일 역할 `.env` 템플릿은 `AUTO_MIGRATE=true` 로 출하되며 그대로 동작합니다. **L1 역할 분리** 스택(DDL용 `DATABASE_URL_OWNER` 와 런타임용 `DATABASE_URL_APP` 분리)에서는 런타임 컨테이너가 DML 전용 app DSN만 보유해 DDL을 실행할 수 없으므로 자동 마이그레이션을 꺼야 합니다.

- **마법사 사용 시(2단계):** `install.sh` 가 **L1을 감지**(`DATABASE_URL_OWNER` 가 설정돼 있고 런타임 DSN과 다름)하면 **`.env` 에 `AUTO_MIGRATE=false` 를 자동 기록**한 뒤 owner 역할로 직접 마이그레이션합니다. 별도로 설정할 필요가 없습니다.
- **이 클론 없는 경로:** 마법사가 없으므로 L1 스택에서는 **운영자가 직접 `.env` 에 `AUTO_MIGRATE=false` 를 설정**하고 owner 역할로 `alembic upgrade head` 를 실행해야 합니다(그 한 명령에서 `DATABASE_URL` 을 `DATABASE_URL_OWNER` 로 덮어씀). L1 스택에서 `true` 로 두면 backend entrypoint 가 명확한 DDL 권한 오류와 함께 즉시 실패(exit 1, 크래시 루프 없음)하고 로그에 원인을 남깁니다.
:::

### Liveness vs. readiness: 스택이 스키마를 기다리는 방식

backend 는 인증이 필요 없는 health 엔드포인트를 **두 개** 노출합니다. 둘은 서로 다른 질문에 답하며, Compose / Kubernetes 의 기동 게이트가 이 구분에 의존합니다.

| 엔드포인트 | 답하는 질문 | DB 접근? | 사용처 |
| --- | --- | --- | --- |
| `GET /health` | uvicorn **프로세스**가 떠서 요청을 받는가? (순수 liveness) | 아니오 | Kubernetes `livenessProbe`; liveness 전용 소비자 |
| `GET /health/ready` | Postgres **스키마가 Alembic HEAD** 인가, 즉 트래픽을 처리하고 워커를 띄워도 안전한가? (readiness) | 예 (`alembic_version` 에 대한 읽기 전용 `SELECT`) | Compose backend `healthcheck`; Kubernetes `readinessProbe` |

`/health/ready` 는 스키마가 HEAD 와 일치할 때만 `200 {"status":"ready"}` 를 반환합니다. 그렇지 않으면 RFC 7807 `application/problem+json` 본문으로 리비전 불일치를 요약한 `503` 을 반환합니다(DSN 이나 자격 증명은 절대 노출하지 않습니다).

(Track B)부터 `backend` 서비스의 Compose `healthcheck` 가 **`/health/ready`** 를 검사하므로, `depends_on: backend (condition: service_healthy)` 를 선언한 `worker` / `beat` 서비스는 **스키마가 마이그레이션된 뒤에야** 기동합니다. 두 토글 모두에서:

- **`AUTO_MIGRATE=true`**(단일 역할 기본값): backend 컨테이너가 기동 시 `alembic upgrade head` 를 실행하고, 완료되면 `/health/ready` 가 `200` 으로 바뀝니다. 그 후 워커가 마이그레이션된 스키마 위에서 시작합니다. 일반 경로이며 운영자 조치가 필요 없습니다.
- **`AUTO_MIGRATE=false`**(L1 역할 분리 스택): uvicorn 은 `/health` 에 즉시 응답하지만, **외부**에서 owner 역할로 `alembic upgrade head`(`install.sh` / `upgrade.sh` 가 수행)를 실행해 스키마를 HEAD 로 올릴 때까지 `/health/ready` 는 `503` 으로 남습니다(컨테이너는 `health: starting` 유지). **이는 의도된 동작입니다.** 워커와 beat 는 아직 마이그레이션되지 않은 DB 위에서 시작하는 대신 스키마를 기다립니다. L1 스택에서 마이그레이션을 깜빡하면 backend 가 영영 healthy 가 되지 않으니 `docker-compose logs backend` 를 확인하고 owner 역할 마이그레이션을 실행하세요.

:::note 긴 마이그레이션이 컨테이너를 `unhealthy` 로 만들지 않는 이유
backend healthcheck 는 넉넉한 `start_period`(60s)를 사용합니다. 큰 DB 의 첫 마이그레이션은 `/health/ready` 가 `200` 이 되기 전까지 한동안 걸릴 수 있는데, `start_period` 가 그 첫 마이그레이션 완료 전에 Docker 가 컨테이너를 `unhealthy` 로 표시(및 재시작)하지 않도록 막아 줍니다.
:::

:::tip 가이드 설치는 마법사를 권장
아래 1~3단계의 `install.sh` 마법사는 위 작업을 대신 처리합니다 — 비밀값 생성, health 대기 루프, 마이그레이션, 관리자 부트스트랩까지. 또한 호스트에 V1이 없으면 Compose **V2** 플러그인(`docker compose`)으로도 동작합니다. 각 단계를 직접 제어하거나 자체 자동화를 구성할 때 클론 없는 경로를 사용하세요.
:::

## 1단계 — 레포 클론

```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca
```

포크를 운영한다면 포크 레포를 클론하세요. 재현 가능한 설치를 위해 릴리스 태그로 체크아웃합니다.

```bash
git checkout v0.10.0
```

## 2단계 — 설치 마법사 실행

```bash
bash scripts/install.sh
```

마법사 동작 순서:

1. `docker-compose`, `openssl`, `curl`이 PATH에 있는지 확인.
2. `.env`가 없으면 `.env.example`을 복사 (있다면 백업 후 교체 옵션).
3. 64-hex `SECRET_KEY`와 강력한 PostgreSQL 비밀번호 생성.
4. 포털이 노출될 **공개 URL**을 입력받아 `.env`에 `CORS_ALLOWED_ORIGINS`와 `DOMAIN`을 기록.
5. **마이그레이션 정책 결정**: L1 역할 분리 스택(`DATABASE_URL_OWNER` 가 설정돼 있고 런타임 DSN과 다름)을 감지하면 `.env` 에 `AUTO_MIGRATE=false` 를 기록해 런타임 컨테이너가 app 역할로 DDL을 시도하지 않게 합니다. 단일 역할 스택은 기본값 `true` 유지.
6. `docker-compose pull` — 고정된 이미지 풀.
7. `docker-compose up -d` — 스택 기동. 단일 역할 스택에서는 backend 컨테이너가 기동 시 Alembic 마이그레이션을 자동 적용(`AUTO_MIGRATE=true`); L1에서는 적용하지 않습니다(앞 단계에서 정책 설정).
8. 백엔드 `/health`가 200 응답할 때까지 60초 폴링.
9. **owner** 역할(`DATABASE_URL_OWNER`)로 `alembic upgrade head` 를 한 번 실행합니다. L1에서는 권위 있는 DDL 패스(런타임 컨테이너는 DML 전용 app DSN만 보유); 단일 역할 스택에서 entrypoint 가 이미 마이그레이션한 경우 멱등 재확인입니다 — 이미 적용된 리비전은 건너뜁니다.
10. 첫 super-admin 이메일과 비밀번호(12자 이상, 확인 입력) 입력. 자동 마이그레이션은 사용자를 만들지 않으므로 이 단계는 항상 실행됩니다.
11. 최종 URL과 다음 단계 안내 출력.

### 정상 종료 시 출력

```
Installation complete
✓ TRUSCA is running at: https://trustedoss.example.com
  Login:           you@example.com
  Admin panel:     https://trustedoss.example.com/admin
  API docs:        https://trustedoss.example.com/api/docs
```

## 3단계 — 로그인 및 검증

1. 마법사가 출력한 URL을 엽니다.
2. super-admin 자격증명으로 로그인.
3. **/admin/health** 방문 — backend·postgres·redis·worker·beat 모두 **녹색**이어야 합니다. 워커는 첫 부팅에 Trivy DB를 다운로드(1~3분)하며, 다운로드 완료 시 Vulnerability data 카드가 녹색으로 전환됩니다.

Trivy DB 운영(갱신 주기, air-gapped 미러, 트러블슈팅)은 [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) 참조.

## 4단계 — 백업 스케줄링

프로덕션에서 호스트 외부 백업은 선택이 아닙니다. cron 항목을 추가하세요.

```bash
sudo crontab -e
# m h dom mon dow command
0 3 * * *  cd /opt/trustedoss-portal && bash scripts/backup.sh >> /var/log/trustedoss-backup.log 2>&1
```

`scripts/backup.sh`는 `backups/<타임스탬프>/`에 `postgres.sql.gz`, `workspace.tar.gz`, `manifest.json`을 작성합니다. 7일 이상 지난 백업은 자동 정리됩니다(`.env`의 `BACKUP_RETENTION_DAYS`로 변경).

전체 복원 절차는 [백업·복원](../admin-guide/backup-and-restore.md)을 보세요.

## 종단 간 첫 성공 체크리스트 (30분)

`bash scripts/install.sh` 완료 후:

- [ ] `https://<your-host>` 열기 — 로그인 화면이 렌더링되고
  브라우저가 유효한 TLS 자물쇠를 표시(HTTPS 인 경우).
- [ ] 마법사가 출력한 super-admin 이메일·비밀번호로 로그인.
- [ ] 워커가 **첫 Trivy DB 다운로드**를 마치기를 대기 —
  `docker-compose -f docker-compose.yml logs --tail=100 worker | grep trivy_db`
  가 첫 부팅 후 1~3분 내에 `trivy_db_download_complete`를 보여줍니다.
  완료 전엔 새 스캔의 Vulnerabilities 탭이 비어 있습니다.
- [ ] `/admin/teams` → **New team** 으로 이동 → 이름을 `engineering`
  으로 설정.
- [ ] 동료에게 `/register` 에서 가입을 요청한 뒤,
  `/admin/users → <user> → Memberships → Add to team` 에서 추가.
- [ ] 동료 세션으로 전환 → `/projects → New project` 에서 작은
  공개 레포(테스트용)로 프로젝트 생성.
- [ ] 스캔을 트리거; 우측 슬라이드 진행 드로어가 약 2~5분 안에
  `bootstrap → fetch → prep → cdxgen → scancode →
  sbom_upload → vuln_match → finalize` 순서로 진행되어야 합니다. v0.10.0
  WebSocket 프레임은 호환성을 위해 과거 슬러그 `dt_upload`/`dt_findings`를
  유지하지만 화면 라벨은 새 이름으로 표시됩니다.
- [ ] 프로젝트의 **Vulnerabilities** 탭 열기 — 테스트 레포의 CVE 들이
  나열되어야 합니다.

어느 단계든 실패하면 `/docs/installation/troubleshooting` 과
Admin → Health 대시보드를 보세요.

## 트러블슈팅

### 80 또는 443 포트 사용 중

```text
Bind for 0.0.0.0:443 failed: port is already allocated
```

다른 프로세스가 포트를 점유 중입니다. 바인딩 목록을 확인하고 비웁니다.

```bash
sudo ss -tlnp | grep -E ':80|:443'
```

기존 리버스 프록시를 유지하려면 `docker-compose.yml`에서 Traefik 서비스를 제거하고 `/v1`, `/auth`, `/ws`, `/health`, `/health/ready`는 backend 컨테이너로, `/`는 frontend 컨테이너로 라우팅하도록 설정합니다.

### 백엔드가 healthy로 전환되지 않음

```text
✗ backend did not become healthy. Run: docker-compose -f docker-compose.yml logs backend
```

가장 흔한 원인:

- `DATABASE_URL`의 호스트가 컴포즈 네트워크에 없는 호스트입니다. 호스트 부분이 `postgres`(서비스명)인지 확인하세요. `localhost`나 `127.0.0.1` 금지.
- Postgres 컨테이너가 아직 healthy가 아닙니다. `docker-compose ps`에서 `postgres`가 `Up (healthy)`로 표시되어야 합니다. 재시작 중이라면 `docker-compose logs postgres`로 자격증명 불일치를 확인하세요.
- 자동 마이그레이션 실패. `AUTO_MIGRATE=true`(기본)일 때 backend는 기동 시 `alembic upgrade head` 를 실행하며, 재시도 루프 후에도 실패하면 비정상 종료하므로 컨테이너가 healthy로 전환되지 않습니다. `docker-compose logs backend` 의 alembic 트레이스백을 확인하세요. L1 역할 분리 스택에서는 런타임 DSN으로 DDL을 실행할 수 없으므로 `AUTO_MIGRATE=false` 로 설정하고 owner 역할로 마이그레이션을 실행하세요(마법사 2단계가 이를 처리).

### 설치 중 디스크 부족

cdxgen + scancode + Trivy의 Docker 레이어 캐시는 ~4 GB입니다. `/var/lib/docker`가 가득 차면 풀이 중단됩니다. 공간을 확보한 뒤 `docker-compose pull`과 `docker-compose up -d`를 다시 실행합니다.

### `.env`를 새로 시작하기

`.env`를 삭제(또는 이동)하고 마법사를 재실행합니다.

```bash
mv .env .env.backup
bash scripts/install.sh
```

마법사가 비밀값을 다시 생성합니다. **PostgreSQL의 데이터는 보존됩니다** — `.env`의 비밀값은 새 세션에만 영향을 주지만, `SECRET_KEY` 회전은 기존 모든 refresh 토큰을 무효화하여 모든 사용자의 재로그인을 강제합니다. 비밀값 수동 편집보다 이 방식이 권장됩니다.

## 제거

데이터를 보존한 채 스택만 중단:

```bash
docker-compose -f docker-compose.yml down
```

**데이터베이스와 workspace 포함 모든 것을 제거**:

```bash
docker-compose -f docker-compose.yml down -v
sudo rm -rf /opt/trustedoss/workspace
```

:::warning 데이터 손실
`docker-compose down -v`는 명명 볼륨(`postgres-data`, `redis-data`, `traefik-acme`, `workspace`)을 삭제합니다. 최근 백업 없이는 복구할 수 없습니다.
:::

## 메인테이너 안내 — 이미지 게시 (조직 1회 설정)

포털 이미지는 [`release.yml`](https://github.com/trustedoss/trusca/blob/main/.github/workflows/release.yml) 워크플로우가 GitHub Container Registry에 게시하며, `vX.Y.Z` git 태그 push(또는 **Run workflow**에 태그 입력)로 트리거됩니다. 이 워크플로우가 push하려면 **조직이 GitHub Actions의 패키지 쓰기를 허용**해야 합니다 — Org → Settings → Actions → Workflow permissions → *Read and write permissions* (또는 패키지의 *Manage Actions access*에서 해당 레포에 *Write* 부여). 워크플로우는 내장 `GITHUB_TOKEN`을 사용하며 별도 PAT는 필요 없습니다.

첫 push 이후 각 패키지 가시성을 **Public**(ghcr 패키지 → Package settings → Change visibility → Public)으로 바꿔 운영자가 익명으로 `docker pull` 할 수 있게 합니다 — 클론 없는 빠른 설치가 이에 의존합니다. 릴리스마다 불변 `X.Y.Z` 태그와 이동 가능한 `X.Y` 태그를 게시하며 `latest` 태그는 만들지 않습니다(CLAUDE.md 규칙 #9).

## 왜 docker-compose V1 인가

본 프로젝트의 **개발·CI** 환경은 Compose V1(`docker-compose`)을 표준으로 합니다 — V2 문법 차이가 내부 파이프라인에서 검증되지 않으며, dev/CI 영역에 `docker compose`(V2)를 도입한 PR은 리뷰에서 차단됩니다([`CLAUDE.md`](https://github.com/trustedoss/trusca/blob/main/CLAUDE.md) 규칙 #10).

이 제약은 내부 한정입니다. **최종 사용자 설치**에서는 `install.sh` 마법사가 V1을 우선 사용하되, V1이 2023년 EOL을 맞은 최신 호스트에서도 동작하도록 V2 플러그인(`docker compose`)으로 폴백합니다. compose 파일 자체는 V1 파일 포맷을 쓰며 V2도 이를 읽습니다.

## 함께 보기

- [기존 설치 업그레이드](./upgrade.md)
- [환경변수 참고](../reference/env-variables.md)
- [아키텍처 개요](../reference/architecture.md)
