# Session Handoff — 2026-05-07 — chore PR #5 — Security follow-up + License fetcher + assert_team_access migration

## 1. 무엇을 했나

`feature/chore-pr5-security-license-fetcher` 브랜치 생성 + 11 commit + security-reviewer 1 라운드 (PASS with conditions) + PR #9 open. 본 PR 은 chore PR #4 의 보안 후속(Medium #1) + UAT 매트릭스의 license-coverage 보강 + chore PR #3 의 carry-over (assert_team_access) + chore PR #4 의 carry-over (dt_resync 가드 / Trivy 5 HIGH / Tabs swap / settings.json) 일괄 정리. **새 endpoint 0건 / 새 도메인 0건 / schema +1건 (license_fetch_cache, forward-only)**. Phase 4 (알림) 진입 직전 안정화 라스트 마일.

### 1.1 Commit 11개 구성 (`git log --oneline main..HEAD`)

1. `e79facc` **chore(scan): scrub worker secrets from prep subprocesses + dt_resync source guard** — 3 files / +164 / -1
   - **Part A** — `_PREP_ENV_ALLOWLIST` (PATH/HOME/LANG + Go/Cargo/.NET/Java/Maven/Ruby per-ecosystem) + `_scrubbed_env()` 헬퍼. `_run_prep` 의 `subprocess.run(..., env=_scrubbed_env())`. DT_API_KEY/SECRET_KEY/DATABASE_URL/`*_WEBHOOK_URL` 차단 핀.
   - **Part E.1** — `dt_resync._upsert_vulnerability` 의 `external_id` 추출도 line 110-118 의 isinstance 분기와 동일 패턴 적용. DT 4.13 의 string source shape 에서 AttributeError 가능성 제거.
   - 단위 테스트 2건 추가: `test_run_prep_passes_only_allowlisted_env`, `test_run_prep_seeds_dotnet_telemetry_optout`.

2. `2965283` **chore(settings): formalize git push + gh pr merge allow rules** — 1 file / +5 / -3
   - `.claude/settings.json` 의 사용자 편집 (2026-05-06 정책: push/PR 직접 수행 허용) 정식 commit. 기존 `git push*` / `gh pr merge*` deny 라인 제거 + `Bash(git push *)` / `Bash(gh pr merge *)` allow 추가.

3. `b446bd2` **chore(deps,trivy): bump python-multipart 0.0.27 + ignore 4 Maven UNREACHED HIGHs** — 2 files / +47 / -3
   - **Part E.2** — chore PR #4 의 Maven 3.9 도입으로 노출된 5 신규 HIGH 처리. python-multipart 0.0.22 → 0.0.27 (CVE-2026-42561 boundary-parsing DoS). 4 Maven JAR (netty-codec / netty-codec-http 4.1.132→133, bcpg-jdk18on 1.78.1→1.84, plexus-utils 3.5.1→4.0.3) `.trivyignore` Category (3) UNREACHED + 개별 reach 분석. `mvn dependency:tree` 가 attacker-controlled HTTP/2 server frames / OpenPGP 패킷 / 임의 XML XXE code path 를 안 타는 근거 명시.

4. `d047ee3` **chore(docs): archive completed chore PR #3 / #4 next-session prompts** — 2 files / +291 / -0
   - 완료된 prompt 2개를 `docs/sessions/archive/next-session-prompts/` 로 이동. `git status` 청결 유지 + 역사 기록 보존.

5. `07e27f8` **refactor(authz): migrate project / project_detail / vulnerability services to assert_team_access** — 3 files / +92 / -59
   - **Part D** — `_can_access_team` 헬퍼 11 사이트 (project_service:118/262, project_detail_service:233/399/627, vulnerability_service:371/695/778) 를 `assert_team_access(actor, team_id, log=log, resource=..., resource_id=..., deny=lambda: ...)` 로 일괄 마이그레이션. exception class / 메시지 / 403 vs 404 정책 보존. 기존 inline `log.warning("authz.cross_team_attempt", ...)` 3건은 helper 가 자동 emit 하므로 제거. import + alias comment 정리.
   - 회귀 테스트: 기존 IDOR 회귀 8건 (`test_overview_idor_other_team_is_forbidden`, `test_list_components_idor_other_team_is_forbidden`, `test_component_detail_other_team_user_gets_404_not_403`, `test_get_project_other_team_is_forbidden`, `test_create_project_outsider_is_forbidden`, `test_list_idor_other_team_is_forbidden`, `test_detail_other_team_user_gets_404_not_403`, `test_update_status_cross_team_returns_404_and_logs`) 가 모든 사이트 커버. 신규 테스트 불필요.
   - **scan_service `_can_access_team`** (line 92, 144, 272, 292) 은 명시적 out-of-scope — 다음 chore PR 후보.

