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

목록 엔드포인트는 다음을 받습니다.

| 쿼리 파라미터 | 기본값 | 설명 |
|---|---|---|
| `limit` | `50` | 페이지 크기. 최대 200. |
| `offset` | `0` | 0-기반 행 오프셋. |
| `sort` | 엔드포인트별 | 콤마 분리 `field` 또는 `-field`(내림차순). |

응답 envelope:

```json
{
  "items": [ … ],
  "total": 1273,
  "limit": 50,
  "offset": 0
}
```

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
POST   /v1/projects
GET    /v1/projects/{id}
PATCH  /v1/projects/{id}
DELETE /v1/projects/{id}
GET    /v1/projects/{id}/sbom?format=…
GET    /v1/projects/{id}/notice
GET    /v1/projects/{id}/components
GET    /v1/projects/{id}/scans
POST   /v1/projects/{id}/scans               202 Accepted; Celery 태스크 큐잉
GET    /v1/projects/{id}/vulnerabilities
GET    /v1/projects/{id}/licenses
GET    /v1/projects/{id}/obligations
GET    /v1/projects/{id}/obligations/{obligation_id}
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
GET    /v1/admin/dt/status                   breaker + heartbeat 스냅샷
POST   /v1/admin/dt/health-check             강제 probe
POST   /v1/admin/dt/breaker/reset            최후의 복구 수단
GET    /v1/admin/dt/orphans
POST   /v1/admin/dt/orphans/cleanup          정리 태스크 큐잉
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

게이트웨이는 첫 프레임이 `WEBSOCKET_AUTH_TIMEOUT_SECONDS`(기본 1.0초) 내 도착하지 않으면 코드 `1008` / reason `auth_timeout`으로 닫습니다. 이후 서버 프레임은 진행 이벤트를 담습니다.

```json
{ "percent": 70, "step": "dt_upload", "ts": "2026-05-10T12:34:56Z" }
```

지수 backoff로 재연결. 매 재연결 시 라이브 이벤트가 흐르기 전 현재 스캔 행에서 한 번의 초기 동기화 프레임을 받습니다.

사용자당 동시 커넥션은 `WEBSOCKET_MAX_CONNECTIONS_PER_USER`(기본 3)로 제한되며, 4번째 커넥션이 가장 오래된 것을 코드 1001(`reason="newer_connection"`)로 evict합니다.

## OpenAPI 다운로드

```bash
curl -sS https://trustedoss.example.com/api/openapi.json > openapi.json
```

스키마는 시작 시점에 재생성됩니다. 클라이언트를 생성한다면(`openapi-generator-cli`, `openapi-typescript`) 릴리스 태그에 핀하세요.

## 레이트 리밋

- 로그인(`/auth/login`) — IP 키 5/분. 429 + `Retry-After: 60`.
- 비밀번호 재설정(`/auth/forgot-password`) — IP 키 5/분(`PASSWORD_RESET_RATE_LIMIT`로 변경 가능); 주소별 쿨다운은 `Retry-After`로 반환.

:::note
`Idempotency-Key` 요청 처리와 `X-RateLimit-*` 응답 헤더는 로드맵 항목이며 v2.0.0에서는 구현되어 있지 않습니다.
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

- 모든 설치의 `/api/docs`(Swagger UI).
- [아키텍처](./architecture.md)
- [API keys](../admin-guide/api-keys.md)
- [Webhooks](../ci-integration/webhooks.md)
