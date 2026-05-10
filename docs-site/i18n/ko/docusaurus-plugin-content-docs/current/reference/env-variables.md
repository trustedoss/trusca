---
id: env-variables
title: 환경 변수
description: TrustedOSS Portal이 읽는 .env 키의 완전한 레퍼런스 — 기본값, 검증, 런타임 시멘틱.
sidebar_label: 환경 변수
sidebar_position: 2
---

# 환경 변수

포털은 설정을 `.env`에서 읽습니다. 번들된 `.env.example`이 지원되는 모든 키를 열거합니다. 설치 마법사(`scripts/install.sh`)가 필수 키를 강한 기본값으로 채워 주고 나머지는 필요에 따라 설정합니다.

:::note 대상 독자
배포를 튜닝하는 운영자. `.env` 파일과 Docker Compose의 변수 치환에 익숙해야 합니다.
:::

## 읽기 순서

1. 레포 루트의 `.env`를 `docker-compose`가 자동 로드합니다.
2. 백엔드 코드는 `os.getenv()`를 **런타임에** 호출합니다 — 모듈 import 시점이 아닙니다. 이는 CLAUDE.md 규칙 #11. 컨테이너 재시작만으로 변경된 값을 픽업하며 재빌드는 필요 없습니다.
3. Compose는 `docker-compose.yml`의 `${VAR}` 참조를 `docker-compose up` 시점에 `.env`에서 치환합니다.

아래 모든 키는 `apps/backend/core/config.py`, `docker-compose.yml`, `scripts/*` 중 한 곳에서 읽습니다 — **읽는 위치** 컬럼에 표기되어 있습니다.

## 필수 키 {#required-keys}

다음 네 개는 반드시 존재해야 하며 비어 있어선 안 됩니다. 마법사가 설정합니다.

| 키 | 설정자 | 읽는 위치 | 비고 |
|---|---|---|---|
| `SECRET_KEY` | 마법사(`openssl rand -hex 32`) | `config.py` | JWT 서명 키 (HS256). 비-dev에서 최소 32자. 회전 시 모든 refresh token 무효. |
| `DATABASE_URL` | 마법사 | `config.py`, `docker-compose.yml` | `postgresql+asyncpg://user:pass@postgres:5432/trustedoss`. compose 서비스명 `postgres` 호스트 사용. |
| `CORS_ALLOWED_ORIGINS` | 마법사 | `config.py` | 콤마 분리. 프로덕션은 origin을 명시적으로 열거해야 하며 `allow_credentials=true` 와 함께 `*` 사용 시 부팅에서 거부됩니다. |
| `DOMAIN` | 마법사 | `docker-compose.yml` | Traefik의 host-rule이 사용하는 호스트명. scheme과 path는 제거. |

## 애플리케이션

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `APP_ENV` | `dev` | `config.py` | `dev`, `staging`, 또는 `prod`. 일부 CORS / 로그 기본값에 영향. |
| `LOG_LEVEL` | `INFO` | `config.py` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `IMAGE_TAG` | `2.0.0` | `docker-compose.yml` | `trustedoss/backend`, `trustedoss/backend-worker`, `trustedoss/frontend`의 핀 태그. |

## 데이터베이스

`DATABASE_URL`(위 표)이 표준 설정입니다. 아래 합성 대안은 GCP Cloud Run 모듈이 Secret Manager에서 `DB_PASSWORD`를 마운트할 때 DSN을 Terraform state에 굽지 않도록 제공됩니다. **`DATABASE_URL`** 또는 **네 개의 `DB_*` 키 중 하나만** 설정하세요 — 둘 다 설정 금지.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `DATABASE_URL` | — | `config.py`, `docker-compose.yml` | 위 참고. |
| `DB_USER` | — | `config.py` | 합성 DSN: 사용자명. 결과 DSN에서 URL 인코딩됨. |
| `DB_PASSWORD` | — | `config.py` | 합성 DSN: 비밀번호. URL 인코딩으로 `@`, `:`, `/`, `#`, `%` 가 파싱을 통과합니다. |
| `DB_HOST` | — | `config.py` | 합성 DSN: 호스트. Cloud SQL Auth Proxy 유닉스 소켓 경로(`/cloudsql/...`)도 가능. |
| `DB_PORT` | `5432` | `config.py` | 합성 DSN: 포트. |
| `DB_NAME` | — | `config.py` | 합성 DSN: 데이터베이스명. |
| `POSTGRES_USER` | `trustedoss` | `docker-compose.yml` | postgres 컨테이너 init이 사용. `DATABASE_URL`과 일치해야 함. |
| `POSTGRES_PASSWORD` | — | `docker-compose.yml` | 마법사가 생성. |
| `POSTGRES_DB` | `trustedoss` | `docker-compose.yml` | 데이터베이스명. |