6. `0975098` **chore(scan): silence S108 on _scrubbed_env HOME default with rationale** — 1 file / +7 / -1
   - `# noqa: S108 — see comment above` + 6줄 rationale comment. S108 의 collision/symlink-race 시나리오는 HOME 힌트와 무관 + workspace 가 매 scan 종료 시 wipe.

7. `99304fc` **refactor(ui): swap stand-in tabs primitive for @radix-ui/react-tabs** — 3 files / +60 +241 / -204
   - **Part E.3** — chore PR #3 carry-over. `apps/frontend/src/components/ui/tabs.tsx` (216줄 hand-rolled) → `@radix-ui/react-tabs@1.1.0` 표준 shadcn/ui wrapper (71줄). API 1:1 보존, data-state / role / aria 어트리뷰트 보존 → E2E 하네스 셀렉터 호환.
   - npm install: 11 packages added (radix transitives), 0 peer warnings, MIT.
   - 회귀: 31 test files / 241 tests pass. ProjectDetailPage.test.tsx 의 5탭 deep-link + active-state hydration 모두 green.

8. `9a00b86` **feat(integrations): add multi-ecosystem license fetcher (Maven/PyPI/crates/pkg.go.dev)** — 다수 파일 / +3000+ 라인
   - **Part B** — UAT 매트릭스 §4.1 의 license unknown 비율 (Java/Maven 91 / Python 39 / Rust 164 / Go 29) 보강 모듈.
   - 신규 디렉토리: `apps/backend/integrations/license_fetcher/{__init__,base,maven,pypi,crates,pkggo}.py`. dispatcher 가 purl prefix 로 4개 ecosystem fetcher 분기.
   - 캐시: `license_fetch_cache` 테이블 (24h TTL, positive + negative). forward-only Alembic `0004_license_fetch_cache.py` (`downgrade()` raise NotImplementedError). PRIMARY KEY = `purl`, `is_negative` 컬럼으로 404 응답도 cache.
   - rate-limit: per-host concurrency 1 + crates.io 1 req/sec, timeout 30s + 3 retry exponential backoff.
   - scan_source 통합: `_persist_component_licenses` 후처리 — cdxgen 결과 우선, 빈 컴포넌트만 fetcher 호출, `kind="concluded"` LicenseFinding emit.
   - 테스트: 108 단위 (VCR cassettes) + 1 dispatcher integration + 14 alembic upgrade test. **88% line coverage** on changed code.

9. `c21911d` **fix(integrations): cdxgen Gradle 8 init.gradle compatibility** — 2 files / 신규 test
   - **Part C** — pilot-java-gradle 0 component 이슈 (cdxgen 의 init.gradle 주입이 Gradle 8 의 `Could not get unknown property 'allprojects' for root project`). 옵션 (2) 채택 — env-var 기반 init script 주입.
   - Gradle 빌드 감지 시 no-op `allprojects { /* shim */ }` 작성, `CDXGEN_GRADLE_ARGS="--init-script <path>"` 로 cdxgen 에 전달. operator override 존중.
   - 회귀 테스트: `tests/unit/integrations/test_cdxgen_gradle_compat.py` — Gradle 감지 / shim 작성 / args 합성 핀.

