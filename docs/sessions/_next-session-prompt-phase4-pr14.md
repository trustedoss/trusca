Phase 4 (알림 시스템) PR #14 — Notification 도메인 + REST API + 권한.

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = 53be9ba (chore PR #7 squash merge). 누적 머지: PR #1~#11 + chore CI fix 4건 + chore PR #1~#7. Phase 3 (PR #10~#13) 완료. **Phase 4 첫 PR 진입**.

이번 세션 = Phase 4 PR #14 — 알림 시스템 모델 + REST API + 권한 (워커 / 룰 엔진 / UI 는 별도 PR).

직전 핸드오프 (반드시 시작 시 읽기):
  - `docs/sessions/2026-05-07-chore-pr7-maven-url-uat-revalidation.md` — 직전 세션. **§5 의 "adversarial input 결함" + "UAT 와 단위 테스트의 functional gap" 교훈 반드시 흡수**. 본 PR 의 untrusted input 파싱 (예: NotificationChannel.config JSONB 에 들어오는 webhook URL / Slack token / Teams URL) 에 동일 위험.
  - `docs/sessions/2026-05-07-chore-pr6-cdxgen-ort-env-scrub.md` — chore PR #6 핸드오프 (subprocess env scrub 패턴 — Notification 발송 worker 가 SMTP / webhook 호출할 때도 동일 원칙 적용).
  - `docs/sessions/2026-05-07-chore-pr5-security-license-fetcher.md` §6 옵션 A — Phase 4 PR #14 의 원본 시작 지시문. 본 prompt 가 그 옵션 A 의 정식 발췌.
  - `docs/sessions/2026-05-07-phase3-pr13-obligations.md` — Phase 3 종결 핸드오프.
  - `CLAUDE.md` "주요 기능 / 거버넌스 / 운영" 의 알림 시스템 절.

시작 시 검증 (반드시):
  ```
  docker-compose -f docker-compose.dev.yml ps               # 6/6 healthy (celery-beat 포함; backend healthcheck 잔여 issue 는 무시 OK)
  docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml ps  # +dtrack-api healthy
  gh run list --limit 3                                      # main 최신 success (53be9ba)
  git status                                                 # working tree 검증 (untracked 제외 클린)
  ```

  중요 — main 의 working tree 잔여:
  - `docs/sessions/_next-session-prompt-chore-pr6-pr7.md` (untracked, archive 로 이동 완료된 prompt 의 **혹시 아직 있으면** archive 로 이동 + 별도 commit). 현재 상태는 본 prompt 첫 commit 으로 archive 완료.
  - `docs/sessions/_next-session-prompt-phase4-pr14.md` (본 prompt) — 세션 종료 시 archive 로 이동.
  - `.claude/scheduled_tasks.lock` — 무시.
  - `docs/review-binaryanalysis-ng.md` — 사용자 작업 중 doc, 무시.

브랜치 전략:
  - `feature/phase4-pr14-notification-domain` 생성. PR #14 만 처리. 워커 / 룰 엔진 / UI 는 PR #15~#18 별도 세션.

═══════════════════════════════════════════════════════════════
Phase 4 PR #14 작업 내용
═══════════════════════════════════════════════════════════════

## 1. 모델 (db-designer)

새 도메인 3개. Alembic 0006 (forward-only, schema only — 데이터 시드는 별도 revision):

### 1.1 `notifications` (이벤트당 1행, 사용자 단위 raw)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | uuid PK | UUIDv7 |
| `user_id` | uuid FK → users.id | 수신자 |
| `team_id` | uuid FK → teams.id NULL | 이벤트 발생 팀 (전사 알림은 NULL) |
| `event_type` | text | `vulnerability.detected` / `scan.completed` / `license.violation` 등 dotted 명명 |
| `severity` | enum (`info` / `warning` / `critical`) | UI badge color |
| `title` | text NOT NULL | 1줄 요약 (i18n 시 EN/KO 키 변환) |
| `payload` | jsonb NOT NULL | 이벤트 본문 (CVE id / scan id / license id / link 등) |
| `read_at` | timestamptz NULL | 읽은 시각 (NULL = 안 읽음) |
| `created_at` | timestamptz NOT NULL DEFAULT now() | 발송 시각 |

Index: `ix_notifications_user_read` partial on `(user_id, created_at DESC) WHERE read_at IS NULL` — unread-count + 안 읽음 목록 빠른 path.

### 1.2 `notification_channels` (team-scoped 발송 채널)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | uuid PK |
| `team_id` | uuid FK → teams.id NOT NULL |
| `type` | enum (`email` / `slack_webhook` / `teams_webhook` / `generic_webhook`) |
| `name` | text NOT NULL | "Eng Slack", "Sec Email" 등 |
| `config` | jsonb NOT NULL | type 별 dispatcher payload — **adversarial input 검증 필수** (URL scheme / 길이 / 호스트 화이트리스트) |
| `enabled` | bool NOT NULL DEFAULT true |
| `last_success_at` | timestamptz NULL |
| `last_failure_at` | timestamptz NULL |
| `last_error` | text NULL | 마지막 실패 메시지 (truncated 1KB) |
| `created_at` / `updated_at` | timestamptz | std |

Unique: `(team_id, name)` — 한 팀에 같은 이름 채널 중복 금지.

### 1.3 `notification_preferences` (user-scoped 라우팅 룰)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | uuid PK |
| `user_id` | uuid FK → users.id NOT NULL |
| `event_type` | text NOT NULL | 매칭할 이벤트. `*` 으로 와일드카드, `vulnerability.*` 으로 prefix 매칭 |
| `min_severity` | enum (`info` / `warning` / `critical`) NOT NULL DEFAULT `info` | 이 이상 severity 만 |
| `channel_ids` | uuid[] NOT NULL | NotificationChannel 의 PK 배열. 배열이 비면 in-app only |
| `frequency` | enum (`immediate` / `digest_daily` / `digest_weekly`) NOT NULL DEFAULT `immediate` |
| `enabled` | bool NOT NULL DEFAULT true |
| `created_at` / `updated_at` | timestamptz |

Unique: `(user_id, event_type)` — 사용자당 같은 이벤트 매칭 룰 중복 금지.

## 2. API (backend-developer)

새 endpoint 6:

| Method | Path | Owner | RBAC |
|--------|------|-------|------|
| POST | `/api/v1/notifications/preferences` | 일반 사용자 (본인) | authenticated |
| GET | `/api/v1/notifications/preferences` | 일반 사용자 (본인) | authenticated |
| GET | `/api/v1/notifications` | 일반 사용자 (본인) | authenticated, page/page_size, ?unread=true 필터 |
| PATCH | `/api/v1/notifications/{id}/read` | 일반 사용자 (본인) | authenticated, IDOR 방지 |
| GET | `/api/v1/notifications/unread-count` | 일반 사용자 (본인) | authenticated |
| POST | `/api/v1/admin/notification-channels` | Team Admin / Super Admin | `assert_team_access` 패턴 |

### 2.1 권한
- 일반 사용자: 본인 NotificationPreference 만 CRUD, 본인 Notification 만 조회 (id mismatch → 404 not 403).
- Team Admin: 팀 NotificationChannel CRUD (assert_team_access).
- Super Admin: 전사 channel + audit log.

### 2.2 RFC 7807 Problem Details
- `application/problem+json` 응답 모든 4xx/5xx.
- 도메인 확장 필드: `event_type` / `channel_id` 등 snake_case.

### 2.3 adversarial input 검증 (chore PR #7 교훈)
- `NotificationChannel.config.url` (slack/teams/generic webhook) — URL scheme allowlist (`https://` only), 호스트 정규화 (lowercased), 길이 제한 (2048).
- `NotificationChannel.config.token` (slack bot token 등) — 마스킹 (`mask_pii`) 후 audit 로그에만 평문 저장 안 함.
- `NotificationPreference.event_type` 가 와일드카드일 때 `_LICENSE_FETCHER_NORMALIZE_SPDX_ID` 류의 자기참조 / 무한 매칭 위험 차단.
- 모든 free-text payload 는 길이 한도 (title 200, payload 16KB).

## 3. 단위 + IDOR 테스트 (test-writer)

- **단위**: 모델 시리얼라이저 / Pydantic 스키마 / event_type prefix 매칭 / severity ≥ filter / channel_ids 배열 검증.
- **IDOR 회귀** (chore PR #5/#7 패턴): 다른 user 의 NotificationPreference 접근 → 404. 다른 team 의 NotificationChannel 접근 → 404.
- **adversarial parametrize**: webhook URL 에 `javascript:` / `file://` / `http://` (HTTPS 강제 검증) / oversized (> 2048) / null bytes / CRLF (header injection 우회 시도). 모두 422 problem-details.
- **페이지네이션**: 20-page boundary / page_size cap / 정렬 안정성.

## 4. security-reviewer Producer-Reviewer

scope:
- RBAC / IDOR (assert_team_access 패턴 일관성)
- payload PII (NotificationChannel.config, Notification.payload 의 secret 노출 위험)
- adversarial input (URL / event_type / payload — chore PR #7 교훈 반영)
- audit 로그가 secret 평문 저장 안 함 (mask_pii)
- forward-only migration 의 멱등성

## 5. DoD

- lint/typecheck clean (ruff + mypy)
- 단위 ≥ 80% line coverage on changed code
- IDOR 회귀 8+ 케이스
- adversarial input parametrize 10+ 케이스
- security-reviewer PASS (M1-or-higher 0건)
- PR open + CI 9/9 + squash merge
- 핸드오프 `docs/sessions/2026-05-XX-phase4-pr14-notification-domain.md`

## 6. 예상 변경

- 신규: 3 모델 + 6 endpoint + 1 service + 1 schema 파일 + 1 migration ≈ 800 LoC + 단위/통합 테스트 ≈ 600 LoC.
- 수정: `core/exception_handlers` 의 새 도메인 에러 매핑, `audit` listener 의 새 도메인 등록.
- commit 4~6건 (logical 단위): (1) migration + 모델, (2) service + RBAC helper, (3) endpoint, (4) 스키마, (5) 테스트, (6) audit 통합.

## 7. 주의·블로커

- **scan_service `_can_access_team` 마이그레이션** — chore PR #5 carry-over. 본 세션 처리 안 함. 다음 chore PR 후보.
- **chore PR #8 backlog**: B1~B6 (UAT v2 doc §6 + chore PR #7 핸드오프 §5).
- **license fetcher cache 비대화 (L3) / batch budget (L2)** — Phase 4 진입 후에도 모니터링 지속.
- **NotificationChannel webhook 호출 자체는 PR #15** — 본 PR 은 모델 + API only. PR #15 에서 chore PR #6 의 `_subprocess_env` 패턴 (httpx.Client config 의 minimal env, 마스킹된 secret) 동일 원칙 적용.
- **deploy ordering** — Phase 4 의 schema migration 0006 은 worker / backend rollout 과 어떤 순서로 적용할지 PR-level 에서 고려. 본 PR 은 worker 가 새 도메인 안 건들기 때문에 ordering 자유.

═══════════════════════════════════════════════════════════════
공통 설계 제약
═══════════════════════════════════════════════════════════════

- PostgreSQL only / Alembic forward-only / 인증 필수 / docker-compose V1 / `os.getenv()` 런타임 호출 / docker image `:latest` 금지.
- VEX 상태 enum 7-state 보존.
- Optimistic concurrency 패턴 (`if_match` echo + `SELECT FOR UPDATE`) 보존.
- 새 endpoint 6 / 새 도메인 3 / schema 변경 1건 (3 테이블 +) — Phase 4 의 의도된 surface.
- 핵심 보안 코드 (RBAC / IDOR / webhook URL 검증) 는 Producer-Reviewer 패스 필수.
- 사용자가 직접 push / merge 권한 보유 (chore PR #5 settings.json 정책). force-push 는 명시 승인 필요.
- **adversarial input parametrize 필수** (chore PR #7 교훈).

═══════════════════════════════════════════════════════════════
세션 종료 시
═══════════════════════════════════════════════════════════════

1. `docs/sessions/2026-05-XX-phase4-pr14-notification-domain.md` 작성 (`docs/v2-execution-plan.md` §7 양식).
2. `docs/sessions/_next-session-prompt-phase4-pr14.md` (본 prompt) 를 archive 로 이동.
3. 다음 세션 prompt 작성: **PR #15 (Notification 발송 worker)** — Celery task + SMTP / Slack / Teams adapter + retry-with-backoff + 멱등 키.

본 세션 작업량: 단일 세션 내 처리 가능 (4~6 시간 예상). 백그라운드 에이전트 활용 권고.
