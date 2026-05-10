---
id: dt-connector
title: Dependency-Track 커넥터
description: Dependency-Track 통합 운영 — health 모니터, 회로 차단기, 취약점 캐시, 고아 정리.
sidebar_label: DT 커넥터
sidebar_position: 2
---

# Dependency-Track 커넥터

[Dependency-Track](https://dependencytrack.org/)(DT)은 포털이 SBOM을 대조하는 상위 취약점 데이터베이스입니다. 커넥터는 DT API에 없는 신뢰성 프리미티브를 추가합니다 — health 모니터링, 회로 차단기, PostgreSQL 취약점 캐시, 고아 정리.

`/admin/dt` 페이지는 커넥터의 런타임 뷰를 노출합니다 — health 상태 카드, 회로 차단기 배지, 새로고침 액션:

![Admin DT 커넥터 — breaker 배지와 새로고침 액션이 있는 상태 카드](/img/screenshots/admin-dt-status.png)

:::note 대상 독자
배포를 운영하는 `super_admin`. 이하 화면은 `/admin/dt`에 있습니다.
:::

## 왜 커넥터인가

DT는 운영상 세 가지 부류의 어려움을 겪어왔습니다.

1. **느린 시작** — 인덱스를 재구축하는 동안 DT는 콜드 스타트에서 5–10분이 걸릴 수 있습니다. 그 사이의 호출은 타임아웃합니다.
2. **고아 프로젝트** — 포털에서 프로젝트를 삭제했지만 커넥터가 DT에 flush하지 못한 경우 DT에 "유령" 프로젝트가 쌓입니다.
3. **동기화 윈도** — DT는 NVD·OSV 미러를 주기 갱신하는 동안 쓰기 작업을 거부합니다.

커넥터는 모든 DT 호출을 다음 레이어로 감싸 위 세 케이스에서도 포털을 사용 가능하게 유지합니다.

## 운영 레이어

```
포털 API 호출 → 회로 차단기 → DT health 프로브 (60초 heartbeat)
                       │
              CLOSED ──┴── OPEN ──► PostgreSQL 취약점 캐시
```

### 1. Health 모니터

Celery Beat 작업이 매 60초마다 `${DT_URL}/api/version`을 ping합니다. 연속 3회 실패 시 상태가 `healthy`에서 `degraded`로, 한 번 더 실패하면 `down`으로 전환됩니다. DT 프로브 결과는 `dt_health_check` structlog 이벤트로 발신되어 대시보드 엔드포인트와 차단기가 소비합니다 — 이벤트 스트림은 Loki / journald 에서 확인하세요(v2.0.0 에는 `dt_health` SQL 테이블이 없습니다).

**/admin/dt** 대시보드 표시:

- 현재 상태(`healthy` / `degraded` / `down`).
- 마지막 성공 프로브 타임스탬프.
- `healthy`가 아닐 때 마지막 오류 메시지.
- 프로브 이력(최근 24시간 sparkline).

상태가 `down`에 도달하면 **회로 차단기**가 OPEN되며 후속 호출은 차단기가 다시 성공적으로 프로브할 때까지 캐시된 취약점을 반환합니다. `down` 시 자동 컨테이너 재시작은 로드맵 항목입니다 — 그 전까지는 health 프로브가 몇 분 이상 빨강을 유지하면 운영자가 `docker-compose restart dt`로 DT를 재시작합니다.

### 2. 회로 차단기

:::note 회로 차단기 상태 용어
- **CLOSED** — DT 가 정상; 요청이 평소처럼 통과합니다.
- **OPEN** — 최근 프로브가 임계치 이상 실패; 포털이 DT 호출을 단락시키고
  대신 캐시된 취약점 데이터를 반환합니다. `/admin/dt` 의 DT 행이
  빨간색으로 **OPEN** 표시됩니다.
- **HALF_OPEN** — 쿨다운이 경과; 다음 프로브가 CLOSED(성공) 로 닫을지
  OPEN(실패) 으로 유지할지 결정합니다.

새로 설치한 직후에는 첫 성공 프로브가 도착할 때까지(보통 60초 이내)
차단기가 **OPEN** 상태입니다. OPEN 을 문제로 판단하기 전에 1분을
기다리세요.
:::

차단기는 3-state 머신: `CLOSED`(정상), `HALF_OPEN`(프로브), `OPEN`(거부).

- `CLOSED` — 호출 통과.
- `OPEN` — 호출이 즉시 캐시 데이터를 반환. DT 왕복 없음.
- `HALF_OPEN` — OPEN인 동안 30초마다 한 번 호출을 통과시킴. 성공 → `CLOSED`. 실패 → `OPEN`.

현재 상태는 **/admin/dt**와 `GET /v1/admin/dt/status`(응답 스키마 `DTStatusOut`)에서 확인 가능합니다.

### 3. PostgreSQL 취약점 캐시

취약점 데이터는 `vulnerabilities` 와 `vulnerability_findings` 테이블에 직접 저장됩니다 — 포털은 성공한 모든 DT 응답을 이 두 테이블에 미러합니다(CVE 메타데이터 + 스캔별 finding, 심각도·요약·수정 가용성 포함). 차단기가 OPEN 일 때 이 테이블들이 진실의 원천입니다. v2.0.0 에는 별도의 `vuln_cache` 테이블이 없습니다.

캐시는 best-effort: DT 대비 최대 1시간(동기화 간격) 지연될 수 있습니다. 포털 측 장애 중 DT가 알게 된 새 CVE는 다음 성공 동기화까지 등장하지 않습니다.

### 4. 고아 정리

매 6시간마다 Celery Beat 작업이 DT 프로젝트를 나열하고 포털 `projects` 테이블과 비교합니다.

- 매칭 포털 행이 없는 DT 프로젝트는 **고아** — DT에서 삭제(`DT_ORPHAN_AUTODELETE=false`이면 운영자 확인 필요, 프로덕션 기본).
- DT 카운터파트가 없는 포털 프로젝트는 **누락** — 다음 스캔에서 자동 생성.

고아 목록은 **/admin/dt → Orphan projects**에 표시되며 **Delete selected** 버튼이 있습니다.

## 첫 부트스트랩 (번들 DT)

번들 DT 오버레이(`docker-compose.dt.yml`)를 가동하면 API 서버가 기본 자격증명으로 시작합니다.

1. **DT 오버레이로 스택 가동:**

   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.dt.yml up -d
   ```

2. **`http://localhost:8080`** (또는 리버스 프록시의 DT 라우트) 열기.

3. **`admin / admin`으로 로그인** 후 새 비밀번호 설정.

4. **8개 OSV 생태계 활성화:**

   Administration → Vulnerability Sources → 각 생태계 활성화:
   - npm
   - Maven
   - PyPI
   - RubyGems
   - crates.io
   - Go
   - Packagist
   - NuGet

   미러 동기화는 백그라운드에서 실행됩니다. Maven은 콜드 동기화에서 ~1시간; 나머지는 각 5–15분.

5. **API Key 생성:**

   Administration → Access Management → Teams → **Automation** → API Key 복사.

6. **`.env` 연결**(커밋 금지):

   ```bash
   DT_API_KEY=<방금 복사한 키>
   ```

   영향 받는 서비스 재시작:

   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.dt.yml \
     restart backend worker beat
   ```

7. **검증** — **/admin/dt** 방문. 60초 이내에 상태가 `healthy`여야 합니다. 고아 프로젝트 목록이 비어 있어야 합니다.

## 외부 DT 연결

조직이 DT를 중앙 운영하고 포털이 그것을 가리키도록 하려면:

1. `.env`의 `DT_URL`을 외부 URL로 설정.
2. `DT_API_KEY`를 외부 DT의 Automation 팀이 발급한 키로 설정.
3. `docker-compose.dt.yml`을 가동하지 **마세요** — 로컬 DT 서비스를 끔.
4. `backend`, `worker`, `beat` 재시작.

커넥터 동작은 동일합니다. 고아 정리 작업이 본인 소유가 아닌 DT 프로젝트를 나열할 수 있으므로 — 다른 팀의 프로젝트를 삭제하지 않도록 수동 확인 정책(`DT_ORPHAN_AUTODELETE=false`)을 유지하세요.

## 수동 프로브

런북용:

```bash
# 즉시 DT health 프로브 강제(super_admin 전용)
curl -fsS -X POST \
  https://trustedoss.example.com/v1/admin/dt/health-check \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# 즉시 고아 정리 패스 트리거
curl -sS -X POST \
  https://trustedoss.example.com/v1/admin/dt/orphans/cleanup \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

두 엔드포인트 모두 `super_admin` 필요.

## 알림 {#알림}

알림 트리거는 **/notifications** 에서 설정합니다. v2.0.0 의 `kind` enum 은 6개이며 `apps/backend/models/notification.py` 와 `apps/backend/schemas/notification.py` 에 정의되어 있습니다.

| `kind` | 트리거 | 기본 |
|---|---|---|
| `scan_completed` | 스캔이 성공으로 종료 | 끔 |
| `scan_failed` | 스캔이 `failed` 상태로 종료 | 켬(team admin) |
| `cve_detected` | 기존 프로젝트에서 신규 CVE 재탐지 | 켬 |
| `license_violation` | 스캔에서 금지·조건부 라이선스 관측 | 켬(team admin) |
| `approval_pending` | 결정을 기다리는 승인 대기 컴포넌트 | 켬(team admin) |
| `policy_gate_failed` | 빌드 게이트(`POST /v1/scans/{id}/policy-gate`)가 `block` 반환 | 켬 |

채널: 이메일(SMTP), Slack Webhook, MS Teams Webhook. Webhook URL 은 `.env`(`SMTP_*`, `SLACK_WEBHOOK_URL`, `TEAMS_WEBHOOK_URL`) 에 설정.

`disk_pressure` 알림 종류는 v2.0.0 enum 에 **포함되지 않습니다** — 디스크 압박은 `/admin/disk` 에서만 노출됩니다. [디스크·health](./disk-and-health.md) 참고.

## 트러블슈팅

:::info 먼저 확인할 로그
- `docker-compose logs --tail=500 dt` — JVM 기동, OOM, fatal.
- `docker-compose logs --tail=500 backend | grep dt_health_check` — 포털이 본 DT 프로브(structlog 이벤트).
- `/admin/dt/status` API — 차단기 상태 + 직전 프로브 결과 JSON.
:::

### `/admin/dt`에 `down`인데 브라우저에서는 DT 도달 가능

포털 워커는 컴포즈 네트워크로 DT에 도달합니다. 공개 URL이 아닙니다. 확인:

```bash
docker-compose -f docker-compose.yml exec worker \
  curl -fsS http://dtrack-api:8080/api/version
```

실패하면 네트워크 설정 오류(backend와 DT가 다른 컴포즈 네트워크)입니다. 두 파일로 다시 가동:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dt.yml up -d
```

### 차단기가 OPEN에 머무름

차단기는 성공한 HALF_OPEN 프로브에서만 닫힙니다. DT가 깜빡이면 차단기가 진동합니다. 실용적인 회복 절차:

1. DT 컨테이너를 재시작해 다음 프로브가 깨끗한 DT를 보게 합니다 — `docker-compose restart dt`.
2. `POST /v1/admin/dt/health-check`로 즉시 health 프로브를 강제합니다([수동 프로브](#수동-프로브) 참고). 한 번의 녹색 프로브가 HALF_OPEN을 CLOSED로 전환합니다.
3. 최후 수단으로 운영자가 차단기를 CLOSED 로 강제 전이 — 아래 [차단기 리셋](#차단기-리셋-최후-수단) 참고.

#### 차단기 리셋 (최후 수단)

`POST /v1/admin/dt/breaker/reset`(super_admin 전용)은 쿨다운 창과 무관하게 차단기를 CLOSED 로 강제 전이하고 연속 실패 카운터를 0 으로 초기화합니다. 다음 DT 호출은 대기 없이 즉시 시도됩니다.

차단기가 이미 CLOSED 인 상태에서 호출하면 `409 Conflict` + `dt_breaker_already_closed: true` 로 거부됩니다 — 스크립트 재시도가 조용히 no-op 되지 않게 운영자가 원인을 먼저 확인하도록 강제합니다. 전이 결과(`state_before` / `state_after` / `fail_count_before`)는 감사 로그에 `target_table=dt_breaker`, `action=breaker_reset`, 호출자 user id 와 함께 기록됩니다.

관리자 → DT 커넥터 페이지의 상태 카드에 **브레이커 리셋** 버튼을 노출합니다. 버튼은 차단기가 OPEN 또는 HALF_OPEN 상태일 때만 활성화되어 백엔드의 409 계약과 일치하므로, 클릭 후 오류 토스트로 이어지지 않고 affordance 자체가 비활성화됩니다.

```bash
curl -X POST -H "Authorization: Bearer $JWT" \
  https://portal.example.com/v1/admin/dt/breaker/reset
# 200 OK
# {"state_before": "open", "state_after": "closed", "fail_count_before": 5, "reset_at": "..."}
```

### DT 회복 후 취약점이 갱신되지 않음

기존 스캔에 대해 상관관계를 재실행하는 경로는 시간 단위 Celery Beat 동기화 작업입니다. v2.0.0 에는 수동 동기화 HTTP 엔드포인트가 없습니다 — 다음 시간 틱을 기다리거나, 기다릴 수 없다면 워커를 재시작해 주기 스케줄이 즉시 재평가되게 하세요.

```bash
docker-compose -f docker-compose.yml restart worker beat
```

동기화는 멱등 — 두 번 실행해도 동일 결과. 1급 수동 동기화 엔드포인트는 로드맵 항목입니다.

### "고아 목록에 모르는 프로젝트가 표시됨"

공유 외부 DT를 가리키는 중입니다. 다른 팀이 거기에 프로젝트를 만들었을 수 있습니다. `DT_ORPHAN_AUTODELETE=false`(기본) 유지하고 본인 소유 고아만 삭제하세요.

## 로드맵 (v2.x)

다음 운영자용 기능들은 초기 문서에 언급되었으나 v2.0.0 에는 **반영되지 않았습니다**.

- Health 모니터가 `down`으로 전환될 때 자동 `docker restart dt` 시도.
- 운영자용 수동 동기화 엔드포인트(`POST /v1/admin/dt/resync`); Celery Beat 의 시간 단위 동기화 작업 자체는 출하되어 동작합니다.

## 함께 보기

- [시스템 health 대시보드](./disk-and-health.md)
- [백업·복원](./backup-and-restore.md)
- [아키텍처 — DT 통합](../reference/architecture.md#dependency-track)