10. `4d2c619` **chore(scan,settings): adopt security-reviewer L1 + I1 follow-ups** — 2 files / +25 / -0
    - **L1** — `_PREP_ENV_ALLOWLIST` 에 corporate CA / proxy 변수 추가 (`SSL_CERT_FILE` / `SSL_CERT_DIR` / `REQUESTS_CA_BUNDLE` / `NODE_EXTRA_CA_CERTS` / `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` + 소문자 변종). 코퍼레이트 TLS-intercepting proxy 환경에서 prep 가 silent x509 실패하던 시나리오 해소. 변수 자체는 worker-set 이므로 hostile clone 이 영향 못 줌.
    - **I1** — `git push --force*` / `git push -f *` / `git push --force-with-lease*` / `git push --delete *` / `git push *--mirror*` deny rule 추가. memory `feedback_push_pr_authorized` 의 "force-push 명시 승인 필요" 정책과 settings.json 일치.

### 1.2 security-reviewer Producer-Reviewer 결과

평결: **PASS with conditions** (0 Critical / 0 High / 2 Medium / 4 Low / 3 Info, 35 files / +4199 / -275 LoC 검토)

| ID | Severity | 위협 | 처리 |
|----|----------|------|------|
| **M1** | Medium | cdxgen / ORT subprocess 도 worker env 그대로 상속 (Part A 와 동일 위협, 다른 surface) — 악성 cdxgen 플러그인 / ORT rules.kts 가 DT_API_KEY 등 exfil 가능 | **별도 chore PR backlog** (M1 v2). 본 PR scope 밖 명시. |
| **M2** | Medium | Maven license `reference_url` 이 attacker-controlled (POM 의 `<url>` 그대로 저장) → phishing 링크. 프론트 `isSafeUrl()` 가 `javascript:` 차단하지만 임의 HTTPS 통과. NOTICE / PDF 템플릿은 검증 없음 | **별도 chore PR backlog**. allow-list 또는 fetcher-derived `reference_url` drop. |
| **L1** | Low | corporate CA / proxy 변수 누락 → on-prem TLS-intercept proxy 환경에서 prep silent x509 실패 | **본 PR 4d2c619 에서 흡수** ✅ |
| **L2** | Low | License fetcher 의 worst-case wall time (200 components × 1s rate-limit + 30s timeout × 4 retries) 가 Celery soft_time_limit (3600s) 초과 가능 | backlog (per-fetcher batch budget 도입) |
| **L3** | Low | `license_fetch_cache` TTL-expired row 누적 (cleanup task 미존재) | backlog (Celery Beat sweeper 도입) |
| **L4** | Low | License fetcher `httpx.Client(follow_redirects=True)` — 레지스트리 redirect 무검증 | backlog (defense-in-depth, M1 v2 PR 에 묶을 후보) |
| **I1** | Info | `Bash(git push *)` allow 가 force-push 변종 허용 — 사용자 정책과 misalign | **본 PR 4d2c619 에서 흡수** ✅ |
| **I2** | Info | pkg.go.dev HTML scrape regex 가 Hugo 템플릿 변경에 fragile | backlog (관측성 알림) |
| **I3** | Info | License fetcher cache write 가 caller session 안에 묶임 — scan 실패 시 cache rollback (의도된 trade-off) | docs note (Phase 7) |

## 2. 결정 사항 / 변경된 가정

- **License fetcher post-process placement** — scan pipeline 의 stage 신설 X. `_persist_components` 안의 후처리 함수 (`_persist_component_licenses`) 로 통합. cdxgen 결과 우선, 빈 컴포넌트만 fetch. ORT analyzer 정식 통합 시 reconcile 정책 (kind+source_path discriminator) 별도 backlog.
- **License cache 24h TTL + transactional binding** — fetcher 가 caller 의 Session 으로 cache write. scan 실패 시 cache 도 rollback. 트레이드오프: flaky 환경에서 캐시 효과 감소, 대신 부분 결과 inconsistency 회피.
- **assert_team_access 마이그레이션 점진 진행** — license/obligation_service (5) 는 chore PR #3, project/project_detail/vulnerability (11) 는 본 PR. **scan_service (4) 는 의도적으로 다음 chore PR**.
- **security-reviewer Medium 후속 분리 정책** — Medium #1 v2 (cdxgen/ORT env scrub) + Medium #2 (Maven reference_url) 는 별도 PR. 동일 위협 패턴이지만 surface 분리 + 별도 Producer-Reviewer 패스 권고. 본 PR 의 scope creep 차단.
- **test isolation 누수** — `test_component_detail_returns_drawer_payload_with_vulns` 가 누적된 `pkg:npm/foo` row 에 의존. main 에서도 재현 (pre-existing). CI 는 clean DB 로 시작하므로 영향 없음. fixture-level cleanup 별도 backlog.

