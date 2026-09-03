---
id: api-overview
title: API 개요
description: REST API 표면 — 인증, 경로, 오류, 페이지네이션, 라이브 OpenAPI / Swagger UI 포인터.
sidebar_label: API 개요
sidebar_position: 3
---

# API 개요

포털은 `/v1`을 루트로 한 REST API를 노출합니다. 전체 OpenAPI 3.1 스키마는 FastAPI가 생성하며 `https://<your-portal>/api/docs`(Swagger UI), `/api/redoc`(Redoc), `/api/openapi.json`에서 라이브로 제공됩니다. 이 페이지는 상위 수준 오리엔테이션입니다.

:::note 대상 독자
포털과 통합하는 엔지니어 — CI 러너·파트너 도구·커스텀 대시보드. HTTP, JSON, OAuth 스타일 bearer 토큰에 익숙해야 합니다.
:::

:::tip 전체 레퍼런스
이 페이지는 오리엔테이션입니다. 요청 본문·응답 스키마·검증 규칙까지 엔드포인트별로 탐색 가능한 전체 레퍼런스는 **[API 레퍼런스 (Redoc)](pathname:///reference/api)**를 참고하세요. 커밋된 OpenAPI 스냅샷에서 렌더되며 문서 사이트와 함께 배포됩니다(백엔드 구동 불필요).
:::

:::info 경로 매핑
브라우저에 보이는 경로는 `/api/...`로 시작합니다. Traefik의 `stripprefix` 미들웨어가 FastAPI로 포워딩하기 전 `/api`를 제거하므로, 백엔드 내부 마운트 지점은 `/v1/*`, `/auth/*`, `/ws/*`, `/health` 그리고 FastAPI 자체의 `/docs`, `/redoc`, `/openapi.json` 입니다. 백엔드 컨테이너 내부에서 디버깅하는 운영자는 `/api` 접두사를 떼고 호출하세요.
:::

## Base URL

```
https://<your-portal>/v1
```

후행 슬래시는 정규화됩니다 — `/projects`와 `/projects/` 모두 동작.

## 인증

모든 보호된 엔드포인트에서 두 인증 스킴이 허용됩니다. **둘 다 `Bearer` 스킴을 사용** — 별도의 `ApiKey` 스킴은 없습니다.

### Bearer JWT (대화형 세션)

```http
Authorization: Bearer <access_token>
```

`POST /v1/auth/login`이 발급합니다. 기본 30분 수명. 로그인 시 반환되는 회전 쿠키로 refresh.

### API Key (머신 클라이언트)

```http
Authorization: Bearer tos_<prefix>_<secret>
```

포털이 `tos_` 접두사를 인식해 bearer를 API Key 검증기로 라우팅합니다. [API keys](../admin-guide/api-keys.md) 참고.

### 익명 엔드포인트

다음은 JWT를 **요구하지 않습니다**.

- `GET /health` (백엔드 liveness)
- `GET /healthz` (프론트엔드 컨테이너 liveness; v1 표면 아님)
- `POST /v1/auth/register`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/forgot-password`
- `POST /v1/auth/reset-password`
- `GET  /v1/auth/oauth/{provider}/authorize`
- `GET  /v1/auth/oauth/{provider}/callback`
- `POST /v1/webhooks/github` (HMAC 인증)
- `POST /v1/webhooks/gitlab` (token 인증)

## 오류 — RFC 7807

모든 4xx · 5xx 응답은 `Content-Type: application/problem+json`으로 다음 형태를 가집니다.

```json
{
  "type":     "https://trustedoss.io/problems/forbidden",
  "title":    "Forbidden",
  "status":   403,
  "detail":   "API key 'tos_a1b2c3d4_…' lacks required action 'scan:trigger'.",
  "instance": "/v1/projects/01H…/scans"
}
```

도메인 확장은 `snake_case`이며 OpenAPI 스키마에 모델링됩니다. 잘 알려진 예시 두 가지:

| Type URI | Status | 발생 조건 |
|---|---|---|
| `…/last-super-admin` | 409 | 마지막 super-admin 강등 시도. |
| `…/disk-pressure` | 503 | 디스크가 hard limit를 넘어 새 스캔 거부. |

## 페이지네이션

형태가 하나가 아니라 셋입니다. 이 문서는 이전까지 `limit`/`offset` 한 가지만 설명했는데,
그것은 8개 엔드포인트에만 해당하고 나머지 21개에는 틀린 설명이었습니다. 호출하려는
엔드포인트가 어느 형태인지 [OpenAPI 스키마](#openapi-schema)나 아래 표에서 확인하고 쓰세요.
하나로 가정하면 안 됩니다.

| 형태 | 쿼리 파라미터 | 응답 필드 | 엔드포인트 수 |
|---|---|---|---|
| 오프셋 | `limit`, `offset` | `items`, `total`, `limit`, `offset` | 8 |
| 번호 (`page_size`) | `page`, `page_size` | `items`, `total`, `page`, `page_size` | 15 |
| 번호 (`size`) | `page`, `size` | `items`, `total`, `page`, `page_size` | 6 |

같은 형태 안에서도 기본값과 최대값이 갈리므로 값을 코드에 박지 마세요.

| 형태 | 페이지 크기 기본값 | 최대값 |
|---|---|---|
| 오프셋 | `50` | 인벤토리 엔드포인트는 `200`, 프로젝트별 엔드포인트는 `500` |
| 번호 (`page_size`) | `50`, `/v1/notifications`만 `20` | `200` |
| 번호 (`size`) | `20`, 검색은 `25`, 소스 트리는 `100` | `100`, 소스 트리만 `500` |

`page`는 1부터 시작하고 `offset`은 0부터 세는 행 오프셋입니다. 둘 다 상한이 있으며,
상한을 넘긴 요청은 조용히 잘리지 않고 거부됩니다.

`sort`는 페이지네이션과 별개이며 10개 엔드포인트가 받습니다(컴포넌트, 라이선스,
의무사항, 취약점, 컴플라이언스, 인벤토리 목록과 각각의 CSV 내보내기). 콤마로 구분한
`field` 또는 `-field`를 받고 앞의 빼기 기호가 내림차순입니다. 허용되는 필드는
엔드포인트마다 다릅니다.

오프셋 형태의 응답 envelope입니다.

```json
{
  "items": [ … ],
  "total": 1273,
  "limit": 50,
  "offset": 0
}
```

번호 형태입니다. 쿼리 파라미터가 `page`/`size`인 엔드포인트도 응답 필드는
`page_size`입니다.

```json
{
  "items": [ … ],
  "total": 1273,
  "page": 1,
  "page_size": 50
}
```

### 왜 셋이고 앞으로 어떻게 되는지 {#pagination-convergence}

설계가 아니라 누적입니다. 새 엔드포인트가 기존 형태를 따르도록 강제하는 장치가 없었고,
각각은 작성 시점에 나름 합리적이었습니다. 그래도 이것은 결함입니다. 클라이언트
라이브러리나 내보내기 스크립트가 형태마다 분기를 두어야 하고, 이 절은 그 비용을
드러내기 위한 것이지 정당화하려는 것이 아닙니다.

새 엔드포인트는 공용 스키마 하나를 쓰므로 위 숫자는 더 늘지 않습니다. 기존 29개는
의도적으로 두었습니다. TRUSCA는 1.0 이전이고
[`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md)가 마이너
릴리스에서 HTTP API가 바뀔 수 있다고 밝히고 있습니다. 형태를 통일하는 것은 파괴적
변경이므로, 여러 마이너에 걸쳐 작은 놀라움을 반복하기보다 호출자가 파괴적 변경을
예상하는 1.0.0에서 한 번에 하는 편이 맞습니다. 두 표기를 모두 받는 호환 계층도
검토했지만 채택하지 않았습니다. 지금 이 프로젝트가 제공하는 것보다 강한 안정성을
시사하게 되고, 그동안 올바르게 유지해야 할 코드 경로가 두 벌이 됩니다. 통일 작업은
[로드맵](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md)에 있습니다.

