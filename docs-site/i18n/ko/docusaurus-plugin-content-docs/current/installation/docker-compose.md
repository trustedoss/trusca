---
id: docker-compose
title: Docker Compose 설치
description: docker-compose V1과 번들 설치 마법사로 Linux 호스트에 TrustedOSS Portal을 설치하는 단계별 안내.
sidebar_label: Docker Compose
sidebar_position: 1
---

# Docker Compose 설치

자체 호스팅 환경에 권장하는 설치 경로입니다. `scripts/install.sh` 마법사가 이미지 풀, 비밀값 생성, Alembic 마이그레이션 실행, 첫 `super_admin` 사용자 생성을 일괄 수행합니다. Docker 캐시가 따뜻한 상태라면 보통 10분 이내에 끝납니다.

:::note 대상 독자
Linux 호스트에서 `sudo` 권한을 가진 운영자. `docker-compose`와 기본 셸 사용에 익숙해야 합니다. 최종 사용자 대상은 아닙니다 — 설치 완료 후 URL을 안내하세요.
:::

## 사전 요구사항

- **Linux 호스트** (Ubuntu 22.04 LTS, Debian 12, RHEL 9에서 검증). macOS는 개발용으로만 작동하며 프로덕션 대상이 아닙니다.
- **`docker-compose` (V1, 하이픈).** Compose V2(`docker compose`)는 미지원입니다 — [이유](#왜-docker-compose-v1-인가).
- **`openssl`** — SECRET_KEY와 데이터베이스 비밀번호 생성에 사용.
- **`curl`** — 설치 후 health 프로브에 사용.
- **외부 HTTPS 접근** — Docker Hub(또는 미러 레지스트리)와 Dependency-Track을 번들로 띄울 경우 OSV/NVD 피드 도달 가능해야 합니다.
- **디스크**: 이미지·workspace 마운트·최소 7일치 백업을 위해 20 GB 이상 여유.
- **CPU/RAM**: 최소 4 vCPU / 8 GB RAM. 실제 ORT 스캔은 워커에서 ~6 GB까지 사용하므로 여유를 확보해 두세요.

환경 검증:

```bash
docker-compose --version           # Compose 1.x여야 합니다 — V2 아님
openssl version
curl --version
df -h /                            # 20 GB 이상 여유
```

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

## 1단계 — 레포 클론

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
```

포크를 운영한다면 포크 레포를 클론하세요. 재현 가능한 설치를 위해 릴리스 태그로 체크아웃합니다.

```bash
git checkout v2.0.0
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
5. `docker-compose pull` — 고정된 이미지 풀.
6. `docker-compose up -d` — 스택 기동.
7. 백엔드 `/health`가 200 응답할 때까지 60초 폴링.
8. `alembic upgrade head` — 스키마 최신화.
9. 첫 super-admin 이메일과 비밀번호(12자 이상, 확인 입력) 입력.
10. 최종 URL과 다음 단계 안내 출력.

### 정상 종료 시 출력

```
Installation complete
✓ TrustedOSS Portal is running at: https://trustedoss.example.com
  Login:           you@example.com
  Admin panel:     https://trustedoss.example.com/admin
  API docs:        https://trustedoss.example.com/api/docs
```

## 3단계 — 로그인 및 검증

1. 마법사가 출력한 URL을 엽니다.
2. super-admin 자격증명으로 로그인.
3. **/admin/health** 방문 — backend·postgres·redis·worker·beat 모두 **녹색**이어야 합니다. `dt` 행은 **OPEN**으로 표시되며 이는 Dependency-Track을 아직 연결하지 않았기 때문에 정상입니다.

Dependency-Track 없이도 컴포넌트·라이선스 분석은 완전히 동작합니다. 취약점 데이터를 활성화하려면 [DT 커넥터](../admin-guide/dt-connector.md)를 보세요.

## 4단계 — 백업 스케줄링

프로덕션에서 호스트 외부 백업은 선택이 아닙니다. cron 항목을 추가하세요.

```bash
sudo crontab -e
# m h dom mon dow command
0 3 * * *  cd /opt/trustedoss-portal && bash scripts/backup.sh >> /var/log/trustedoss-backup.log 2>&1
```

`scripts/backup.sh`는 `backups/<타임스탬프>/`에 `postgres.sql.gz`, `workspace.tar.gz`, `manifest.json`을 작성합니다. 7일 이상 지난 백업은 자동 정리됩니다(`.env`의 `BACKUP_RETENTION_DAYS`로 변경).

전체 복원 절차는 [백업·복원](../admin-guide/backup-and-restore.md)을 보세요.

## 번들 Dependency-Track 추가 (선택)

기본 설치는 Dependency-Track을 포함하지 않습니다. 번들로 가져오려면:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dt.yml up -d
```

이후 [DT 커넥터](../admin-guide/dt-connector.md)를 따라 API Key를 연결하고 8개 OSV 생태계를 활성화하세요. 첫 미러 동기화는 Maven이 ~1시간, 나머지는 더 짧게 걸립니다.

## 종단 간 첫 성공 체크리스트 (30분)

`bash scripts/install.sh` 완료 후:

- [ ] `https://<your-host>` 열기 — 로그인 화면이 렌더링되고
  브라우저가 유효한 TLS 자물쇠를 표시(HTTPS 인 경우).
- [ ] 마법사가 출력한 super-admin 이메일·비밀번호로 로그인.
- [ ] `/admin/dt` 로 이동 — `bash scripts/install.sh` 는
  `docker-compose.yml` 만 띄우고 DT 오버레이는 활성화하지 않으므로
  행은 기본적으로 **OPEN** 입니다. 위 Step 3 의 설명과 동일하며
  정상입니다. 오버레이를 실행(`docker-compose -f docker-compose.yml
  -f docker-compose.dt.yml up -d`)하고 `.env` 의 `DT_API_KEY` 를
  설정한 경우에만 약 60초 안에 CLOSED 로 전환되는 것을 기다립니다.
- [ ] `/admin/teams` → **New team** 으로 이동 → 이름을 `engineering`
  으로 설정.
- [ ] 동료에게 `/register` 에서 가입을 요청한 뒤,
  `/admin/users → <user> → Memberships → Add to team` 에서 추가.
- [ ] 동료 세션으로 전환 → `/projects → New project` 에서 작은
  공개 레포(테스트용)로 프로젝트 생성.
- [ ] 스캔을 트리거; 우측 슬라이드 진행 드로어가 약 2~5분 안에
  `bootstrap → fetch → prep → cdxgen → ort →
  dt_upload → dt_findings → finalize` 순서로 진행되어야 합니다.
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

기존 리버스 프록시를 유지하려면 `docker-compose.yml`에서 Traefik 서비스를 제거하고 `/api`, `/health`, `/metrics`는 backend 컨테이너로, `/`는 frontend 컨테이너로 라우팅하도록 설정합니다.

### 백엔드가 healthy로 전환되지 않음

```text
✗ backend did not become healthy. Run: docker-compose -f docker-compose.yml logs backend
```

가장 흔한 원인:

- `DATABASE_URL`의 호스트가 컴포즈 네트워크에 없는 호스트입니다. 호스트 부분이 `postgres`(서비스명)인지 확인하세요. `localhost`나 `127.0.0.1` 금지.
- Postgres 컨테이너가 아직 healthy가 아닙니다. `docker-compose ps`에서 `postgres`가 `Up (healthy)`로 표시되어야 합니다. 재시작 중이라면 `docker-compose logs postgres`로 자격증명 불일치를 확인하세요.
- 스키마 마이그레이션 실패. `docker-compose exec backend alembic upgrade head`를 직접 실행해 트레이스백을 확인합니다.

### 설치 중 디스크 부족

cdxgen + ORT + Trivy의 Docker 레이어 캐시는 ~4 GB입니다. `/var/lib/docker`가 가득 차면 풀이 중단됩니다. 공간을 확보한 뒤 `docker-compose pull`과 `docker-compose up -d`를 다시 실행합니다.

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

## 왜 docker-compose V1 인가

본 프로젝트는 현재 모든 대상 환경이 V1을 제공한다는 이유로 Compose V1(`docker-compose`)을 표준으로 합니다. V2 문법 차이(특히 `version:` 헤더와 의존성 조건)는 CI에서 검증되지 않습니다. `docker compose`(V2) 사용을 도입한 PR은 리뷰에서 차단됩니다. [`CLAUDE.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/CLAUDE.md) 규칙 #10 참고.

## 함께 보기

- [기존 설치 업그레이드](./upgrade.md)
- [환경변수 참고](../reference/env-variables.md)
- [아키텍처 개요](../reference/architecture.md)
