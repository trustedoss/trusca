---
id: api-overview
title: API 개요
description: REST API 표면 — 인증, 경로, 오류, 페이지네이션, 라이브 OpenAPI / Swagger UI 포인터.
sidebar_label: API 개요
sidebar_position: 3
---

# API 개요

포털은 `/api/v1`을 루트로 한 REST API를 노출합니다. 전체 OpenAPI 3.1 스키마는 FastAPI가 생성하며 `https://<your-portal>/api/docs`(Swagger UI)와 `/api/redoc`(Redoc)에서 라이브로 제공됩니다. 이 페이지는 상위 수준 오리엔테이션입니다.

:::note 대상 독자
포털과 통합하는 엔지니어 — CI 러너·파트너 도구·커스텀 대시보드. HTTP, JSON, OAuth 스타일 bearer 토큰에 익숙해야 합니다.
:::

## Base URL

```
https://<your-portal>/api/v1
```

후행 슬래시는 정규화됩니다 — `/projects`와 `/projects/` 모두 동작.

## 인증

모든 보호된 엔드포인트에서 두 인증 스킴이 허용됩니다.

### Bearer JWT (대화형 세션)

```http
Authorization: Bearer <access_token>
```

`POST /api/v1/auth/login`이 발급합니다. 30분 수명. 로그인 시 반환되는 회전 쿠키로 refresh.

### API Key (머신 클라이언트)

```http
Authorization: ApiKey tos_<prefix>_<secret>
```

편의를 위해 `Authorization: Bearer <key>`도 허용됩니다. [API keys](../admin-guide/api-keys.md) 참고.

### 익명 엔드포인트

webhook 수신기(`/webhooks/github`, `/webhooks/gitlab`)와 `GET /health`만 비인증입니다. Webhook은 요청 본문 / 헤더의 HMAC / 토큰 검증에 의존합니다.

## 오류 — RFC 7807

모든 4xx · 5xx 응답은 `Content-Type: application/problem+json`으로 다음 형태를 가집니다.

```json
{
  "type":     "https://trustedoss.io/problems/forbidden",
  "title":    "Forbidden",
  "status":   403,
  "detail":   "API key 'tos_a1b2c3d4_…' lacks required action 'scan:trigger'.",
  "instance": "/api/v1/projects/01H…/scans"
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
| `page` | `1` | 1-기반 페이지 번호. |
| `size` | `50` | 페이지 크기. 최대 200. |
| `sort` | 엔드포인트별 | 콤마 분리 `field` 또는 `-field`(내림차순). |

응답 envelope:

```json
{
  "items": [ … ],
  "page": 1,
  "size": 50,
  "total": 1273,
  "next": "/api/v1/projects?page=2&size=50",
  "prev": null
}
```

마지막 페이지에서 `next`는 `null`. 정확한 카운트가 너무 비싼 매우 큰 테이블에서는 `total`이 생략되고 `has_more: true` 불리언이 그 자리에 들어갑니다.

## 멱등성

변경 엔드포인트는 `Idempotency-Key: <uuid>`를 받아 응답을 24시간 저장합니다. 같은 키를 다시 보내면 부수 효과 없이 캐시된 응답을 반환합니다. 불안정한 네트워크에서 클라이언트 재시도에 사용하세요.

## 표면 맵

```
POST   /auth/login                           bearer 발급
POST   /auth/refresh                         회전
POST   /auth/logout

GET    /me                                   self
PATCH  /me

GET    /projects                             목록 (팀 범위)
POST   /projects
GET    /projects/{id}
PATCH  /projects/{id}
DELETE /projects/{id}
GET    /projects/{id}/sbom?format=…
GET    /projects/{id}/notice
GET    /projects/{id}/reports/components.xlsx
GET    /projects/{id}/reports/vulnerabilities.xlsx
GET    /projects/{id}/reports/compliance.pdf
GET    /projects/{id}/gate-result

GET    /projects/{id}/scans
POST   /projects/{id}/scans
GET    /scans/{id}
DELETE /scans/{id}                           취소
GET    /scans                                전역 큐 (admin)
POST   /scans/{id}/post-pr-comment

GET    /projects/{id}/components
GET    /projects/{id}/components/{component_id}
PATCH  /projects/{id}/components/{component_id}/concluded-license
GET    /projects/{id}/vulnerabilities
PATCH  /projects/{id}/vulnerabilities/{finding_id}    # VEX state
GET    /projects/{id}/licenses
GET    /projects/{id}/obligations
GET    /projects/{id}/approvals
PATCH  /approvals/{id}                       Pending → Under Review → …

GET    /api-keys
POST   /api-keys
DELETE /api-keys/{id}                        폐기

POST   /webhooks/github                      익명, HMAC
POST   /webhooks/gitlab                      익명, token

# /admin/** — super_admin 전용 (비-admin에는 404 existence-hide)
GET    /admin/users
POST   /admin/users/invite
PATCH  /admin/users/{id}
DELETE /admin/users/{id}
GET    /admin/teams
POST   /admin/teams
PATCH  /admin/teams/{id}
DELETE /admin/teams/{id}
GET    /admin/audit                          감사 로그 쿼리
GET    /admin/health                         컴포넌트 liveness
GET    /admin/disk
GET    /admin/dt/state                       breaker + heartbeat
POST   /admin/dt/probe
POST   /admin/dt/breaker/reset
POST   /admin/dt/resync
POST   /admin/dt/orphans/cleanup
```

전체 스키마(요청 본문, 응답 형태, 검증 룰)는 모든 실행 인스턴스의 `/api/docs`에 있습니다.

## WebSocket

포털은 한 개의 WebSocket 엔드포인트를 노출합니다.

```
WSS  /api/v1/scans/{id}/progress
```

같은 JWT로 인증합니다(`?token=<jwt>` 쿼리 문자열로 전달 — 브라우저는 WebSocket upgrade에 헤더를 설정할 수 없음). 메시지:

```json
{ "scan_id": "…", "stage": "resolving_vulnerabilities", "progress": 0.62, "message": "…", "ts": "…" }
```

지수 backoff로 재연결. 매 재연결 시 최신 stage를 전달해 UI가 빠르게 수렴합니다.

## OpenAPI 다운로드

```bash
curl -sS https://trustedoss.example.com/api/openapi.json > openapi.json
```

스키마는 시작 시점에 재생성됩니다. 클라이언트를 생성한다면(`openapi-generator-cli`, `openapi-typescript`) 릴리스 태그에 핀하세요.

## 레이트 리밋

- 로그인(`/auth/login`) — IP 키 5/분. 429 + `Retry-After: 60`.
- 그 외 대부분 — 사용자 키 600/분(정상 사용에는 사실상 무제한).
- API Key는 동일 사용자 단위 정책 적용.

모든 응답의 헤더에는 다음이 포함됩니다.

```
X-RateLimit-Limit:     600
X-RateLimit-Remaining: 599
X-RateLimit-Reset:     1715269200
```

`Remaining`이 0이 되면 일시 정지하고 backoff하세요.

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