## 표면 맵

백엔드 내부 경로(Traefik이 `/api` 제거 후):

```
POST   /auth/register                        익명
POST   /auth/login                           익명, bearer 발급
POST   /auth/refresh                         익명, 회전
POST   /auth/logout
GET    /auth/me                              self
POST   /auth/forgot-password                 익명
POST   /auth/reset-password                  익명
GET    /auth/oauth/{provider}/authorize      익명
GET    /auth/oauth/{provider}/callback       익명

GET    /auth/me                              현재 사용자 정보 (auth 라우터)
GET    /v1/users/me/notification-prefs
PUT    /v1/users/me/notification-prefs
GET    /v1/users/me/oauth-identities
DELETE /v1/users/me/oauth-identities/{identity_id}   # last-OAuth + has-password 게이팅
                                                     # 409 → urn:trustedoss:problem:last-oauth-link

GET    /v1/projects                          목록 (팀 범위)
GET    /v1/projects/export.csv               목록과 동일한 행, 페이지 없이 전량 (D9)
POST   /v1/projects
GET    /v1/projects/{id}
PATCH  /v1/projects/{id}
DELETE /v1/projects/{id}
GET    /v1/projects/{id}/sbom?format=…
GET    /v1/projects/{id}/vex?format=…       openvex | cyclonedx; 결과 분류 기반 VEX
POST   /v1/projects/{id}/vex/import         VEX 문서 소비(team_admin); multipart 업로드
GET    /v1/projects/{id}/notice
GET    /v1/projects/{id}/components
GET    /v1/projects/{id}/scans
POST   /v1/projects/{id}/scans               202 Accepted; Celery 태스크 큐잉
GET    /v1/projects/{id}/vulnerabilities
GET    /v1/projects/{id}/licenses
GET    /v1/projects/{id}/licenses/export.csv  목록과 동일한 행, 페이지 없이 전량 (D9)
GET    /v1/projects/{id}/obligations
GET    /v1/projects/{id}/obligations/{obligation_id}
PUT    /v1/projects/{id}/obligations/{obligation_id}/fulfilment   # If-Match optional
DELETE /v1/projects/{id}/obligations/{obligation_id}/fulfilment
GET    /v1/projects/{id}/obligation-fulfilments
GET    /v1/projects/{id}/gate-result

GET    /v1/scans                             목록
GET    /v1/scans/{id}
POST   /v1/scans/{id}/post-pr-comment

GET    /v1/components/{component_id}

GET    /v1/license_findings/{finding_id}

GET    /v1/vulnerability_findings/{finding_id}
PATCH  /v1/vulnerability_findings/{finding_id}/status   # VEX 상태, If-Match 필수

GET    /v1/approvals
GET    /v1/approvals/{id}
POST   /v1/approvals
PATCH  /v1/approvals/{id}/transition         # If-Match 필수
DELETE /v1/approvals/{id}

GET    /metrics                                       off by default; 404 when off or on a wrong token
GET    /v1/notification-rules/org/{organization_id}   who else hears, deployment-wide
POST   /v1/notification-rules/org/{organization_id}   super_admin only
GET    /v1/notification-rules/teams/{team_id}         includes the organization's own
POST   /v1/notification-rules/teams/{team_id}         team_admin
DELETE /v1/notification-rules/{rule_id}
GET    /v1/notifications
GET    /v1/notifications/unread-count
PATCH  /v1/notifications/read-all
PATCH  /v1/notifications/{id}/read

GET    /v1/api-keys
POST   /v1/api-keys
DELETE /v1/api-keys/{id}                     폐기

POST   /v1/webhooks/github                   익명, HMAC
POST   /v1/webhooks/gitlab                   익명, token

# /v1/admin/** — super_admin 전용 (비-admin에는 404 existence-hide)
GET    /v1/admin/users
GET    /v1/admin/users/{id}
PATCH  /v1/admin/users/{id}/role
PATCH  /v1/admin/users/{id}/deactivate
PATCH  /v1/admin/users/{id}/activate
POST   /v1/admin/users/{id}/password-reset
GET    /v1/admin/teams
POST   /v1/admin/teams
GET    /v1/admin/teams/{id}
PATCH  /v1/admin/teams/{id}
DELETE /v1/admin/teams/{id}
POST   /v1/admin/teams/{id}/members
DELETE /v1/admin/teams/{id}/members/{user_id}
GET    /v1/admin/scans                       전역 큐
POST   /v1/admin/scans/{scan_id}/cancel      실행 중 스캔 취소
GET    /v1/admin/audit                       감사 로그 쿼리
GET    /v1/admin/audit/export.csv            스트리밍 CSV
GET    /v1/admin/health                      컴포넌트 liveness
GET    /v1/admin/disk
GET    /v1/admin/backup                      백업 목록
POST   /v1/admin/backup                      수동 백업 트리거
GET    /v1/admin/backup/{name}/download
POST   /v1/admin/backup/restore              업로드 + 복원 (타이핑 게이트)
DELETE /v1/admin/backup/{name}
```

