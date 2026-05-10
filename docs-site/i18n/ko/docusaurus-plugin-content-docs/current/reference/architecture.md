---
id: architecture
title: 아키텍처
description: TrustedOSS Portal 아키텍처 — 서비스, 데이터 흐름, 스캔 파이프라인, ORT 룰, DT 통합, 운영 프리미티브.
sidebar_label: 아키텍처
sidebar_position: 1
---

# 아키텍처

이 페이지는 TrustedOSS Portal이 내부적으로 어떻게 연결되어 있는지 설명합니다. 포털을 확장하거나 기존 플랫폼에 통합하거나 사내 아키텍처 리뷰에 비교하려고 한다면 여기서 시작하세요.

:::note 대상 독자
아키텍트, 플랫폼 엔지니어, 보안 리뷰어. FastAPI, PostgreSQL, Celery, Docker에 익숙해야 합니다.
:::

## 서비스

프로덕션 스택은 7개의 컨테이너 서비스(+선택적 8번째 — Dependency-Track)를 실행합니다.

| 서비스 | 이미지 | 역할 |
|---|---|---|
| `traefik` | `traefik:v3.2.1` | 엣지 프록시. Let's Encrypt HTTP-01로 TLS 종료. HTTP→HTTPS 리다이렉트. |
| `postgres` | `postgres:17.2-alpine` | 주 저장소. 모든 영구 상태. |
| `redis` | `redis:7.4-alpine` | Celery 브로커 + 결과 백엔드. WebSocket pub/sub. |
| `backend` | `trustedoss/backend:<tag>` | FastAPI + uvicorn(4 workers). Traefik이 `/api`, `/health`로 라우팅. |
| `worker` | `trustedoss/backend-worker:<tag>` | `cdxgen`, ORT, Trivy, JRE가 번들된 Celery worker. |
| `beat` | `trustedoss/backend-worker:<tag>` | Celery Beat 스케줄러. DT heartbeat(60초), DT resync(1시간), 고아 정리(6시간), 백업(매일). |
| `frontend` | `trustedoss/frontend:<tag>` | Vite 빌드를 nginx로 서비스. Traefik이 `/`로 라우팅. |
| `dt` (overlay) | `dependencytrack/apiserver:4.13.2` | 선택적 번들 Dependency-Track. `docker-compose.dt.yml`로 기동. |