## 3. 현재 상태

- **브랜치**: `feature/chore-pr5-security-license-fetcher` push 완료, **PR #9 open**.
- **CI**: 진행 중 (push 직후 queued). image-scan 는 chore PR #4 의 soft-fail 정책 유지.
- **테스트**: backend 단위 696 pass / 1 pre-existing fixture flake (clean DB 19/19), backend integration 141 pass / 1 skipped, frontend 241 tests / 31 files all green, license fetcher 88% line coverage.
- **lint/typecheck**: ruff clean, mypy clean (143 source files), npm lint 0 errors / 14 pre-existing warnings, npm typecheck clean.
- **로컬 dev**: 6/6 services healthy (postgres / redis / backend / celery-worker / celery-beat / frontend) + dtrack-api healthy via `+docker-compose.dt.yml` overlay.

## 4. 다음 세션이 할 일

옵션 두 갈래. 사용자 우선순위에 따라 선택.

### 옵션 A — Phase 4 (알림 시스템)

본 PR 머지 후 Phase 4 진입. 알림 시스템 = 이메일 SMTP + Slack Webhook + MS Teams Webhook + 알림 센터 UI + 사용자 알림 환경설정. CLAUDE.md "거버넌스 / 운영" 의 핵심 운영 기능. Phase 3 (프로젝트 detail / 취약점 / 라이선스 / 의무사항) 가 데이터 표시 위주였다면 Phase 4 는 **이벤트 → 채널 → 사용자** routing 의 첫 도입. 새 도메인 (Notification, NotificationChannel, NotificationPreference) + 새 endpoint (~6) + 새 UI 페이지 (`/notifications`, `/admin/notification-channels`).

PR 분할 권고:
- PR #14 — 알림 모델 + REST API + 권한 (NotificationPreference per-user + NotificationChannel per-team)
- PR #15 — 알림 발송 worker (Celery task, SMTP/Slack/Teams adapter, retry-with-backoff, 멱등 키)
- PR #16 — 이벤트 → 알림 정책 룰 엔진 (예: `vulnerability.severity >= HIGH` → 어느 채널, 어느 사용자)
- PR #17 — 알림 센터 UI (페이지 + 드로어 + 안 읽음 카운터)
- PR #18 — 관리자 알림 채널 관리 UI

### 옵션 B — Medium #1 v2 + L4 (cdxgen/ORT subprocess env scrub + license fetcher follow-redirects)

본 PR 의 보안 후속. 단일 chore PR 로 묶기. ~30~40 LoC 변경 + 단위 테스트.

작업:
1. `apps/backend/tasks/scan_source.py` 의 `_PREP_ENV_ALLOWLIST` + `_scrubbed_env` 를 새 모듈 `apps/backend/integrations/_subprocess_env.py` 로 승격. 기존 임포트 경로는 re-export 로 유지.
2. cdxgen 의 더 큰 allowlist (`NODE_PATH` / `NPM_CONFIG_*` 등) 추가한 별도 helper (`_scrubbed_env_for_cdxgen`).
3. `apps/backend/integrations/cdxgen.py:238` 의 `env = dict(os.environ)` → `env = _scrubbed_env_for_cdxgen()`.
4. `apps/backend/integrations/ort.py:147` 의 `subprocess.run(...)` 에 `env=_scrubbed_env_for_ort()` 추가.
5. `apps/backend/integrations/license_fetcher/*.py` 4 파일의 `httpx.Client(..., follow_redirects=True)` → `False`. 3xx 시 `None` 반환 + negative cache.
6. 단위 테스트 — cdxgen / ort subprocess env 가 secret 안 가짐 (Part A 와 동일 패턴) + license fetcher 가 redirect 안 따라감.
7. security-reviewer Producer-Reviewer 1 라운드.