전체 스키마(요청 본문, 응답 형태, 검증 룰)는 모든 실행 인스턴스의 `/api/docs`에 있습니다.

### Optimistic concurrency

상태 워크플로 도메인 행을 변경하는 엔드포인트는 행의 현재 `version` 정수를 담은 `If-Match` 요청 헤더를 받습니다(필수). `PATCH /v1/approvals/{id}/transition`과 `PATCH /v1/vulnerability_findings/{finding_id}/status` 모두 이 패턴을 사용합니다. 불일치는 `412 Precondition Failed`와 현재 버전을 포함한 Problem Details 본문을 반환합니다.

## WebSocket

포털은 한 개의 WebSocket 엔드포인트를 노출합니다.

```
WSS  /api/ws/scans/{scan_id}
```

(Traefik이 `/api`를 제거한 후 백엔드는 `/ws/scans/{scan_id}`로 처리합니다.)

인증은 쿼리 문자열이나 헤더가 아닌 클라이언트가 보내는 **첫 메시지**로 처리됩니다.

```json
{ "type": "auth", "token": "<JWT access token>" }
```

게이트웨이는 첫 프레임이 `WEBSOCKET_AUTH_TIMEOUT_SECONDS`(기본 1.0초) 안에 도착하지 않으면 코드 `1008` / 사유 `auth_timeout`으로 연결을 닫습니다.

