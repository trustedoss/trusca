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

## 필수 키 {#required-keys}

다음 네 개는 반드시 존재해야 하며 비어 있어선 안 됩니다. 마법사가 설정합니다.

| 키 | 설정자 | 비고 |
|---|---|---|
| `SECRET_KEY` | 마법사(`openssl rand -hex 32`) | JWT 서명 키. 회전 시 모든 refresh token 무효. |
| `DATABASE_URL` | 마법사 | `postgresql+asyncpg://user:pass@postgres:5432/trustedoss`. compose 서비스명 `postgres` 호스트 사용. |
| `CORS_ALLOWED_ORIGINS` | 마법사 | 콤마 분리. 프로덕션은 origin을 명시적으로 열거해야 합니다 — 와일드카드 fallback 없음. |
| `DOMAIN` | 마법사 | Traefik의 host-rule이 사용하는 호스트명. scheme과 path는 제거. |

## 애플리케이션

| 키 | 기본값 | 설명 |
|---|---|---|
| `APP_ENV` | `dev` | `dev` 또는 `prod`. `prod`에서 더 엄격한 기본값 활성화(CSP, secure cookies). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `IMAGE_TAG` | `2.0.0` | `trustedoss/backend`, `trustedoss/backend-worker`, `trustedoss/frontend`의 핀 태그. |

## 데이터베이스

| 키 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | — | 위 참고. |
| `POSTGRES_USER` | `trustedoss` | postgres 컨테이너 init이 사용. `DATABASE_URL`과 일치해야 함. |
| `POSTGRES_PASSWORD` | — | 마법사가 생성. |
| `POSTGRES_DB` | `trustedoss` | 데이터베이스명. |

포털은 async SQLAlchemy + `asyncpg`를 사용합니다. 커넥션 풀 기본값은 FastAPI worker 수에 맞춰 튜닝되어 있습니다(uvicorn 워커 4 × 각 5 커넥션 = 20).

## Redis & Celery

| 키 | 기본값 | 설명 |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | 브로커 + 결과 백엔드. |
| `CELERY_CONCURRENCY` | `2` | worker 프로세스 수. 슬롯당 피크 시 ~2 GB RAM 필요. |

## 인증

