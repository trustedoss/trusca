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
| `CORS_ALLOWED_ORIGINS` | 마법사 | `config.py` | 콤마 분리. 프로덕션은 origin을 명시적으로 열거해야 하며 `allow_credentials=true`와 함께 `*` 사용 시 부팅에서 거부됩니다. |
| `DOMAIN` | 마법사 | `docker-compose.yml` | Traefik의 host-rule이 사용하는 호스트명. scheme과 path는 제거. |

## 애플리케이션

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `APP_ENV` | `dev` | `config.py` | `dev`, `staging`, 또는 `prod`. 일부 CORS / 로그 기본값에 영향. |
| `LOG_LEVEL` | `INFO` | `config.py` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `IMAGE_TAG` | `0.11.0` | `docker-compose.yml` | `ghcr.io/trustedoss/trusca-backend`, `…/trusca-backend-worker`, `…/trusca-frontend`의 핀 태그. |
| `UVICORN_WORKERS` | `4` | `Dockerfile.prod`(uvicorn CLI), `config.py` | 백엔드 컨테이너가 띄우는 uvicorn 워커 프로세스 수. 값을 올리면 컨테이너를 늘리는 대신 컨테이너 하나가 쓰는 CPU 코어 수가 늘어나며, 값을 올리기 전에 아래 커넥션 예산 계산식에도 반영해야 한다. |

## 데이터베이스

`DATABASE_URL`(위 표)이 표준 설정입니다. 아래 합성 대안은 GCP Cloud Run 모듈이 Secret Manager에서 `DB_PASSWORD`를 마운트할 때 DSN을 Terraform state에 굽지 않도록 제공됩니다. **`DATABASE_URL`** 또는 **네 개의 `DB_*` 키 중 하나만** 설정하세요 — 둘 다 설정 금지.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `DATABASE_URL` | — | `config.py`, `docker-compose.yml` | 위 참고. |
| `DB_USER` | — | `config.py` | 합성 DSN: 사용자명. 결과 DSN에서 URL 인코딩됨. |
| `DB_PASSWORD` | — | `config.py` | 합성 DSN: 비밀번호. URL 인코딩으로 `@`, `:`, `/`, `#`, `%`가 파싱을 통과합니다. |
| `DB_HOST` | — | `config.py` | 합성 DSN: 호스트. Cloud SQL Auth Proxy 유닉스 소켓 경로(`/cloudsql/...`)도 가능. |
| `DB_PORT` | `5432` | `config.py` | 합성 DSN: 포트. |
| `DB_NAME` | — | `config.py` | 합성 DSN: 데이터베이스명. |
| `POSTGRES_USER` | `trustedoss` | `docker-compose.yml` | postgres 컨테이너 init이 사용. `DATABASE_URL`과 일치해야 함. |
| `POSTGRES_PASSWORD` | — | `docker-compose.yml` | 마법사가 생성. |
| `POSTGRES_DB` | `trustedoss` | `docker-compose.yml` | 데이터베이스명. |

`DB_*` 네 키 중 하나라도 설정되면 **모두** 설정해야 합니다 (그렇지 않으면 합성 분기에서 부팅 시 raise). 포털은 async SQLAlchemy + `asyncpg`를 사용합니다. 커넥션 풀 크기(`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_SYNC_POOL_SIZE`, `DB_SYNC_MAX_OVERFLOW`)는 `.env.example`의 "Postgres connection budget" 절에 정리되어 있습니다. FastAPI 풀 값에 uvicorn 워커 수와 백엔드 레플리카 수를 곱하고 Celery 워커·beat 풀을 더한 값이 Postgres `max_connections` 이내여야 하며, 배포 형태가 이 예산을 넘으면 백엔드가 부팅 시 경고를 남깁니다.

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
| `REFRESH_TOKEN_RETENTION_GRACE_DAYS` | `1` | `tasks/auth_token_retention.py` | refresh token 행이 자신의 `expires_at`을 지난 뒤 며칠까지 남아 있다가 매일 도는 정리 작업에서 삭제되는지. 회전·로그아웃·재사용 탐지로 폐기된 행도 `expires_at` 값 자체는 바뀌지 않으므로, `REFRESH_TOKEN_EXPIRE_DAYS` 한 주기 안에 같은 조건으로 함께 삭제됩니다. 폐기 시각을 따로 추적하는 경로는 두지 않았습니다. |
| `PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS` | `1` | `tasks/auth_token_retention.py` | 비밀번호 재설정 토큰 행이 자신의 `expires_at`을 지난 뒤 며칠까지 남아 있다가 삭제되는지. 위 refresh token 항목과 같은 방식입니다. |

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

### 취약점 SLA {#vuln-sla}