재연결은 지수 백오프로 합니다. 재연결마다 현재 스캔 행에서 만든 초기 동기화 프레임을 한 번 받고 그다음 실시간 이벤트가 흐릅니다.

### 서버 프레임

이 하나의 소켓으로 두 종류의 프레임이 내려오며, `type`으로 구분합니다.

진행(progress) 프레임은 파이프라인이 어디까지 왔는지 알립니다.

```json
{ "type": "progress", "percent": 70, "step": "scancode", "ts": "2026-05-10T12:34:56Z" }
```

`type`이 아예 없는 프레임도 진행 프레임입니다. 이 구분자는 봉투 형식이 나온 뒤에 추가되었으므로, `{percent, step, ts}`만 보고 작성한 클라이언트도 그대로 동작합니다.

로그(log) 프레임은 스캔 도구 출력 한 줄을 담습니다.

```json
{ "type": "log", "stage": "scancode", "stream": "stderr", "line": "ERROR: no license detected in LICENSE.txt", "ts": "2026-05-10T12:34:56Z" }
```

`stage`는 그 줄을 낸 파이프라인 단계이며, 진행 프레임의 `step`과 같은 어휘를 씁니다(`cdxgen`, `scancode`, `scanoss`, `trivy` 등). `stream`은 `stdout` 아니면 `stderr` 둘 뿐입니다. 발행 측에서 다른 값은 `stdout`으로 정규화합니다. 클라이언트는 이 값으로 도구의 오류 출력을 본문 파싱 없이 색으로 구분하거나 걸러낼 수 있습니다.

도착하는 내용에는 두 가지 상한이 걸립니다. `SCAN_LOG_LINE_MAX_LEN`(기본 2000)보다 긴 줄은 잘리고, 한 스캔이 `SCAN_LOG_MAX_LINES_PER_SCAN`(기본 20000, 모든 단계가 함께 쓰는 몫)만큼 발행하고 나면 로그 프레임은 더 오지 않습니다. 따라서 로그 스트림이 끊긴 것을 스캔이 끝난 것으로 읽으면 안 됩니다. 진행 프레임은 그와 무관하게 계속 옵니다.

모르는 `type`은 프로토콜 오류가 아니라 건너뛸 프레임으로 다루세요. 포털 자체 클라이언트도 위 두 형태로 읽히지 않는 프레임은 버리고 소켓은 그대로 둡니다. 새 프레임 종류가 추가되어도 옛 클라이언트가 깨지지 않는 것이 이 규약 덕분입니다.

### 종료 코드

서버가 보내는 종료 코드 전부와 그 뜻입니다. 출처는 이 엔드포인트가 연결을 닫는 유일한 파일인 `apps/backend/api/v1/ws.py`입니다.