| 키 | 기본값 | 설명 |
|---|---|---|
| `SECRET_KEY` | — | [필수 키](#required-keys) 참고. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT access token 수명. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token 수명. 회전 + 재사용 탐지 활성화. |

## Dependency-Track

| 키 | 기본값 | 설명 |
|---|---|---|
| `DT_URL` | `http://dtrack-api:8080` | DT API base URL. DT가 번들이면 compose 서비스명, 외부면 공개 URL. |
| `DT_API_KEY` | — | DT의 Automation 팀이 발급. 취약점 데이터에 **필수**. 없으면 breaker가 OPEN으로 유지되고 스캔은 캐시를 사용. |
| `DT_ORPHAN_AUTODELETE` | `false` | `true`이면 고아 정리가 DT 프로젝트를 자동 삭제. 기본은 운영자 확인 필요. |

부트스트랩 흐름은 [DT 커넥터](../admin-guide/dt-connector.md) 참고.

## Workspace & ORT

| 키 | 기본값 | 설명 |
|---|---|---|
| `WORKSPACE_HOST_PATH` | `/opt/trustedoss/workspace` | worker에 `/workspace`로 마운트되는 호스트 디렉터리. 레포 클론 + ORT 분석기 출력 보관. |
| `ORT_RULES_PATH` | `/opt/trustedoss/ort/rules.kts` | worker 내부 경로. 커스터마이즈한 룰을 여기에 마운트. |

## 알림

| 키 | 기본값 | 설명 |
|---|---|---|
| `SMTP_HOST` | (비어있음) | SMTP 서버. 없으면 이메일 알림이 건너뜀. |
| `SMTP_PORT` | `587` | SMTP 포트. 587에서 STARTTLS 기대. |
| `SMTP_USER` | (비어있음) | SMTP 사용자명. |
| `SMTP_PASSWORD` | (비어있음) | SMTP 비밀번호. |
| `SMTP_FROM` | `noreply@<DOMAIN>` | Envelope-from 주소. |
| `SLACK_WEBHOOK_URL` | (비어있음) | `super_admin` 알림용 조직 단위 Slack Webhook. 팀별 Webhook은 UI에서 구성. |
| `TEAMS_WEBHOOK_URL` | (비어있음) | 조직 단위 MS Teams Webhook. |

## OAuth (데모 SaaS 전용)

다가오는 데모 SaaS 배포에 적용. 자체 호스팅 설치는 비워 둡니다.

| 키 | 기본값 | 설명 |
|---|---|---|
| `GITHUB_CLIENT_ID` | (비어있음) | GitHub OAuth App client ID. |
| `GITHUB_CLIENT_SECRET` | (비어있음) | GitHub OAuth App client secret. |
| `GOOGLE_CLIENT_ID` | (비어있음) | Google OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | (비어있음) | Google OAuth client secret. |

## 백업

| 키 | 기본값 | 설명 |
|---|---|---|
| `BACKUP_RETENTION_DAYS` | `7` | `scripts/backup.sh --no-prune`로 실행별 오버라이드 가능. |
| `BACKUP_DIR` | `<repo>/backups` | 백업 스크립트가 쓰는 위치. |

## 디스크 가드

| 키 | 기본값 | 설명 |
|---|---|---|
| `DISK_WARN_LIMIT_PCT` | `70` | 노란 게이지 + 대시보드 배너. |
| `DISK_HARD_LIMIT_PCT` | `90` | 빨간 게이지 + 새 스캔 차단 + admin 알림. |

## Traefik / TLS

| 키 | 기본값 | 설명 |
|---|---|---|
| `TLS_EMAIL` | — | Let's Encrypt HTTP-01 챌린지가 사용하는 이메일. 인증서 발급에 필수. |
| `TRAEFIK_LOG_LEVEL` | `INFO` | 라우팅 이슈 추적 시 `DEBUG`가 유용. |

## 선택적 통합

| 키 | 기본값 | 설명 |
|---|---|---|
| `JIRA_ENABLED` | `false` | `true`이면 포털이 승인 요청에서 Jira 티켓 생성 가능. |
| `JIRA_URL` | (비어있음) | Jira 인스턴스 URL. |
| `JIRA_TOKEN` | (비어있음) | Jira API 토큰. |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | (비어있음) | `git clone`, `cdxgen`, `trivy`, DT 호출이 존중. |

## 관측성 (선택)

| 키 | 기본값 | 설명 |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (비어있음) | 설정되면 백엔드가 OTLP traces를 export. |
| `OTEL_SAMPLER` | `parentbased_always_off` | trace 샘플링 활성화는 `parentbased_always_on`. |

## 검증

백엔드는 시작 시 설정을 검증합니다.

- `SECRET_KEY`가 32자 미만이면 시작을 거부.
- `DATABASE_URL`이 `postgresql+asyncpg`를 사용하지 않으면 거부.
- `APP_ENV=prod`에서 `CORS_ALLOWED_ORIGINS`가 비어 있거나 `*`을 포함하면 거부.
- `DT_API_KEY`가 비어 있으면 경고(치명적 아님) — breaker가 OPEN으로 유지.

실패 시 `event=config.invalid`와 위반 키를 담은 구조화 로그 라인을 emit합니다.

## 정상 동작 확인

`.env` 편집 후:

```bash
docker-compose -f docker-compose.yml restart backend worker beat
docker-compose -f docker-compose.yml logs --tail=50 backend | grep -i config
```

시작 로그가 키와 함께 `event=config.loaded`를 출력해야 합니다(시크릿 값은 로그에 남지 않음).

## 함께 보기

- [`/.env.example`](https://github.com/trustedoss/trustedoss-portal/blob/main/.env.example) — 표준 레퍼런스, 항상 최신.
- [아키텍처](./architecture.md)
- [Docker Compose 설치](../installation/docker-compose.md)
