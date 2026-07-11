---
id: env-variables
title: 환경 변수
description: TRUSCA가 읽는 .env 키의 완전한 레퍼런스 — 기본값, 검증, 런타임 시멘틱.
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
| `IMAGE_TAG` | `0.11.0` | `docker-compose.yml` | `ghcr.io/trustedoss/trusca-backend`, `…/trusca-backend-worker`, `…/trusca-frontend`의 핀 태그. |

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

## 취약점 데이터

포털은 SBOM을 로컬 **Trivy DB**(NVD + OSV + GHSA + EPSS + KEV 통합 번들)에 대조합니다. 라이프사이클은 [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) 참조.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `TRIVY_DB_REPOSITORY` | `ghcr.io/aquasecurity/trivy-db` | `config.py` | Trivy DB를 받아오는 OCI 저장소. air-gapped 사내 미러로 오버라이드 — [Air-gapped 운영](../admin-guide/vulnerability-data.md#air-gapped) 참조. |
| `TRIVY_DB_REFRESH_HOURS` | `168` (주간) | `config.py` | `trivy_db_refresh` 태스크의 Celery Beat 주기. 낮추면 신선도↑, 높이면 egress↓. |
| `TRIVY_CACHE_DIR` | `/var/lib/trivy` | `integrations/trivy.py` | DB가 풀리는 디렉터리. 공유 `trivy-cache` 볼륨이 뒷받침 — 워커(rw)와 backend(ro)가 함께 마운트해 관리자 health/disk 패널이 DB 상태를 읽을 수 있다. |
| `TRIVY_TIMEOUT_SECONDS` | `300` | `config.py` | `trivy sbom` 스캔별 타임아웃. 매우 큰 모노레포는 `600`~`900`으로 상향. |

### KEV 카탈로그 {#kev-catalog}

포털은 Trivy DB 번들과 별개로 [CISA KEV(Known Exploited Vulnerabilities, 알려진 악용 취약점) 카탈로그](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)를 하루 한 번 취약점 카탈로그에 동기화합니다(Celery beat 태스크 `trustedoss.kev_catalog_refresh`, 약 1,600건, 등재 해제 포함). KEV 등재 결과는 배지와 대응 기한을 표시하고 기본 **Priority** 정렬을 구동합니다 — [취약점 — KEV](../user-guide/vulnerabilities.md#kev) 참고.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `KEV_FEED_URL` | `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json` | `config.py` | 일일 refresh가 KEV 피드를 내려받는 URL. 사내 미러를 쓰려면 CISA JSON의 미러 주소로 오버라이드하십시오. |
| `KEV_REFRESH_ENABLED` | `true` | `config.py` | 일일 refresh 토글. 피드에 접근할 수 없는 air-gapped 배포는 `false`로 설정하십시오 — refresh를 끄면 KEV 데이터가 로드되지 않으므로 **KEV 배지와 대응 기한이 표시되지 않고**, Priority 정렬은 사실상 심각도 → EPSS로 동작합니다. |
| `KEV_REFRESH_TIMEOUT_SECONDS` | `30` | `config.py` | CISA 피드 다운로드의 아웃바운드 HTTP 타임아웃. |

## 빌드 / 정책 게이트

CI 빌드 게이트는 기본적으로 Critical CVE와 금지 라이선스에서 빌드를 실패시키며, 이 조건들은 env로 구동되지 않습니다. 아래 단일 env 노브는 **선택적** EPSS 차원을 더합니다.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `GATE_EPSS_THRESHOLD` | (미설정) | `config.py` | 선택적 EPSS 게이트. `0`~`1` 값. 설정 시 미해결 결과 중 `epss_score >= GATE_EPSS_THRESHOLD`인 것이 있으면 빌드 게이트도 실패하며, 게이트 결과에 `epss_gate_count` + `epss_threshold`가 실립니다. **미설정(기본)이면 EPSS 게이트는 비활성** — 기존 Critical-CVE / 금지-라이선스 조건만 적용됩니다. EPSS 값이 없는 결과는 게이트를 트리거하지 않습니다. EPSS 데이터는 Trivy DB에서 옵니다 — Trivy가 값을 제공하는 CVE만 대상입니다. |

게이트 모델은 [빌드 게이트](./glossary.md#빌드-게이트), CI 워크스루는 [EPSS로 빌드 게이팅](../ci-integration/github-actions.md#epss로-빌드-게이팅-선택) 참고.

## 스캔 파이프라인 {#scan-pipeline}

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `TRUSTEDOSS_SCAN_BACKEND` | `real` | `config.py` | `real`(서브프로세스 `cdxgen` / scancode / Trivy) 또는 `mock`(픽스쳐 JSON). `mock`은 테스트 하네스의 dev / CI 기본값입니다. 프로덕션은 `real` 유지. |
| `SCANCODE_TIMEOUT_SECONDS` | `600` | `config.py` | scancode first-party 라이선스 단계의 hard wall-clock 한도. 타임아웃 시 declared 라이선스만으로 스캔을 계속합니다(best-effort). |
| `SCANCODE_MAX_FILES` | `20000` | `config.py` | 적격 first-party 파일(제외 필터 적용 후) 상한. 초과 시 scancode 를 건너뛰고 declared 라이선스만 유지합니다. |
| `SCANCODE_MAX_DETECTIONS` | `5000` | `config.py` | 스캔당 저장되는 detected 라이선스 결과 수 상한. |
| `SCANCODE_MAX_RESULT_BYTES` | `268435456` (256 MB) | `config.py` | 파싱 전 scancode JSON 아티팩트 상한 — 악의적 트리의 OOM 가드. |
| `SCANOSS_ENABLED` | `false` | `config.py` | SCANOSS vendored-OSS 단계 마스터 opt-in. **기본 비활성.** `true`면 소스 트리를 핑거프린트해 그 핑거프린트(소스 자체는 아님)를 `SCANOSS_API_URL`로 보내 복사된 OSS를 식별 — 그 외부 egress에 동의할 때만 켜세요. `false`면 단계 전체 스킵(스캐너·egress 없음). [컴포넌트·라이선스 → Vendored-OSS 식별](../user-guide/components-and-licenses.md#vendored-oss) 참고. |
| `SCANOSS_API_URL` | `https://api.osskb.org` | `config.py` | 핑거프린트를 매칭할 SCANOSS 지식 베이스 엔드포인트(`SCANOSS_ENABLED=true`일 때만 사용). 자체 호스팅 SCANOSS 인스턴스로 향하게 하면 핑거프린트가 사내에 머뭅니다. |
| `SCANOSS_API_KEY` | *(빈 값)* | `config.py` | `SCANOSS_API_URL`용 선택 API 키(유료/자체 호스팅 엔드포인트). 비우면 무료 `api.osskb.org` 등급 사용. |
| `SCANOSS_TIMEOUT_SECONDS` | `300` | `config.py` | SCANOSS 단계의 하드 wall-clock 제한. 타임아웃 시 vendored-OSS 결과 없이 스캔 계속(best-effort). |
| `SCAN_SCOPE_FILTER_ENABLED` | `true` | `config.py` | 런타임 스코프 필터의 마스터 스위치: 소스 스캔이 저장·서명·Trivy 매칭 전에 배포되지 않는 의존성(Maven `test`/`provided`, npm `devDependencies`)을 SBOM 에서 제거합니다. 외부 전송 없는 순수 로컬 변환입니다. 정확히 `false` / `0` / `no` 토큰만 끕니다. [컴포넌트·라이선스 → 런타임 스코프 필터링](../user-guide/components-and-licenses.md#runtime-scope-filtering) 참고. |
| `SCAN_SCOPE_FILTER_MAVEN_ENABLED` | `true` | `config.py` | 스코프 필터의 Maven 부분(cdxgen scope `optional`/`excluded` 노드 제거). 프로젝트가 Maven `<optional>true</optional>` **런타임** 의존성을 쓰면 끄십시오 — cdxgen 이 test scope 와 똑같이 `optional` 로 태깅해 함께 제거됩니다. |
| `SCAN_SCOPE_FILTER_NODE_ENABLED` | `true` | `config.py` | 스코프 필터의 npm 부분(커밋되었거나 prep 단계가 생성한 `package-lock.json` 이 `dev` 로 분류한 패키지 제거). lockfile 에 없는 패키지는 항상 유지합니다. |
| `EOL_ENABLED` | `true` | `config.py` | 지원 종료(EOL) 표시: endoflife.date 추적 제품 목록에 맞는 컴포넌트를 공유 카탈로그에 `eol` / `supported` / `unknown` 으로 기록합니다. 완전 오프라인 — 판정은 릴리즈에 벤더링된 스냅숏에서 나오며 외부 전송이 없습니다. 정확히 `false` / `0` / `no` 만 끕니다. [컴포넌트·라이선스 → 지원 종료 표시](../user-guide/components-and-licenses.md#end-of-life-flagging) 참고. |
| `EOL_SNAPSHOT_PATH` | *(빈 값 — 벤더 파일)* | `config.py` | endoflife.date 스냅숏 재정의. air-gapped 설치에서는 연결된 호스트에서 더 신선한 스냅숏을 만들어(`python3 scripts/refresh_eol_snapshot.py`) 마운트한 뒤 이 변수로 지정합니다. |
| `EOL_REFRESH_ENABLED` | `false` | `config.py` | 실시간 수집 opt-in: 주간 beat 가 `EOL_FEED_URL_TEMPLATE` 에서 신선한 라이프사이클 데이터를 내려받습니다. **기본 꺼짐** — 새로운 외부 전송이기 때문입니다. beat 의 로컬 재기록 패스는 이 값과 무관하게 실행됩니다. 정확히 `true` / `1` / `yes` 토큰만 켭니다(fail-closed, SCANOSS 방식). |
| `EOL_FEED_URL_TEMPLATE` | `https://endoflife.date/api/{product}.json` | `config.py` | 실시간 수집용 제품별 API 템플릿(`{product}` 치환). 내부 미러를 지정하면 전송이 사내에 머뭅니다. |
| `EOL_REFRESH_TIMEOUT_SECONDS` | `15` | `config.py` | 실시간 수집 시 제품 요청당 HTTP 타임아웃. `[1, 120]` 범위이며, 전체 수집은 별도로 60초 wall-clock 으로 제한됩니다. |
| `WORKSPACE_HOST_PATH` | `/tmp/trustedoss` | `config.py`, `docker-compose.yml` | worker에 `/workspace`로 마운트되는 호스트 디렉터리. 레포 클론 + 스캔 아티팩트(cdxgen SBOM, scancode 출력) 보관. compose 스택은 컨테이너 내에서 `/workspace`로 오버라이드합니다. |
| `ORT_RULES_PATH` | `/opt/trustedoss/ort/rules.kts` | `docker-compose.yml` | worker 내부 레거시 경로로, ORT 단계 제거 후 잔재입니다. 파일은 placeholder 이며 v0.10.0 에서는 효과가 없습니다 — 라이선스 단계 분류는 `apps/backend/tasks/scan_source.py` 의 `_LICENSE_CATEGORY_DEFAULTS` 에서 옵니다. |
| `JSONB_ROW_SIZE_LIMIT_BYTES` | `262144` (256 KB) | `config.py` | writer가 truncate + warn하기 전 행당 JSON 바이트 상한. I-1 무한 페이로드 클래스 가드. |

## 스캔 보존(retention) {#scan-retention}

superseded·노후 스캔 스냅샷을 회수하는 자동 보존 sweep을 조정하는 키입니다. sweep은 6시간 주기 Celery beat 태스크로 실행됩니다. 전체 모델은 [스캔 보존](../admin-guide/scan-retention.md)을 참고하십시오.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` | `7` | `config.py` | superseded 스냅샷이 sweep에 회수되기 전 보존되는 일수입니다. 동일한 `(project, 정규화된 ref)` 타겟에 더 새로운 성공 스캔이 도착하면 기존 스냅샷이 superseded 됩니다. 타겟별 롤백 이력을 더 길게 유지하려면 값을 높이십시오. |
| `SCAN_RETENTION_KEEP_LAST` | `30` | `config.py` | 나이와 무관하게 **프로젝트당** 보존되는 ref-less·실패 스캔의 최소 개수입니다. sweep은 이 하한 아래로 트림하지 않습니다 — ref 타겟이 없는 ad-hoc·진단 스캔을 보호합니다. |
| `SCAN_RETENTION_MAX_AGE_DAYS` | `180` | `config.py` | hard age 상한. release가 아닌 스캔이 이보다 오래되면 해당 타겟의 live 스냅샷이라도 sweep이 회수합니다. `metadata.release` 라벨이 붙은 스캔은 예외이며 영구 보존됩니다. |

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
| `JIRA_ENABLED` | `false` | (없음) | **스텁 — 현재 릴리스의 어떤 코드 경로에서도 소비되지 않음.** Phase B Jira 통합용 예약. 기능 도착 시 기존 배포가 깨지지 않도록 `.env.example`에 포함. |
| `JIRA_URL` | (비어있음) | (없음) | 스텁. 위 참고. |
| `JIRA_TOKEN` | (비어있음) | (없음) | 스텁. 위 참고. |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | (비어있음) | 서브프로세스 env | `git clone`, `cdxgen`, `trivy --download-db-only` 부팅 / refresh 경로가 존중. |

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

- [`/.env.example`](https://github.com/trustedoss/trusca/blob/main/.env.example) — 표준 레퍼런스, 항상 최신.
- [아키텍처](./architecture.md)
- [Docker Compose 설치](../installation/docker-compose.md)