`DB_*` 네 키 중 하나라도 설정되면 **모두** 설정해야 합니다 (그렇지 않으면 합성 분기에서 부팅 시 raise). 포털은 async SQLAlchemy + `asyncpg`를 사용합니다. 커넥션 풀 기본값은 FastAPI worker 수에 맞춰 튜닝되어 있습니다(uvicorn 워커 4 × 각 5 커넥션 = 20).

## Redis & Celery

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | `config.py` | 브로커 + 결과 백엔드. |
| `CELERY_CONCURRENCY` | `2` | `docker-compose.yml` | worker 프로세스 수. 슬롯당 피크 시 ~2 GB RAM 필요. |

## 인증

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `SECRET_KEY` | — | `config.py` | [필수 키](#required-keys) 참고. HS256 서명. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | `config.py` | JWT access token 수명. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | `config.py` | Refresh token 수명. 회전 + 재사용 탐지 활성화. |

## Dependency-Track

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `DT_URL` | `http://dtrack-api:8080` | `config.py` | DT API base URL(후행 슬래시 제거). DT가 번들이면 compose 서비스명, 외부면 공개 URL. |
| `DT_API_KEY` | (비어있음) | `config.py` | DT의 Automation 팀이 발급. 없으면 breaker가 OPEN으로 유지되고 스캔은 캐시 사용. |
| `DT_REQUEST_TIMEOUT_SECONDS` | `30` | `config.py` | 아웃바운드 DT 호출 HTTP 타임아웃. |
| `DT_BREAKER_FAILURE_THRESHOLD` | `5` | `config.py` | breaker를 CLOSED → OPEN으로 전환하는 연속 실패 횟수. |
| `DT_BREAKER_COOLDOWN_SECONDS` | `30` | `config.py` | breaker가 OPEN을 유지하다 HALF_OPEN 프로브를 허용하기까지의 대기 시간. |
| `DT_HEALTH_ENDPOINT` | `/api/version` | `config.py` | 60초 heartbeat가 `DT_URL`에 덧붙이는 경로. |
| `DT_AUTO_RESTART` | `false` | `config.py` | `true`이면 Health monitor가 지속적인 OPEN 후 `docker restart dtrack-api`를 시도. DT가 외부이거나 운영자 주도 복구만 원하면 `false` 유지. |

부트스트랩 흐름은 [DT 커넥터](../admin-guide/dt-connector.md) 참고.

## 스캔 파이프라인

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `TRUSTEDOSS_SCAN_BACKEND` | `real` | `config.py` | `real`(서브프로세스 `cdxgen` / ORT / Trivy) 또는 `mock`(픽스쳐 JSON). `mock`은 테스트 하네스의 dev / CI 기본값입니다. 프로덕션은 `real` 유지. |
| `WORKSPACE_HOST_PATH` | `/tmp/trustedoss` | `config.py`, `docker-compose.yml` | worker에 `/workspace`로 마운트되는 호스트 디렉터리. 레포 클론 + ORT 분석기 출력 보관. compose 스택은 컨테이너 내에서 `/workspace`로 오버라이드합니다. |
| `ORT_RULES_PATH` | `/opt/trustedoss/ort/rules.kts` | `docker-compose.yml` | worker 내부 경로. 커스터마이즈한 룰을 여기에 마운트. |
| `JSONB_ROW_SIZE_LIMIT_BYTES` | `262144` (256 KB) | `config.py` | writer가 truncate + warn하기 전 행당 JSON 바이트 상한. I-1 무한 페이로드 클래스 가드. |

## WebSocket 게이트웨이

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `WEBSOCKET_MAX_CONNECTIONS_PER_USER` | `3` | `config.py` | 사용자당 동시 커넥션 상한. 같은 사용자의 4번째 커넥션이 가장 오래된 것을 close code 1001(`reason="newer_connection"`)로 evict합니다. **워커 프로세스별** 적용 — 멀티 워커 배포는 N × worker-count 까지 허용. |
| `WEBSOCKET_AUTH_TIMEOUT_SECONDS` | `1.0` | `config.py` | 첫 `{"type":"auth"}` 프레임을 기다리는 시간. 윈도우 내 미수신 시 1008 / `reason="auth_timeout"`으로 닫힘. |

## 알림

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `SMTP_HOST` | (비어있음) | `config.py` | SMTP 서버. 없으면 이메일 알림이 `NotificationDisabled`를 raise하고 채널은 건너뜁니다. |
| `SMTP_PORT` | `587` | `config.py` | SMTP 포트. 587에서 STARTTLS 기대. |
| `SMTP_USER` | (비어있음) | `config.py` | SMTP 사용자명. |
| `SMTP_PASSWORD` | (비어있음) | `config.py` | SMTP 비밀번호. |
| `SMTP_USE_STARTTLS` | `true` | `config.py` | 465에서 implicit TLS를 요구하는 SMTP 서버 또는 25 테스트 시에만 `false`. |
| `SMTP_FROM` | `no-reply@trustedoss.local` | `config.py` | 아웃고잉 알림의 `From:` 헤더. 환경별 오버라이드 권장. |
| `SMTP_TIMEOUT_SECONDS` | `10` | `config.py` | 호출당 SMTP 소켓 타임아웃. |
| `SLACK_WEBHOOK_URL` | (비어있음) | `config.py` | `super_admin` 알림용 조직 단위 Slack Webhook. 팀별 Webhook은 UI에서 구성. |
| `TEAMS_WEBHOOK_URL` | (비어있음) | `config.py` | 조직 단위 MS Teams Webhook. |
| `NOTIFICATION_HTTP_TIMEOUT_SECONDS` | `10` | `config.py` | Slack / Teams Webhook 아웃바운드 HTTP 타임아웃. |

## 비밀번호 재설정

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `PASSWORD_RESET_BASE_URL` | `http://localhost:5173` | `config.py` | 재설정 이메일에 임베드되는 프론트엔드 base URL. 링크 템플릿: `{base}/reset-password?token={token}`. |
| `PASSWORD_RESET_RATE_LIMIT` | `5/minute` | `config.py` | `POST /auth/forgot-password`에 대한 IP별 slowapi 한도. |
| `PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS` | `300` | `config.py` | 같은 주소로 두 번째 재설정 이메일 발송까지 최소 초 수. 쿨다운 시 `Retry-After`로 반환. |

## OAuth (데모 SaaS 전용)

데모 SaaS 배포에 적용. 자체 호스팅 설치는 비워 둡니다(이 경우 `/auth/oauth/{provider}/authorize` 엔드포인트가 503과 `oauth_provider_disabled = true`를 반환).

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `GITHUB_CLIENT_ID` | (비어있음) | `config.py` | GitHub OAuth App client ID. |
| `GITHUB_CLIENT_SECRET` | (비어있음) | `config.py` | GitHub OAuth App client secret. |
| `GOOGLE_CLIENT_ID` | (비어있음) | `config.py` | Google OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | (비어있음) | `config.py` | Google OAuth client secret. |
| `OAUTH_STATE_TTL_SECONDS` | `300` | `config.py` | 서명된 `state` JWT 수명(CSRF 가드). RFC 6749 §10.12. |
| `OAUTH_HTTP_TIMEOUT_SECONDS` | `10` | `config.py` | OAuth 공급자 API로의 아웃바운드 HTTP 타임아웃. |
| `OAUTH_LOGIN_REDIRECT_DEFAULT` | `http://localhost:5173/` | `config.py` | OAuth 콜백 성공 후 SPA가 도착하는 곳. |
| `OAUTH_LOGIN_REDIRECT_FAILURE` | `http://localhost:5173/login` | `config.py` | 콜백 실패 시 SPA가 도착하는 곳. `?error=oauth_failed` 수신. |

## 백업

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `BACKUP_RETENTION_DAYS` | `7` | `scripts/backup.sh` | `scripts/backup.sh --no-prune`로 실행별 오버라이드. |
| `BACKUP_DIR` | `<repo>/backups` | `scripts/backup.sh` | 백업 스크립트가 쓰는 위치. |

## 디스크 가드

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `DISK_HARD_LIMIT_PCT` | `95.0` | `apps/backend/services/scan_service.py` | 빨간 게이지 + 새 스캔 차단 + admin 알림. |

## Traefik / TLS

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `DOMAIN` | — | `docker-compose.yml` | [필수 키](#required-keys) 참고. |
| `TLS_EMAIL` | — | `docker-compose.yml` | Let's Encrypt HTTP-01 챌린지가 사용하는 이메일. 인증서 발급에 필수. |
| `TRAEFIK_LOG_LEVEL` | `INFO` | `docker-compose.yml` | 라우팅 이슈 추적 시 `DEBUG`가 유용. |

## 선택적 통합

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `JIRA_ENABLED` | `false` | (없음) | **스텁 — v2.0.0의 어떤 코드 경로에서도 소비되지 않음.** Phase B Jira 통합용 예약. 기능 도착 시 기존 배포가 깨지지 않도록 `.env.example`에 포함. |
| `JIRA_URL` | (비어있음) | (없음) | 스텁. 위 참고. |
| `JIRA_TOKEN` | (비어있음) | (없음) | 스텁. 위 참고. |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | (비어있음) | 서브프로세스 env | `git clone`, `cdxgen`, `trivy`, DT 호출이 존중. |

## 부트스트랩 / 스크립트

다음 키는 부트스트랩과 데모 시드 스크립트만 읽습니다. 동작 중인 백엔드가 소비하지는 않지만 설치·데모 시점에 설정합니다.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `ADMIN_EMAIL` | — | `apps/backend/scripts/create_super_admin.py` | 스크립트 실행 시 프로비저닝할 첫 super-admin의 이메일. 읽을 때 소문자화·trim. |
| `ADMIN_PASSWORD` | — | `apps/backend/scripts/create_super_admin.py` | 부트스트랩 super-admin의 비밀번호. 12자 이상 필수 — 그렇지 않으면 스크립트가 중단됩니다. |
| `DEMO_SUPER_ADMIN_PASSWORD` | (자동 생성) | `apps/backend/scripts/seed_demo.py` | 데모 시드의 super-admin 비밀번호 오버라이드. `APP_ENV`가 `staging` 또는 `prod`일 때 필수이며 설정 시 12자 이상이어야 합니다. |

## 검증

백엔드는 시작 시 설정을 검증합니다(`apps/backend/main.py` lifespan).

- 비-dev `APP_ENV`에서 `SECRET_KEY`가 32자 미만이면 시작 거부.
- `CORS_ALLOWED_ORIGINS`에 `*`가 포함되고 credentials 허용 시 거부.
- `APP_ENV=prod`에서 origin이 평문 `http://`이면 거부.
- `DB_*` 키가 부분 설정이면 거부(합성 DSN 경로는 all-or-nothing).

실패 시 구조화 로그 라인을 emit하고 프로세스가 크래시 — 관대한 fallback은 없습니다.

## 정상 동작 확인

`.env` 편집 후:

```bash
docker-compose -f docker-compose.yml restart backend worker beat
docker-compose -f docker-compose.yml logs --tail=50 backend | grep backend_starting
```

시작 로그가 `app_env` 필드를 담은 단일 `backend_starting` 이벤트를 emit해야 합니다. 시크릿은 결코 로그에 남지 않습니다.

## 함께 보기

- [`/.env.example`](https://github.com/trustedoss/trustedoss-portal/blob/main/.env.example) — 표준 레퍼런스, 항상 최신.
- [아키텍처](./architecture.md)
- [Docker Compose 설치](../installation/docker-compose.md)