이미지 태그는 핀되어 있습니다(CLAUDE.md 규칙 #9 — `:latest` 절대 금지).

:::note
`/metrics` 라우트는 Traefik 레벨(`docker-compose.yml`)에서 예약되어 있지만 v2.0.0에는 백엔드 핸들러가 마운트되어 있지 않습니다. Prometheus exporter는 GA 이후 로드맵 항목입니다.
:::

## 네트워크

```
                       :80 / :443
                          │
                       ┌──────────┐
                       │ Traefik  │  TLS 종료, HTTP→HTTPS
                       └────┬─────┘
                            │ trustedoss network (bridge)
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
       ┌──────────┐  ┌──────────┐  ┌──────────┐
       │ frontend │  │ backend  │  │ DT (opt) │
       └──────────┘  └────┬─────┘  └──────────┘
                          │
            ┌─────────────┼─────────────┬──────────┐
            ↓             ↓             ↓          ↓
      ┌──────────┐  ┌──────────┐  ┌──────────┐ ┌──────┐
      │ postgres │  │  redis   │  │  worker  │ │ beat │
      └──────────┘  └──────────┘  └──────────┘ └──────┘
                            └──── 공유 `workspace` 볼륨 ────┘
```

호스트에 포트를 노출하는 것은 `traefik`(`80`, `443`)뿐입니다. 다른 모든 서비스는 compose 네트워크 내부에서만 접근 가능합니다.

## 데이터 레이아웃

PostgreSQL이 단일 진실 저장소입니다. 주요 테이블:

| 테이블 | 용도 |
|---|---|
| `users`, `teams`, `team_memberships` | 신원 + RBAC. |
| `api_keys` | 서비스 계정 자격증명(bcrypt 해시). |
| `projects` | 프로젝트당 한 행. 스캔·컴포넌트·발견을 소유. |
| `scans` | 스캔 라이프사이클 레코드(queued → terminal). |
| `components`, `component_licenses` | 스캔별 SBOM 행 + 라이선스 귀속. |
| `vuln_findings` | VEX 상태 + justification 포함 CVE. |
| `vuln_cache` | 오프라인 / breaker-OPEN 읽기를 위한 DT 미러 캐시. |
| `obligations`, `obligation_kinds` | 컴포넌트별 라이선스 의무사항. |
| `approvals` | 조건부 라이선스 승인 워크플로. |
| `audit_log` | 추가 전용 쓰기 이력. CHECK 제약으로 immutable. |
| `dt_health` | DT heartbeat 결과(최근 24시간). |
| `webhook_deliveries` | 멱등성을 위한 `(source, delivery_id)`. |
| `notifications` | 아웃바운드 알림 로그 + dedup 키. |
| `backups` | 백업 manifest 이력(애플리케이션은 read-only). |

마이그레이션은 forward-only Alembic. 스키마와 데이터 마이그레이션은 별도 revision에 분리됩니다.

## 스캔 파이프라인

스캔은 Celery 태스크 체인입니다. 소스 스캔 단계(`apps/backend/tasks/scan_source.py` 참고):

```
1. bootstrap     (workspace 셋업, 프로젝트별 lock 획득)
2. fetch         (git clone / fetch / checkout)
3. prep          (workspace 레이아웃, ORT analyzer 구성)
4. cdxgen        (cdxgen → CycloneDX SBOM)
5. ort           (ORT가 SBOM을 소비, 발견 + 의무사항 emit)
6. dt_upload     (CycloneDX SBOM을 Dependency-Track에 업로드)
7. dt_findings   (DT 상관 OR breaker OPEN 시 캐시 fallback)
8. finalize      (스캔당 단일 트랜잭션으로 PostgreSQL에 기록)
```

컨테이너 스캔 단계(`apps/backend/tasks/scan_container.py` 참고):

```
1. bootstrap
2. trivy         (OS 패키지 CVE 탐지)
3. persist       (PostgreSQL에 발견 기록)
4. finalize
```

단계 전환은 WebSocket 이벤트(`scan.<id>.progress`)를 emit해 UI가 실시간으로 업데이트됩니다. 완료 시 적절한 알림 트리거가 발사됩니다.

## ORT 룰 {#ort-rules}

라이선스 분류는 룰 기반입니다. 룰은 `ort/rules.kts`에 있으며 worker에 read-only로 마운트됩니다.

```kotlin
// 발췌 — 표준 버전은 ort/rules.kts 참고.
val forbidden = setOf(
    "AGPL-3.0-only", "AGPL-3.0-or-later",
    "GPL-2.0-only",  "GPL-2.0-or-later",
    "GPL-3.0-only",  "GPL-3.0-or-later",
    "SSPL-1.0",      "BUSL-1.1",
)

val conditional = setOf(
    "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0-only", "LGPL-3.0-or-later",
    "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0",
)

// 허용 = 인식되는 SPDX 식별자 중 그 외; 알 수 없는 표현식은
// `Unknown`으로 표면화되어 사람의 검토가 필요.
```

룰 편집은 지원됩니다. 편집 후:

1. worker 재시작(`docker-compose restart worker beat`).
2. 영향받는 프로젝트를 재스캔해 새 분류 적용.

포털은 과거 스캔을 자동 재분류하지 않습니다 — 과거 기록은 스캔 시점에 유효했던 룰과 함께 보존됩니다.

## Dependency-Track 통합 {#dependency-track}

DT 커넥터는 단순 HTTP 클라이언트 이상입니다. 다음을 추가합니다.

- **Health monitor**(60초 heartbeat) — DT 상태를 `/admin/dt`에 표면화.
- **Circuit breaker**(CLOSED / HALF_OPEN / OPEN) — DT 장애에서 worker 보호.
- **PostgreSQL 취약점 캐시** — breaker OPEN 시 read fallback.
- **고아 정리**(6시간마다) — 포털의 프로젝트 목록을 DT와 reconcile.
- **Forward-resync**(1시간마다) — 새 CVE를 기존 스캔과 재상관.

운영 상세는 [DT connector](../admin-guide/dt-connector.md)를 참고.

## 인증 & 세션

- **비밀번호** — bcrypt cost 12, NIST 800-63B 차단 사전, 12자 이상, PII 재사용 금지.
- **Access token** — JWT, 30분 수명, `HS256` 서명(대칭, `SECRET_KEY`), 인앱 메모리 전용.
- **Refresh token** — 7일 수명, **회전 + 재사용 탐지**. HttpOnly + Secure + SameSite=Lax 쿠키.
- **API Key** — `tos_<prefix>_<secret>`은 `Authorization: Bearer …` 로 허용. bcrypt 해시 — 전체 Key는 생성 시 1회 표시.
- **CSRF 자세** — SPA는 bearer 토큰을 사용(구조상 CSRF 면역). refresh 쿠키는 HttpOnly + Secure + SameSite=Lax 로 별도의 CSRF 토큰 없이 cross-site POST 공격 클래스를 차단합니다. v2.0.0에는 별도 CSRF 토큰 엔드포인트가 없습니다.
- **레이트 리밋** — 로그인과 forgot-password에 IP 키 5/분, 429 + `Retry-After`. 비밀번호 재설정 이메일에는 주소별 쿨다운.

## 권한 (RBAC)

`super_admin`(조직), `team_admin`(팀별), `developer`(팀별). [사용자 및 팀 → 역할](../admin-guide/users-and-teams.md#역할) 참고.

요청의 effective role은 `(user, target_team)`에서 도출됩니다. 팀 간 API 호출은 403.

Admin 엔드포인트는 추가로 **404-existence-hide** 패턴(존재 은닉)을 사용합니다 — `developer`가 admin URL에 접근하면 403이 아닌 404를 받아 URL 표면을 열거할 수 없습니다.

## 오류 — RFC 7807

모든 4xx · 5xx 응답은 `application/problem+json`을 사용합니다.

```json
{
  "type":     "https://trustedoss.io/problems/last-super-admin",
  "title":    "Cannot demote the last super_admin",
  "status":   409,
  "detail":   "At least one super_admin must remain in the organization.",
  "instance": "/api/v1/admin/users/01H…/role"
}
```

도메인 특화 확장은 `snake_case`이며 OpenAPI에 모델링됩니다.

## 로깅

`structlog` JSON 라인, 한 라인 한 이벤트. 미들웨어가 `request_id`(`X-Request-ID` 또는 UUIDv7), `user_id`, `team_id`, (Celery에서) `task_id`를 시드합니다. PII는 emit 전 `mask_pii` 헬퍼로 마스킹됩니다 — 비밀번호·토큰·API Key·전체 이메일 주소는 절대 로그에 나타나지 않습니다.

## 관측성

기본 제공:

- **로그** — `docker-compose logs <service>`(구조화 JSON, `structlog`).
- **Health** — `/health`(backend), `/healthz`(frontend 컨테이너), 운영자 대시보드용 `/admin/health` UI.
- **Metrics** — 기본 서비스-헬스 메트릭은 Traefik 액세스 로그를 통해 제공됩니다. Prometheus exporter가 있는 백엔드 `/metrics` 엔드포인트는 GA 이후 로드맵 항목입니다.

OpenTelemetry tracing exporter와 번들 Jaeger 오버레이는 GA 이후 로드맵(Phase B) 항목이며 — v2.0.0에는 `docker-compose.tracing.yml` 파일이 없습니다.

## 배포 토폴로지

표준 배포는 **단일 호스트 docker-compose** 설치입니다. 두 가지 변형을 지원합니다.

- **번들 DT가 있는 단일 호스트** — `docker-compose.dt.yml` 추가. DT가 함께 동작.
- **외부 DT가 있는 단일 호스트** — DT를 끄고 `DT_URL`을 외부 인스턴스로 지정.

**Helm chart**는 Phase B(GA 이후)에 도착합니다. 다음을 추가:

- 컴포넌트별 HPA(worker는 큐 깊이로 스케일).
- PVC가 있는 PostgreSQL StatefulSet.
- Prometheus operator용 ServiceMonitor.
- TLS용 Ingress + cert-manager.

다중 호스트 docker-compose(예: 별도 머신의 worker)는 기술적으로는 가능하지만 지원 경로가 아닙니다 — 그 규모에는 Helm chart를 사용하세요.

## 백업 모델

데이터베이스는 `pg_dump --clean --if-exists | gzip`, workspace는 `tar.gz`, 그리고 Alembic head를 담은 manifest. 전체 절차는 [백업·복원](../admin-guide/backup-and-restore.md) 참고.

## 보안 자세 요약

- Apache-2.0 라이선스. GA 시점에 SBOM 발행.
- Phase 8에서 OWASP Top 10 리뷰(`security-reviewer` 에이전트 + 수동 감사).
- 의존성은 `pip-tools`(backend)와 `package-lock.json`(frontend)으로 핀. CI에서 `pip-audit`와 `npm audit` 실행.
- 모든 이미지 빌드에 Trivy 스캔.
- 프로덕션 TLS 전용(Traefik이 HTTPS 강제).
- 비밀값은 절대 로그에 남기지 않음. `mask_pii`는 테스트 fixture로 강제.

## 함께 보기

- [환경 변수](./env-variables.md)
- [API 개요](./api-overview.md)
- [DT 커넥터](../admin-guide/dt-connector.md)
- [용어집](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/glossary.md)