### 옵션 C — Medium #2 + UAT 재검증

Maven license `reference_url` phishing 차단 + chore PR #5 의 Part B/C 효과 UAT 재검증.

작업:
1. `apps/backend/integrations/license_fetcher/maven.py` 등 4 fetcher 의 `reference_url` 처리 결정:
   - (a) allow-list known license-text hosts (`opensource.org` / `apache.org` / `gnu.org` / `creativecommons.org` / `spdx.org` / `eclipse.org` / `mozilla.org`)
   - (b) drop fetcher-derived `reference_url` 전체 (frontend 가 이미 `https://spdx.org/licenses/<id>.html` fallback)
   - 권고: (b) — 단순 + 일관성 + i18n 영향 최소.
2. UAT 재실행 (별도 세션 권고): pilot-java-gradle (Part C 효과 — components ≥ 30) + pilot-java-maven / pilot-python / pilot-rust / pilot-go (Part B 효과 — license unknown ≤ 20%, Go 는 ≤ 30%).
3. UAT 결과 docs/sessions/ 핸드오프.

권고: 옵션 A → 옵션 B 또는 옵션 C → 옵션 D (Phase 4 PR #14 ~ #18 진행 중 backlog 처리).

## 5. 주의·블로커

- **PR #9 CI 결과 미확정** — push 직후 queued. lint/typecheck/test 다 green 예상이지만 image-scan 는 soft-fail 이므로 결과 확인 필요. 새로운 `.trivyignore` 항목이 정확히 매칭되는지 (ID 정확도) 첫 실행에서 확인.
- **scan_service `_can_access_team`** carry-over — Part D 에서 의도적으로 제외. 다음 chore PR 후보 (1-모듈, ~30 LoC, 단위 테스트 동반).
- **test_component_detail_returns_drawer_payload_with_vulns** — pre-existing fixture 누수. 본 PR 와 무관. fixture-level cleanup 별도 backlog.
- **License fetcher 첫 scan worst-case wall time** — security-reviewer L2. flaky 환경에서 첫 scan 의 fetcher 호출이 Celery soft_time_limit (3600s) 초과 가능. 본 PR 머지 후 모니터링 필요. 다음 PR 에서 batch budget 도입.
- **License fetch cache 비대화** — security-reviewer L3. cleanup Celery Beat 미존재. 본 PR 머지 후 한 달 내 backlog 처리 권고.
- **Phase 4 진입 준비** — Notification 도메인 / API / 워커 / UI 5 PR 으로 분할. CLAUDE.md "거버넌스 / 운영" 의 핵심.
- **memory 보강 권고** — `feedback_security_reviewer_medium_followup_split.md` 신규 — Medium 후속은 분리된 chore PR + 별도 Producer-Reviewer 패스. M1 v2 / M2 처리 시 본 패턴 따를 것.

## 6. 다음 세션 시작 지시문 (복붙용)

### 옵션 A — Phase 4 진입 (알림 시스템)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = <chore PR #5 merge SHA>. 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1~#5. Phase 3 PR #10~#13 완료. Phase 4 (알림) 첫 PR 시작.

이번 세션 = Phase 4 PR #14 — 알림 시스템 모델 + REST API + 권한.

직전 핸드오프(반드시 시작 시 읽기):
  - docs/sessions/2026-05-07-chore-pr5-security-license-fetcher.md — chore PR #5 의 11 commit + security-reviewer Producer-Reviewer 결과 (PASS, L1/I1 흡수, M1/M2/L2/L3/L4/I2/I3 backlog).
  - docs/sessions/2026-05-07-phase3-pr13-obligations.md — Phase 3 종결 핸드오프. Phase 4 진입 시 참고.
  - CLAUDE.md "주요 기능 / 거버넌스 / 운영" 의 알림 시스템 절.

시작 시 검증 (반드시):
  ```
  docker-compose -f docker-compose.dev.yml ps               # 6/6 healthy (celery-beat 포함)
  docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml ps  # +dtrack-api healthy
  gh run list --limit 3                                      # main 최신 success
  git status                                                 # working tree 검증
  ```

작업 내용:

[모델] 새 도메인 3개 — Notification (이벤트당 1행, severity/title/payload JSONB), NotificationChannel (team-scoped, type=email|slack|teams|webhook, config JSONB, enabled flag, last_failure_at/error), NotificationPreference (user-scoped, event_type filter, channel_ids 배열, frequency=immediate|digest_daily|digest_weekly).

[API] 새 endpoint 6 — POST /api/v1/notifications/preferences (사용자 본인), GET /api/v1/notifications/preferences, GET /api/v1/notifications (사용자 본인 알림 목록, 페이지네이션 + 필터), PATCH /api/v1/notifications/{id}/read, GET /api/v1/notifications/unread-count, POST /api/v1/admin/notification-channels (Team Admin / Super Admin only).

[권한]
  - 일반 사용자: 본인 NotificationPreference 만 CRUD, 본인 Notification 만 조회.
  - Team Admin: 팀 NotificationChannel CRUD.
  - Super Admin: 전사 channel + audit log.
  - assert_team_access 패턴 사용.

핵심 라우팅:
  - **db-designer** (필수): Alembic migration 0005_notification_*.py (3 테이블, forward-only).
  - **backend-developer** (필수): notification_service.py + REST API + RBAC.
  - **test-writer** (필수): 단위 + IDOR 회귀 + 페이지네이션 핀.
  - **security-reviewer** (필수): Producer-Reviewer 1 라운드 — RBAC / IDOR / payload PII 검증.

설계 제약:
  - **schema 변경 1건 (3 테이블)** — Alembic forward-only.
  - PostgreSQL only / docker-compose V1 / `os.getenv()` 런타임.
  - PR #14 는 **모델 + API + 권한만**. 워커 (PR #15) / 룰 엔진 (PR #16) / UI (PR #17) / 관리자 UI (PR #18) 은 별도 PR.
  - 단위 coverage ≥ 80%, IDOR 회귀 필수.

DoD: lint/typecheck clean, 단위 ≥ 80%, IDOR pass, security-reviewer PASS, PR open, 핸드오프 작성.
```

### 옵션 B — Medium #1 v2 + L4 (cdxgen/ORT subprocess env scrub + follow-redirects)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = <chore PR #5 merge SHA>. 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1~#5.

이번 세션 = chore PR #6 — security-reviewer Medium #1 v2 (cdxgen/ORT subprocess env scrub) + L4 (license fetcher follow-redirects=False). chore PR #5 의 Part A 가 prep subprocess 만 cover 했고, cdxgen + ORT 의 동일 surface 는 별도 PR 로 분리한 후속. 새 endpoint 0건 / schema 0건.

직전 핸드오프:
  - docs/sessions/2026-05-07-chore-pr5-security-license-fetcher.md §1.2 의 M1 / L4 항목 — 본 PR 의 정확한 scope 정의.

시작 시 검증:
  - docker-compose 6/6 + dtrack-api healthy
  - main 최신 CI success
  - working tree 깨끗

작업 내용:

1. `apps/backend/tasks/scan_source.py` 의 `_PREP_ENV_ALLOWLIST` + `_scrubbed_env` 를 `apps/backend/integrations/_subprocess_env.py` 로 승격. 기존 import 는 re-export.
2. cdxgen 추가 allowlist (`NODE_PATH` / `NPM_CONFIG_*` / `npm_config_*`) 포함한 `_scrubbed_env_for_cdxgen` 헬퍼.
3. `apps/backend/integrations/cdxgen.py:238` 의 `env = dict(os.environ)` → `env = _scrubbed_env_for_cdxgen()` + 단위 테스트 1건.
4. `apps/backend/integrations/ort.py:147` 의 `subprocess.run(...)` 에 `env=_scrubbed_env_for_ort()` 추가 + 단위 테스트 1건.
5. `apps/backend/integrations/license_fetcher/{maven,pypi,crates,pkggo}.py` 의 `httpx.Client(..., follow_redirects=True)` → `False`. 3xx 시 None 반환 + negative cache. 단위 테스트 1건 per fetcher (총 4건).
6. security-reviewer Producer-Reviewer 1 라운드.

핵심 라우팅:
  - **scan-pipeline-specialist** (필수): cdxgen + ort env scrub + helper 승격.
  - **backend-developer** (필수): license fetcher follow_redirects 변경 + 회귀 테스트.
  - **test-writer** (필수): subprocess env 통과 핀 (cdxgen / ort 별).
  - **security-reviewer** (필수): Producer-Reviewer.

DoD: lint/typecheck clean, 단위 추가 ≥ 80%, security-reviewer PASS.
```

### 옵션 C — Medium #2 + UAT 재검증

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

이번 세션 = chore PR #6 — security-reviewer Medium #2 (Maven license reference_url phishing 차단) + chore PR #5 의 Part B/C 효과 UAT 재검증.

직전 핸드오프:
  - docs/sessions/2026-05-07-chore-pr5-security-license-fetcher.md §1.2 의 M2 항목.
  - docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md — 기준선.

작업 내용:

1. `apps/backend/integrations/license_fetcher/{maven,pypi,crates,pkggo}.py` 의 `reference_url` 필드 drop. fetcher-derived row 는 `reference_url=None` 만 emit. 프론트는 이미 SPDX id 기반 fallback (`https://spdx.org/licenses/<id>.html`) 보유.
2. `apps/backend/models/license_fetch_cache.py` 의 reference_url 컬럼은 schema 보존 (forward-only) 하되, fetcher 가 더 이상 채우지 않음. 24h 후 모든 fetcher row 의 reference_url 이 NULL 로 회귀.
3. UAT 재실행 (도커 환경 + 5 pilot 레포):
   - pilot-java-gradle: cdxgen scan → component ≥ 30 (Part C 효과).
   - pilot-java-maven / pilot-python / pilot-rust / pilot-go: cdxgen + license fetcher → license unknown ≤ 20% (Go 는 ≤ 30%).
4. UAT 결과 docs/sessions/2026-05-XX-uat-multi-ecosystem-matrix-v2.md 작성.

핵심 라우팅:
  - **backend-developer**: reference_url drop + fetcher 회귀 테스트.
  - **devops-engineer / test-writer**: UAT 5 pilot 시나리오 자동화 권고.
  - **security-reviewer**: Producer-Reviewer 1 라운드.

DoD: lint/typecheck clean, UAT 5 pilot 의 license-coverage 기준 달성, security-reviewer PASS.
```

## 비주문 (chore PR #5 scope 외 — 향후 backlog 등재)

- **scan_service `_can_access_team` 마이그레이션** — 다음 chore PR 후보 (1-모듈, ~30 LoC).
- **security-reviewer Medium #1 v2** (cdxgen / ORT env scrub) — 옵션 B.
- **security-reviewer Medium #2** (Maven `reference_url` phishing) — 옵션 C.
- **security-reviewer L2** (license fetcher batch wall budget) — 별도 chore.
- **security-reviewer L3** (license_fetch_cache cleanup Celery Beat) — 별도 chore.
- **security-reviewer L4** (license fetcher `follow_redirects=False`) — 옵션 B 에 묶을 후보.
- **security-reviewer I2** (pkg.go.dev scrape 관측성) — Phase 7+ 옵스.
- **security-reviewer I3** (license fetcher cache transactional binding 문서화) — Phase 7 docs.
- **chore PR #4 Medium #2 (egress NetworkPolicy)** — 별도 chore PR (devops-engineer 단독). docker-compose + Helm chart + docs/security.md.
- **ORT analyze stage 정식 통합** — 별도 큰 PR. Phase 5/6 후보. fetcher 의 concluded vs analyzer 의 concluded reconcile 정책.
- **NVD API v2 fallback** — docs + .env.example 안내.
- **Phase 8 audit listener INSERT-PK race / byte-stable ETag / PII guidance** — Phase 8.
- **License fetcher Celery Beat prefetch (12h cadence)** — 별도 chore. 현재 24h-on-demand 충분.
- **test_component_detail_returns_drawer_payload_with_vulns fixture 누수 fix** — 별도 chore (fixture-level truncate).