심각도별 조치 SLA 기간으로, 결과의 프로젝트 단위 **최초 탐지** 시각(재스캔·재매칭을 거쳐도 승계)부터 계산합니다. 취약점 목록은 이 기간으로 기한과 `overdue` / `imminent` / `ok` 상태를 계산하고, 일일 sweep은 미해결 결과가 기한을 넘기면 인앱 알림을 발행합니다. [취약점 — 조치 SLA와 경과시간 추적](../user-guide/vulnerabilities.md#sla) 참고.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `VULN_SLA_DAYS_CRITICAL` | `7` | `config.py` | Critical 결과의 조치 기간(일). 숫자가 아니거나 0 이하인 값은 시계를 끄는 대신 기본값으로 되돌아갑니다. |
| `VULN_SLA_DAYS_HIGH` | `30` | `config.py` | High 결과의 조치 기간(일). 대체 규칙은 위와 같습니다. |
| `VULN_SLA_DAYS_MEDIUM` | `90` | `config.py` | Medium 결과의 조치 기간(일). 대체 규칙은 위와 같습니다. |
| `VULN_SLA_DAYS_LOW` | `180` | `config.py` | Low 결과의 조치 기간(일). 대체 규칙은 위와 같습니다. `Info` / `Unknown` 심각도에는 기간도 키도 없습니다 — SLA를 부여하지 않습니다. |
| `VULN_SLA_ALERTS_ENABLED` | `true` | `config.py` | 일일 SLA 초과 sweep(Celery beat `trustedoss.vuln_sla_sweep`, 02:45 UTC) 토글. sweep은 외부 송신이 없는 순수 내부 계산이며, 정확히 `false` / `0` / `no` 토큰만 비활성화합니다. |

## 빌드 게이트 {#빌드--정책-게이트}

CI 빌드 게이트는 기본적으로 Critical CVE와 금지 라이선스에서 빌드를 실패시키며, 이 조건들은 env로 구동되지 않습니다. 아래 단일 env 노브는 **선택적** EPSS 차원을 더합니다.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `GATE_MALICIOUS_ENABLED` | `true` | `policy_gate.py` | 빌드 게이트가 알려진 악성 패키지를 차단할지 정합니다. 다른 `GATE_*` 설정과 달리 기본값이 켜짐입니다. 나머지는 이미 있는 신호를 얼마나 엄격히 읽을지 조정하지만 이것은 공격이 그대로 배포될지를 정하기 때문입니다. 심각도와 무관하게 차단합니다. 악성 패키지에는 올라갈 정상 버전이 없습니다. 끄면 `malicious_component_count`가 0이 되는데 이는 확인하지 않았다는 뜻이며 `malicious_gate_enforced`가 그것을 알려 줍니다. `false` / `0` / `no`만 끕니다. |
| `GATE_EPSS_THRESHOLD` | (미설정) | `config.py` | 선택적 EPSS 게이트. `0`~`1` 값. 설정 시 미해결 결과 중 `epss_score >= GATE_EPSS_THRESHOLD`인 것이 있으면 빌드 게이트도 실패하며, 게이트 결과에 `epss_gate_count` + `epss_threshold`가 실립니다. **미설정(기본)이면 EPSS 게이트는 비활성** — 기존 Critical-CVE / 금지-라이선스 조건만 적용됩니다. EPSS 값이 없는 결과는 게이트를 트리거하지 않습니다. EPSS 데이터는 Trivy DB에서 옵니다 — Trivy가 값을 제공하는 CVE만 대상입니다. |

게이트 모델은 [빌드 게이트](./glossary.md#빌드-게이트), CI 워크스루는 [EPSS로 빌드 게이팅](../ci-integration/github-actions.md#epss로-빌드-게이팅-선택) 참고.

## 스캔 파이프라인 {#scan-pipeline}

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `TRUSTEDOSS_SCAN_BACKEND` | `real` | `config.py` | `real`(서브프로세스 `cdxgen` / scancode / Trivy) 또는 `mock`(픽스쳐 JSON). `mock`은 테스트 하네스의 dev / CI 기본값입니다. 프로덕션은 `real` 유지. |
| `SCANCODE_TIMEOUT_SECONDS` | `600` | `config.py` | scancode first-party 라이선스 단계의 hard wall-clock 한도. 타임아웃 시 declared 라이선스만으로 스캔을 계속합니다(best-effort). |
| `SCANCODE_MAX_FILES` | `20000` | `config.py` | 적격 first-party 파일(제외 필터 적용 후) 상한. 초과 시 scancode를 건너뛰고 declared 라이선스만 유지합니다. |
| `SCANCODE_MAX_DETECTIONS` | `5000` | `config.py` | 스캔당 저장되는 detected 라이선스 결과 수 상한. |
| `SCANCODE_MAX_RESULT_BYTES` | `268435456` (256 MB) | `config.py` | 파싱 전 scancode JSON 아티팩트 상한 — 악의적 트리의 OOM 가드. |
| `SCANOSS_ENABLED` | `false` | `config.py` | SCANOSS vendored-OSS 단계 마스터 opt-in. **기본 비활성.** `true`면 소스 트리를 핑거프린트해 그 핑거프린트(소스 자체는 아님)를 `SCANOSS_API_URL`로 보내 복사된 OSS를 식별 — 그 외부 egress에 동의할 때만 켜세요. `false`면 단계 전체 스킵(스캐너·egress 없음). [컴포넌트·라이선스 → Vendored-OSS 식별](../user-guide/components-and-licenses.md#vendored-oss) 참고. |
| `SCANOSS_API_URL` | `https://api.osskb.org` | `config.py` | 핑거프린트를 매칭할 SCANOSS 지식 베이스 엔드포인트(`SCANOSS_ENABLED=true`일 때만 사용). 자체 호스팅 SCANOSS 인스턴스로 향하게 하면 핑거프린트가 사내에 머뭅니다. |
| `SCANOSS_API_KEY` | *(빈 값)* | `config.py` | `SCANOSS_API_URL`용 선택 API 키(유료/자체 호스팅 엔드포인트). 비우면 무료 `api.osskb.org` 등급 사용. |
| `SCANOSS_TIMEOUT_SECONDS` | `300` | `config.py` | SCANOSS 단계의 하드 wall-clock 제한. 타임아웃 시 vendored-OSS 결과 없이 스캔 계속(best-effort). |
| `TRUSTEDOSS_VERSION` | `unknown` | `config.py` | 이 배포가 자기 버전으로 밝히는 값입니다. SLSA 프로버넌스, About 화면, 그리고 TRUSCA가 만드는 모든 SBOM의 생성 도구 버전에 쓰입니다. 릴리스 이미지는 빌드 시 태그를 주입합니다(`ARG TRUSTEDOSS_VERSION`). 기본값을 그럴듯한 버전 번호로 두지 않은 것은 의도입니다. 자리표시자를 넣으면 세 곳 모두 존재하지 않는 릴리스를 주장하게 되고, 식별자가 없을 때 `unknown`으로 적는 것이 2026 SBOM 최소 요소가 요구하는 방식입니다. |
| `SBOM_AUTHOR` | *(미설정)* | `config.py` | SBOM 데이터를 만든 주체입니다. 내보내는 모든 SBOM에 작성자로 기록합니다. 2026 SBOM 최소 요소가 요구하는 항목인데 스캔으로는 알아낼 수 없습니다. 포털을 운영하는 조직이기 때문입니다. 설정하지 않으면 자리표시자를 넣지 않고 필드를 생략합니다. 자리표시자는 항목만 채울 뿐 받는 쪽에 아무것도 알려 주지 않습니다. |
| `SCAN_SCOPE_FILTER_ENABLED` | `true` | `config.py` | 런타임 스코프 필터의 마스터 스위치: 소스 스캔이 저장·서명·Trivy 매칭 전에 배포되지 않는 의존성(Maven `test`/`provided`, npm `devDependencies`)을 SBOM에서 제거합니다. 외부 전송 없는 순수 로컬 변환입니다. 정확히 `false` / `0` / `no` 토큰만 끕니다. [컴포넌트·라이선스 → 런타임 스코프 필터링](../user-guide/components-and-licenses.md#runtime-scope-filtering) 참고. |
| `SCAN_SCOPE_FILTER_MAVEN_ENABLED` | `true` | `config.py` | 스코프 필터의 Maven 부분(cdxgen scope `optional`/`excluded` 노드 제거). 프로젝트가 Maven `<optional>true</optional>` **런타임** 의존성을 쓰면 끄십시오 — cdxgen이 test scope와 똑같이 `optional`로 태깅해 함께 제거됩니다. |
| `SCAN_SCOPE_FILTER_NODE_ENABLED` | `true` | `config.py` | 스코프 필터의 npm 부분(커밋되었거나 prep 단계가 생성한 `package-lock.json`이 `dev`로 분류한 패키지 제거). lockfile에 없는 패키지는 항상 유지합니다. |
| `LICENSE_FETCH_ENABLED` | `true` | `config.py` | cdxgen 이후 라이선스 보강. cdxgen이 SPDX 라이선스를 못 준 컴포넌트(설치된 패키지가 없는 `requirements.txt` / `go.mod`에서 흔함)에 대해, 컴포넌트의 공개 레지스트리(PyPI / Maven Central / crates.io / pkg.go.dev / RubyGems / NuGet)에 purl로 선언 라이선스를 조회하고 캐시합니다 — "unknown" 라이선스 비율을 낮춥니다. 네트워크로 나가는 것은 패키지명+버전뿐(패키지 매니저가 이미 접속하는 레지스트리)이라 SCANOSS 핑거프린트 egress와 달리 기본 **켬** 입니다. **air-gapped** 배포에서는 `false` / `0` / `no`로 꺼서, 미해석 컴포넌트가 컴포넌트마다 네트워크 타임아웃을 치르지 않고 unknown으로 남게 하십시오. SBOM에 라이선스가 하나만 적혔고 그것이 빌드를 막는 경우에도 fetcher가 다시 확인합니다. 패키지 메타데이터에는 수령자가 고를 수 있는 라이선스가 여러 개 적혀 있는 일이 많은데, SBOM이 그중 첫 번째만 담으면 막을 이유가 없는 패키지가 게이트에 걸립니다. |
| `EXTERNAL_PACKAGE_LOOKUP_ENABLED` | `true` | `config.py` | deps.dev 패키지·advisory 조회(도입 전 카탈로그 검색) 기능이 외부로 나가는 호출을 만들지 정합니다. `GET /v1/external-packages`와 `GET /v1/external-advisories/{id}` 둘 다 이 값 하나로 게이팅됩니다. `LICENSE_FETCH_ENABLED`와 같은 성격입니다. 네트워크로 나가는 것은 패키지명·생태계 또는 advisory ID뿐이고 고정된 공개 호스트(`api.deps.dev`)로만 나가므로 기본 **켬**입니다. **air-gapped** 배포에서는 `false` / `0` / `no`로 꺼서 조회 진입점을 숨기고, 호출마다 타임아웃을 겪는 대신 404를 받게 하십시오. |
| `MALICIOUS_ENABLED` | `true` | `config.py` | 알려진 악성 패키지 표시. 릴리스에 함께 담긴 OSV `MAL-` 권고 스냅샷과 컴포넌트를 대조해 공유 카탈로그에 `flagged` / `clear`를 기록합니다. 전부 오프라인이라 외부 통신이 없습니다. 취약점 축이 아니므로 findings를 만들지 않고 심각도 합계에도 들어가지 않습니다. 끄면 값이 비는데, 화면은 이를 *안전*이 아니라 *확인하지 않음*으로 표시합니다. `false` / `0` / `no`만 끕니다. [컴포넌트·라이선스 → 알려진 악성 패키지](../user-guide/components-and-licenses.md#known-malicious-packages) 참고. |
| `MALICIOUS_REFRESH_ENABLED` | `false` | `config.py` | 주간 작업이 OSV 아카이브(약 274 MB)에서 악성 패키지 스냅샷을 다시 만들지 정합니다. 새로 생기는 외부 통신은 모두 그렇듯 기본값이 꺼짐입니다. 같은 작업의 재판정 부분은 외부 통신이 없어 항상 돌기 때문에, 이 값을 켜지 않아도 릴리스를 올리면 기존 행에 반영됩니다. 인터넷이 차단된 설치는 켜지 않습니다. |
| `MALICIOUS_WAIVE_MAX_DAYS` | `30` | `license_policy_service.py` | 악성 패키지 면제의 최대 수명입니다. `LICENSE_WAIVE_MAX_DAYS`보다 짧게 잡았습니다. 라이선스 면제는 결론일 수 있지만 악성 면제는 권고에 이의를 제기하는 동안 시간을 버는 장치이기 때문입니다. 값을 낮춰도 이미 작성된 면제가 짧아지지는 않습니다. |
| `MALICIOUS_SNAPSHOT_STALE_DAYS` | `60` | `config.py` | 관리자 화면이 악성 스냅샷을 오래됐다고 표시하는 기준입니다. 권고가 매일 나오므로 지원 종료 화면의 180일보다 짧습니다. |
| `EOL_ENABLED` | `true` | `config.py` | 지원 종료(EOL) 표시: endoflife.date 추적 제품 목록에 맞는 컴포넌트를 공유 카탈로그에 `eol` / `supported` / `unknown`으로 기록합니다. 완전 오프라인 — 판정은 릴리스에 포함된 스냅샷에서 나오며 외부 전송이 없습니다. 정확히 `false` / `0` / `no` 만 끕니다. [컴포넌트·라이선스 → 지원 종료 표시](../user-guide/components-and-licenses.md#end-of-life-flagging) 참고. |
| `EOL_SNAPSHOT_PATH` | *(빈 값 — 벤더 파일)* | `config.py` | endoflife.date 스냅샷 재정의. air-gapped 설치에서는 연결된 호스트에서 더 신선한 스냅샷을 만들어(`python3 scripts/refresh_eol_snapshot.py`) 마운트한 뒤 이 변수로 지정합니다. |
| `EOL_REFRESH_ENABLED` | `false` | `config.py` | 실시간 수집 opt-in: 주간 beat가 `EOL_FEED_URL_TEMPLATE`에서 신선한 라이프사이클 데이터를 내려받습니다. **기본 꺼짐** — 새로운 외부 전송이기 때문입니다. beat의 로컬 재기록 패스는 이 값과 무관하게 실행됩니다. 정확히 `true` / `1` / `yes` 토큰만 켭니다(fail-closed, SCANOSS 방식). |
| `EOL_FEED_URL_TEMPLATE` | `https://endoflife.date/api/{product}.json` | `config.py` | 실시간 수집용 제품별 API 템플릿(`{product}` 치환). 내부 미러를 지정하면 전송이 사내에 머뭅니다. |
| `EOL_REFRESH_TIMEOUT_SECONDS` | `15` | `config.py` | 실시간 수집 시 제품 요청당 HTTP 타임아웃. `[1, 120]` 범위이며, 전체 수집은 별도로 60초 wall-clock으로 제한됩니다. |
| `WORKSPACE_HOST_PATH` | `/tmp/trustedoss` | `config.py`, `docker-compose.yml` | worker에 `/workspace`로 마운트되는 호스트 디렉터리. 레포 클론 + 스캔 아티팩트(cdxgen SBOM, scancode 출력) 보관. compose 스택은 컨테이너 내에서 `/workspace`로 오버라이드합니다. |
| `ORT_RULES_PATH` | `/opt/trustedoss/ort/rules.kts` | `docker-compose.yml` | worker 내부 레거시 경로로, ORT 단계 제거 후 잔재입니다. 읽는 곳이 없고 가리키는 파일도 이제 없습니다. 라이선스 단계 분류는 `apps/backend/tasks/scan_source.py`의 `_LICENSE_CATEGORY_DEFAULTS`에서 옵니다. |
| `JSONB_ROW_SIZE_LIMIT_BYTES` | `262144` (256 KB) | `config.py` | writer가 truncate + warn하기 전 행당 JSON 바이트 상한. I-1 무한 페이로드 클래스 가드. |

## 스캔 보존(retention) {#scan-retention}

superseded·노후 스캔 스냅샷을 회수하는 자동 보존 sweep을 조정하는 키입니다. sweep은 6시간 주기 Celery beat 태스크로 실행됩니다. 전체 모델은 [스캔 보존](../admin-guide/scan-retention.md)을 참고하십시오.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` | `7` | `config.py` | superseded 스냅샷이 sweep에 회수되기 전 보존되는 일수입니다. 동일한 `(project, 정규화된 ref)` 타겟에 더 새로운 성공 스캔이 도착하면 기존 스냅샷이 superseded 됩니다. 타겟별 롤백 이력을 더 길게 유지하려면 값을 높이십시오. |
| `SCAN_RETENTION_KEEP_LAST` | `30` | `config.py` | 나이와 무관하게 **프로젝트당** 보존되는 ref-less·실패 스캔의 최소 개수입니다. sweep은 이 하한 아래로 트림하지 않습니다 — ref 타겟이 없는 ad-hoc·진단 스캔을 보호합니다. |
| `SCAN_RETENTION_MAX_AGE_DAYS` | `180` | `config.py` | hard age 상한. release가 아닌 스캔이 이보다 오래되면 해당 타겟의 live 스냅샷이라도 sweep이 회수합니다. `metadata.release` 라벨이 붙은 스캔은 예외이며 영구 보존됩니다. |

## 웹훅 수신

두 수신 엔드포인트는 공개입니다. 서명이 본문을 덮기 때문에 본문을 읽고 저장소를 찾은 뒤에야 자격을 확인할 수 있습니다. 아래 두 키가 인증 전 호출자가 소비할 수 있는 작업량을 제한합니다. [웹훅](../ci-integration/webhooks.md#요청-한도)을 보세요.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `WEBHOOK_MAX_BODY_BYTES` | `2097152`(2 MiB) | `config.py` | 이 크기를 넘는 본문은 버퍼에 담기 전에 `413`으로 거부합니다. `65536`~`26214400` 범위로 조정되며, 상한은 GitHub이 그 위로는 전송하지 않는 크기입니다. |
| `WEBHOOK_RATE_LIMIT` | `120/minute` | `config.py` | slowapi 한도 문자열이며 출발 IP를 기준으로 셉니다(서명 확인 전에 알 수 있는 신원은 IP뿐입니다). `429`는 전송 하나를 잃는 것이고 어느 Git 호스트도 4xx를 스스로 재시도하지 않으므로, 이벤트를 잃기보다 값을 올리세요. |

## WebSocket 게이트웨이

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `WEBSOCKET_MAX_CONNECTIONS_PER_USER` | `8` | `config.py` | 사용자당 동시 커넥션 상한. 모든 백엔드 프로세스가 공유하는 Redis 기반 레지스트리로 적용되므로 워커나 파드 수와 무관하게 정확합니다(스캔 상세 화면 하나가 탭 하나당 소켓 두 개를 쓰므로, 8이면 탭 네 개를 동시에 열어도 문제없습니다). 사용자를 상한 이상으로 밀어 올리는 커넥션은 받아들이고, 그 사용자의 가장 오래된 커넥션을 close code 1001(`reason="newer_connection"`)로 닫습니다. |
| `WEBSOCKET_MAX_CONNECTIONS_GLOBAL` | `500` | `config.py` | 전체 사용자를 합친 동시 커넥션 상한. 위와 같은 Redis 기반 레지스트리를 씁니다. 이 상한을 넘기게 될 커넥션은 다른 사용자의 커넥션을 대신 닫는 것이 아니라 그 자체를 거부합니다(close code 4429, `reason="capacity_at_limit"`). |
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
| `PASSWORD_RESET_CONFIRM_RATE_LIMIT` | `5/minute` | `config.py` | `POST /auth/reset-password`에 대한 IP별 slowapi 한도. 이 엔드포인트는 토큰 자체가 자격증명이라 로그인과 같은 추측 공격 표면이고, 그래서 기본값도 로그인과 같게 뒀다. |
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

## 통합 인증 (일반 OpenID Connect)

배포처 자체의 인증 제공자입니다. 목록이 아니라 하나입니다. 조직에는 인증 제공자가 하나 있고, 모든 엔드포인트를 발급자의 탐색 문서에서 읽으므로 발급자를 적는 것이 설정의 대부분입니다. `OIDC_ISSUER`를 비워 두면 SSO 버튼이 나타나지 않습니다.

| 키 | 기본값 | 읽는 곳 | 설명 |
|---|---|---|---|
| `OIDC_ISSUER` | (비어있음) | `config.py` | 발급자 URL입니다. 예: `https://login.example.com`. 반드시 `https`여야 합니다. 이 요청 하나가 나머지 모든 엔드포인트를 결정하므로 손댈 수 없어야 합니다. 문서가 지정한 엔드포인트가 발급자와 같은 호스트인지도 로그인 시점에 확인합니다. |
| `OIDC_CLIENT_ID` | (비어있음) | `config.py` | 제공자에 등록한 클라이언트 아이디입니다. |
| `OIDC_CLIENT_SECRET` | (비어있음) | `config.py` | 클라이언트 비밀입니다. 교환은 기밀 클라이언트 인가 코드 흐름입니다. |
| `OIDC_SCOPES` | `openid email profile` | `config.py` | 로그인 시 요청할 스코프입니다. `openid`는 빠뜨려도 다시 채웁니다. |
| `OIDC_GROUPS_CLAIM` | `groups` | `config.py` | 그룹 소속을 담은 userinfo 클레임입니다. 주소와 달리 고집할 표준 클레임이 없습니다. 어느 쪽이든 그룹 목록을 보증해 주는 것이 없기 때문입니다. |
| `OIDC_GROUP_ROLE_MAP` | (비어있음) | `config.py` | `그룹:등급` 쌍을 쉼표로 나열합니다. 첫 로그인 때 만들어지는 팀에서 그 사람이 받을 등급을 정합니다. 비워 두면 모두 기존 등급을 그대로 받습니다. 설정하면 어느 그룹에도 걸리지 않은 사람은 가장 낮은 등급을 받습니다. 그룹을 매핑한 배포라면 어디에도 속하지 않는다는 것 자체가 답이기 때문입니다. `super_admin`은 적어도 무시합니다. 인정하면 인증 제공자에서 그룹을 만들 수 있는 사람이 포털 관리자를 만들 수 있게 됩니다. |

주소는 표준 `email` 클레임에서 가져오고 제공자가 검증했다고 표시해야 합니다. 다른 클레임에서 읽는 설정은 두지 않았습니다. `email_verified`는 `email` 클레임만 보증하므로 다른 곳에서 가져온 주소에는 그 표시가 다른 값을 가리키게 되고, 여러 제공자에서 그 클레임은 사용자가 직접 바꿀 수 있습니다. 클레임 매핑은 제공자 쪽에서 하는 것이 제자리입니다.

발급자와 클라이언트 아이디, 비밀이 모두 있어야 하고 발급자가 https여야 구성됨으로 보고합니다. 하나라도 빠진 배포는 눌렀을 때 실패하는 버튼 대신 아무 버튼도 보이지 않습니다.

포털은 ID 토큰 서명을 검증하지 않습니다. 의도한 선택입니다. 인가 코드를 발급자의 토큰 엔드포인트와 TLS로 직접 교환하고 주체를 같은 경로의 userinfo에서 읽는데, 토큰 엔드포인트에서 곧바로 받은 토큰이라면 OpenID Connect Core §3.1.3.7이 이를 허용합니다. 대신 확인하는 것은 탐색 문서가 설정한 발급자의 것인지, 그리고 문서가 지정한 엔드포인트가 발급자 자신의 호스트인지입니다.

## 큐 적체 알림 (S6)

Compose 배포에는 오토스케일러 계층이 없습니다. 이 절의 키들은 그 대신
제품이 주는 것으로, 용량 계산식([Docker Compose — 스캔 용량](../installation/docker-compose.md#scan-capacity-sizing-and-scaling)
참고)과 짝을 이루는 신호입니다. beat 스윕이 5분마다 두 Celery
큐(`trustedoss.scan`, `trustedoss.default` — S3의 큐 분리)의 브로커 대기
길이를 재고, 한쪽이 임계값을 넘은 채 일정 시간 지속되면 Slack/Teams로
알림 하나를 보냅니다(기존 채널을 그대로 씁니다 — 새 알림 채널이
아닙니다).

`QUEUE_BACKLOG_METRICS_ENABLED`(M2)에 강하게 의존합니다: 이 스윕은 그
지표가 읽는 것과 같은 브로커 값을 읽습니다. M2를 끈 채 이 알림만 켜도
오류가 나지는 않습니다 — 매 틱마다 WARNING 로그를 남기고 건너뜁니다.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `QUEUE_BACKLOG_ALERT_ENABLED` | `false` | `config.py` | 이 알림 스윕 자체의 켬/끔. 기본은 꺼짐이고, `QUEUE_BACKLOG_METRICS_ENABLED`가 함께 켜져 있어야 실제로 의미가 있습니다. |
| `QUEUE_BACKLOG_ALERT_SCAN_QUEUE_THRESHOLD` | `10` | `config.py` | `trustedoss.scan`이 적체로 판단되기 전까지 허용하는 대기 메시지 수. 스캔 슬롯 하나가 수십 분씩 자리를 차지하므로(`scan_hard_time_limit_seconds()`), 스캔 몇 건이 밀려 있는 것은 보통 정상입니다. |
| `QUEUE_BACKLOG_ALERT_DEFAULT_QUEUE_THRESHOLD` | `100` | `config.py` | `trustedoss.default`에 대한 같은 값으로, 한 자릿수 더 큽니다. 이 큐는 알림·백업·감사 반출·카탈로그 갱신 베트처럼 짧고 잦은 작업을 나르므로(S3의 큐 분리 참고), 정상 상태라면 몇 초 안에 비웁니다. |
| `QUEUE_BACKLOG_ALERT_SUSTAIN_SECONDS` | `600` | `config.py` | 큐가 임계값을 넘은 채 몇 초를 버텨야 알림이 나가는지. 순간적인 폭주(같은 베트 틱에 몰린 웹훅 스캔 여러 건)는 장애가 아니고, 넘긴 뒤에도 이만큼 계속 그 상태면 장애입니다. |
| `QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS` | `3600` | `config.py` | 같은 큐에 대해 두 알림 사이에 두는 최소 간격. 세 시간짜리 장애라면 5분마다가 아니라 이 간격으로 알립니다. 이 간격을 넘겨서도 여전히 적체 상태면 다시 알립니다 — 한 번만 알리고 마는 방식이 아니라 반복 알림입니다. |

## 용량 부족 응답 (S7)

키 세 개입니다. 앞의 두 개는 대화형 요청이 429(`ConcurrentScanLimitExceeded`,
팀 동시 스캔 캡)를 받을 때 본문에 실릴 수 있는 `estimated_wait_seconds`
필드에만 쓰이고, 요청을 거부할지 말지에는 영향을 주지 않습니다. 세 번째는
용량 부족으로 밀려난 웹훅 스캔을 자동으로 재시도할지를 정합니다.

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `SCAN_QUEUE_SLOT_COUNT` | `2` | `config.py` | 이 배포의 스캔 큐 Celery 슬롯 수(Compose는 `WORKER_REPLICAS x CELERY_CONCURRENCY`, Helm은 `worker.scan.replicaCount x worker.scan.concurrency`). 팀 동시 스캔 캡에 걸린 429의 `estimated_wait_seconds`를 계산하는 데만 쓰입니다. 백엔드 프로세스는 실제 워커 수를 스스로 알 수 없으므로 배포 형태에 맞게 설정해야 합니다. 값이 틀려도 예상 대기 시간만 부정확해질 뿐 429 판정 자체는 바뀌지 않습니다. |
| `SCAN_AVERAGE_DURATION_SECONDS` | `1200` | `config.py` | 스캔 슬롯 하나의 평균 점유 시간(20분). 설치 가이드의 용량 계산식이 쓰는 `M` 값과 같고, `SCAN_QUEUE_SLOT_COUNT`와 함께 위 예상 대기 시간을 구하는 데 쓰입니다. 최악값인 하드 상한 3900초와는 일부러 다른 값입니다 — 하드 상한은 전형적인 소요 시간이 아니라 상한선입니다. |
| `WEBHOOK_CAPACITY_RETRY_ENABLED` | `true` | `config.py` | 팀 동시 스캔 캡이나 디스크 가드에 걸려 밀려난 웹훅 스캔을 지수 백오프를 두고 자동으로 재시도할지. 운영자가 배달을 다시 보내야만 복구되던 기존 동작 대신입니다. 이 계획에서 기본값이 켬인 유일한 토글입니다 — 끈 상태가 이전 동작을 보존하는 것이 아니라 이 단위가 고치려는 결함 그 자체이기 때문입니다. 꺼도 운영자가 배달을 수동으로 다시 보내는 경로는 그대로 남습니다. |

## 운영 데이터 보존

세 테이블은 반출 커서나 사용 여부 같은 외부 신호를 기다리지 않고, 발생
시각만 기준으로 오래된 행을 정리합니다. 매일 도는 beat 하나가 아래 기간을
지난 행을 지웁니다. 전체 모델과 각 테이블의 보존 기간을 이렇게 정한 근거는
[데이터 보존](../admin-guide/data-retention.md) 문서를 봅니다.

`AUDIT_LOG_RETENTION_DAYS`는 다른 세 키와 성격이 다릅니다. `audit_logs`는
데이터베이스 계층에서 append-only로 강제되는 테이블이라(마이그레이션
0012의 트리거), 이 값은 삭제를 직접 실행하지 않습니다. `AUDIT_EXPORT_URL`로
설정한 목적지에 이미 전달된 행 중에서, 이 값보다 오래된 것이 몇 건인지
매일 세어 로그로만 남깁니다. 실제 삭제는 여전히 두 명의 운영자가 함께
진행하는 수동 SQL 세션입니다([감사 로그 — 보존](../admin-guide/audit-log.md#retention)
참고).

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `AUDIT_LOG_RETENTION_DAYS` | `90` | `config.py` | 이미 반출된 감사 로그 행이 삭제 대상으로 잡히기까지의 나이. 위 설명대로 삭제 자체를 실행하지는 않습니다. |
| `NOTIFICATION_RETENTION_DAYS` | `180` | `tasks/operational_retention.py` | 읽음·안 읽음과 무관하게 앱 내 알림 행이 정리되기까지의 나이. |
| `WEBHOOK_DELIVERY_RETENTION_DAYS` | `90` | `tasks/operational_retention.py` | GitHub·GitLab에서 들어온 웹훅 수신 기록이 정리되기까지의 나이. 기본값을 `AUDIT_LOG_RETENTION_DAYS`와 맞췄습니다. |
| `REPORT_DOWNLOAD_RETENTION_DAYS` | `365` | `tasks/operational_retention.py` | SBOM·NOTICE·취약점 보고서를 내려받은 기록이 정리되기까지의 나이. 셋 중 가장 긴 이유는 연간 컴플라이언스 점검에서 가장 먼저 찾을 이력이기 때문입니다. |

## 백업

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `BACKUP_RETENTION_DAYS` | `7` | `scripts/backup.sh` | `scripts/backup.sh --no-prune`로 실행별 오버라이드. |
| `BACKUP_DIR` | `<repo>/backups` | `scripts/backup.sh` | 백업 스크립트가 쓰는 위치. |

## 디스크 가드

| 키 | 기본값 | 읽는 위치 | 설명 |
|---|---|---|---|
| `DISK_HARD_LIMIT_PCT` | `95.0` | `apps/backend/core/config.py` | 빨간 게이지 + 새 스캔 차단 + admin 알림. 허용 범위는 `50`~`100`이고, 범위를 벗어난 값은 가까운 경계로 조정하며 숫자가 아닌 값은 `95.0`으로 되돌립니다. 두 경우 모두 WARNING 로그를 남깁니다. |

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