| 코드 | 사유 | 원인 |
|---|---|---|
| 1001 | `newer_connection` | 사용자별 연결 수 상한(`WEBSOCKET_MAX_CONNECTIONS_PER_USER`, 기본 8)을 넘어 가장 오래된 소켓이 밀려났습니다. 이 수는 모든 백엔드 프로세스가 공유하는 Redis 기반 레지스트리로 세므로, 워커나 파드 수와 무관하게 정확합니다. W4 이전에는 프로세스마다 따로 셌고 그래서 소켓이 어느 워커에 붙느냐에 따라 결과가 갈렸는데, 그 성질은 사라졌습니다. 스캔 화면 하나가 연결 두 개를 쓰므로, 탭 둘이 만드는 연결 네 개가 상한을 넘기면 여전히 먼저 연 탭이 밀려날 수 있습니다. 다만 이제는 그 여부가 운이 아니라 상한 값 하나로 결정됩니다. |
| 1008 | `auth_timeout` | `WEBSOCKET_AUTH_TIMEOUT_SECONDS` 안에 첫 프레임이 오지 않았습니다. |
| 1008 | `auth_invalid` | 토큰을 해독하지 못했거나, access 토큰이 아니거나, subject가 사용자 아이디가 아닙니다. |
| 1008 | `auth_inactive` | 계정이 비활성이거나 없습니다. |
| 1008 | `origin_rejected` | `Origin` 헤더가 `CORS_ALLOWED_ORIGINS`에 없습니다. 아래 단서를 보세요. 클라이언트는 이 코드를 보지 못합니다. |
| 1011 | `internal` | 이벤트 전달 루프에서 오류가 났습니다. 클라이언트가 ping에 응답하지 않으면 ASGI 서버도 사유 `keepalive ping timeout`으로 같은 코드를 보내므로, 이 코드는 발신자가 둘입니다. |
| 4400 | `bad_message` | 첫 프레임이 올바른 `auth` 메시지가 아닙니다. |
| 4403 | `forbidden` | 호출자가 그 스캔을 가진 팀에 속해 있지 않습니다. |
| 4404 | `scan_not_found` | URL의 아이디가 UUID가 아니거나, 그런 스캔이 없습니다. 둘 다 같은 코드로 닫힙니다. |
| 4429 | `capacity_at_limit` | 전역 연결 상한(`WEBSOCKET_MAX_CONNECTIONS_GLOBAL`, 기본 500)에 닿았습니다. 새 연결을 그대로 거부할 뿐, 이 사용자든 다른 사용자든 기존 연결을 밀어내 자리를 만들지 않습니다. 일부러 1008을 쓰지 않았습니다. 클라이언트가 1008을 세션 만료로 보고 로그아웃시키는데, 용량 거부에는 맞지 않기 때문입니다. |

이 표만으로는 알 수 없는 것이 둘 있습니다.

- `origin_rejected`는 1008로 클라이언트에 도달하지 않습니다. 핸드셰이크를 수락하기 전에 보내므로 ASGI 서버가 업그레이드 요청에 대한 HTTP 403으로 바꿉니다. 브라우저는 사유 없는 `1006`으로 보고하며, 네트워크 장애와 구분되지 않습니다.
- `1006`이 표에 없는 이유는 서버가 보내지 않기 때문입니다. 종료 프레임이 오지 않으면 브라우저가 스스로 만들어 냅니다. 연결이 끊겼거나, 기기가 잠들었거나, 프록시가 유휴 시간으로 끊었거나, 위의 origin 거부입니다. 1006에 대한 클라이언트 문구는 서버의 판단이 아니라 실패한 연결을 설명해야 합니다.

이 엔드포인트는 `1000`을 보내지 않습니다. 클라이언트가 1000을 봤다면 스스로 닫은 것입니다.

## OpenAPI 다운로드 {#openapi-schema}

```bash
curl -sS https://trustedoss.example.com/api/openapi.json > openapi.json
```

스키마는 시작 시점에 재생성됩니다. 클라이언트를 생성한다면(`openapi-generator-cli`, `openapi-typescript`) 릴리스 태그에 핀하세요.

## 레이트 리밋

- 로그인(`/auth/login`) — IP 키 5/분. 429 + `Retry-After: 60`.
- 비밀번호 재설정(`/auth/forgot-password`) — IP 키 5/분(`PASSWORD_RESET_RATE_LIMIT`로 변경 가능); 주소별 쿨다운은 `Retry-After`로 반환.

:::note
`Idempotency-Key` 요청 처리와 `X-RateLimit-*` 응답 헤더는 로드맵 항목이며 현재 릴리스에서는 구현되어 있지 않습니다.
:::

## 스캔 취소

일반 사용자는 스캔을 직접 취소할 수 없습니다. 운영자는 `POST /v1/admin/scans/{scan_id}/cancel`(super-admin 전용)로 취소합니다.

## 관측성

아웃바운드 호출에 `X-Request-ID`를 설정하세요. 포털은 응답에 echo하고 그 요청의 모든 라인에 로그합니다. 헤더가 없으면 포털이 UUIDv7을 생성해 반환합니다.

## 버전 관리

경로에 `/v1`을 포함합니다. Breaking 변경은 `/v2`로 이동. `/v1` 안에서:

- 응답에 새 옵셔널 필드 추가는 breaking이 아님.
- 요청에 새 필수 필드 추가는 새 엔드포인트 또는 feature 헤더 뒤에 게이팅.

## 함께 보기

- **[API 레퍼런스 (Redoc)](pathname:///reference/api)** — 엔드포인트별 전체 스키마, 문서와 함께 호스팅.
- 모든 설치의 `/api/docs`(Swagger UI).
- [아키텍처](./architecture.md)
- [API keys](../admin-guide/api-keys.md)
- [Webhooks](../ci-integration/webhooks.md)
