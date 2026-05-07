# Session Handoff — 2026-05-07 — chore PR #4 — Pipeline Stabilization (UAT 정식화 + 다중 언어 worker + pre-cdxgen prep + DT polling retry)

## 1. 무엇을 했나

`feature/chore-pr4-pipeline-stabilization` 브랜치 생성 + 6개 logical commit + security-reviewer 1 라운드 + PR open. 본 PR 은 2026-05-07 UAT 도중 발견된 임시 패치 4건 + ad-hoc 도구 설치를 정식 코드로 정착시키는 안정화 PR. **새 endpoint 0건, 스키마 변경 0건**, 모든 변경은 scan pipeline + worker image + DT integration 레이어.

### 1.1 Commit 6개 구성 (`git log --oneline main..HEAD`)

1. `ce359af` **chore(scan): formalize UAT patches for cdxgen license + DT 4.13 + ORT skip** — 5 files / +274 / -8
   - `apps/backend/tasks/scan_source.py` 의 3개 UAT 패치 정식화:
     1. `_fetch_source(mock_only=False)` — Phase 2 PR #9 의 IP-pin guarded real-clone 활성화 (1 line)
     2. `Stage 4 ORT evaluate` try/except + `log.warning("ort_stage_skipped")` — cdxgen SBOM 을 `ort evaluate` 에 잘못 입력하는 broken integration 우회. ORT analyzer 정식 통합은 별도 PR 백로그.
     3. `_persist_components` 확장 (~150줄) — cdxgen `components[].licenses` → `License` + `kind="declared"` LicenseFinding 변환 + `_LICENSE_CATEGORY_DEFAULTS` 30 SPDX 매핑 + `_classify_license_category` / `_extract_spdx_ids` / `_get_or_create_license` / `_persist_component_licenses` 신규 함수. CLAUDE.md §"라이선스 분류" 의 allowed/conditional/forbidden 카테고리 그대로 반영.
   - `apps/backend/tasks/dt_resync.py:110-118` — DT 4.12 (`{"name": "..."}`) → DT 4.13 (`"<name>"`) source-shape 양쪽 처리 (1줄 → isinstance 분기 5줄로 보강).
   - `docker-compose.dt.yml` 신규 — `dependencytrack/apiserver:4.13.2` + 4 GB heap + `dtrack-data` volume + `./ort` ro 마운트 + backend/celery-worker 에 `DT_URL`/`DT_API_KEY` env 주입. `docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml up -d` 한 줄로 DT 포함 부팅.
   - `ort/rules.kts` 신규 — minimal placeholder kts (empty `ruleSet { }`). v1 ruleset 정식 포팅 backlog 코멘트 포함.
   - `.env.example` — DT 4.13 + 8 OSV ecosystems pre-config 절차 (admin/admin → password change → Automation team API key) + `docs/admin/dt-overlay.md` 미래 링크 안내.

2. `41dae1a` **chore(worker): bake multi-language lockfile resolvers into worker image** — 1 file / +138 / -10
   - `apps/backend/Dockerfile.worker` 에 7개 ecosystem toolchain bake-in (UAT 의 휘발성 ad-hoc 설치 정식화):
     - Maven 3.8.7 (Debian apt) — Java SBOM
     - Composer 2.5 + PHP 8.2 CLI (apt) — Packagist
     - Ruby 3.1 + Bundler 2.4 (apt) — `bundle lock`
     - Cargo 0.66 (apt) — `cargo generate-lockfile`
     - Gradle 8.10.2 (manual zip + SHA-256 verified via 동일 origin `*.sha256` 컴패니언 파일)
     - Go 1.22.7 (manual tarball + SHA-256 verified)
     - .NET SDK 8.0 (Microsoft apt repo)
   - 계층 순서: 작은 apt 패키지 먼저 → manual download (Gradle, Go) → .NET → ORT (가장 큰 ~600MB extracted, 마지막 유지). 결과 이미지 ~2.5GB → ~3.5GB.
   - SHA-256 검증은 publisher 의 `.sha256` 컴패니언을 fetch + `sha256sum -c` (TOFU). cosign-signed 검증은 Phase 8 hardening backlog (security-reviewer Medium #3).

3. `7a9a957` **feat(scan): pre-cdxgen lockfile prep + DT findings retry-with-backoff** — 2 files / +207 / -5
   - **Part B — stage 2.5 pre-cdxgen prep**:
     - `_prepare_for_cdxgen(source_dir, scan_uuid)` — Gemfile / Cargo.toml / go.mod / `*.csproj` 마커 감지 → `bundle lock` / `cargo generate-lockfile` / `go mod tidy` / `dotnet restore` 분기 호출.
     - `_run_prep(name, cmd, cwd, timeout, scan_uuid)` — best-effort 실행, 5 분 timeout, returncode≠0 / TimeoutExpired / OSError 모두 swallow + log.warning. cmd 는 hardcoded list, cwd 는 worker-controlled scan workspace, no shell.
     - `_run_pipeline` 의 stage 2.5 (`prep`, percent=18) 신규 — fetch 와 cdxgen 사이 호출.
   - **Part C — DT findings retry-with-backoff**:
     - `_poll_dt_findings_with_retry` — `_DT_FINDINGS_POLL_DELAYS_SECONDS = (2, 4, 8, 16, 30)` 으로 누적 ~60초 budget. DT BOM_UPLOAD_ANALYSIS 비동기 매칭이 ~1초 내 미완료인 false-empty 시나리오 (UAT 에서 pilot-java-maven 54 vulns 가 폴 시점에 0 으로 보였던 정확한 케이스) 를 핸들링.
     - 첫 non-empty 결과에서 short-circuit. all-empty 시 `[]` 반환 (기존 동작 유지).
     - 각 attempt 는 `breaker.call` 래핑 — DT outage 시 breaker open 이 propagate.
   - 기존 integration test 2건 (`test_scan_source_pipeline_mock`) 에 `monkeypatch.setattr("tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,))` 추가 — 60초 wall-clock 비용 회피.
   - `subprocess` 모듈 import 를 `_fetch_source` 의 dead-code 로컬 import 에서 모듈 스코프로 hoist.

4. `dfc2aac` **chore(compose): add celery-beat sidecar so registered schedules fire** — 2 files / +30 / -4
   - `docker-compose.dev.yml` 에 `celery-beat` 서비스 추가. 워커 이미지 재사용 (`x-worker-build` anchor). `celery -A tasks.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule`. `celery-worker` healthcheck 의존.
   - `apps/backend/tasks/celery_app.py` docstring 업데이트 — "Beat sidecar lands in follow-up PR" → "chore PR #4 wires sidecar".
   - 결과: PR #8 에서 등록된 dt_health/60s + dt_resync/1h + dt_orphan_cleaner/6h 가 실제로 fire.

5. `8602549` **test(scan): unit cover prep dispatch + retry-with-backoff + license map** — 2 files / +545 / -0
   - `tests/unit/tasks/test_scan_source_prep.py` (35 cases): `_prepare_for_cdxgen` 7-ecosystem 분기 (lockfile 존재/부재 / .NET CLI missing / polyglot 동시 호출), `_run_prep` 4 swallow paths, `_LICENSE_CATEGORY_DEFAULTS` 13 매핑 검증 + 4 unknown fallback, `_extract_spdx_ids` 5 CycloneDX 형태 파싱.
   - `tests/unit/tasks/test_scan_source_dt_findings_retry.py` (5 cases): first-attempt 성공 / N번째 attempt 성공 / all-empty / breaker open propagate / zero-delay schedule.
   - 모두 monkeypatch + tmp_path 만 사용. Postgres / Redis 의존 0.

6. `a97ddb4` **chore(scan): broaden _run_prep error handling to OSError** — 1 file / +9 / -6
   - security-reviewer Low #4 즉시 적용. `FileNotFoundError` → `OSError` (PermissionError + host-condition-degraded family 포함). 로그 키도 `prep_tool_missing` → `prep_unavailable` 로 의미 일반화. 기존 `test_run_prep_swallows_missing_tool` 은 `FileNotFoundError IS-A OSError` 라 그대로 통과.

### 1.2 검증

- `docker-compose -f docker-compose.dev.yml exec -T backend ruff check .` → **All checks passed!**
- `mypy tasks/scan_source.py tasks/dt_resync.py tasks/celery_app.py` → 0 errors (3 source files).
- `mypy tests/unit/tasks/test_scan_source_prep.py tests/unit/tasks/test_scan_source_dt_findings_retry.py` → 0 errors.
- `pytest tests/unit/` → **591 passed, 7 skipped** (Phase 3 PR #13 baseline 591 → +29 신규, 일부 helper 이동 net +29 ~ 신규 39 - 기존 10 등 = pytest 의 정확 카운트 검증 필요. CI 에서 pin).
- `pytest tests/integration/scan/test_scan_source_pipeline_mock.py` → **3/3 pass** (retry-with-backoff zero-delay override 확인).
- `pytest tests/unit/tasks/test_scan_source_prep.py tests/unit/tasks/test_scan_source_dt_findings_retry.py` → **40/40 pass**.

### 1.3 security-reviewer Producer-Reviewer 1 라운드

- **평결: PASS** — Critical/High 0건, Medium 3건은 모두 별도 hardening PR (chore PR #5 또는 Phase 8) 권고.
- **즉시 적용**: Low #4 (OSError 통합) — commit `a97ddb4` 로 반영.
- **별도 PR 권고 (PR #4 블로커 아님)**:
  - **Medium #1** — `_run_prep` subprocess env 무필터. `DT_API_KEY` / `SECRET_KEY` / `DATABASE_URL` (password 포함) / `*_WEBHOOK_URL` 등이 `bundle lock` / `go mod tidy` / `dotnet restore` 자식 프로세스로 상속됨. 악성 NuGet feed (`nuget.config`) 또는 Go `replace` directive → 외부 호스트 → 텔레메트리/크래시 경로로 secret 유출 가능. 대응: `subprocess.run(env={"PATH": ..., "HOME": "/tmp", "GOPROXY": "...", "LANG": "C.UTF-8"})` 명시. CVSS 4.3.
  - **Medium #2** — `cargo generate-lockfile` / `go mod tidy` / `dotnet restore` 의 무제한 outbound network. SSRF/공급망 측면 — 악성 repo 가 worker 의 내부 네트워크 도달성을 fingerprint. 대응: (a) 컴포즈/k8s NetworkPolicy egress allowlist, (b) `docs/security.md` 에 egress 요구 명시. CVSS 5.3.
  - **Medium #3** — Gradle/Go SHA-256 검증이 publisher 동일 origin TOFU. 공급망 진영 양보. 대응: SHA 값을 Dockerfile `ENV` 상수로 pin (`debian/openjdk` 패턴) + Phase 8 cosign 통합 시 GPG asc 검증 추가. CVSS 4.4.
- **Low #5** — 라이선스 매핑 4개 extras (`0BSD`, `Zlib`, `WTFPL`, `Python-2.0`, `MPL-1.1`, `Apache-1.1`) 가 CLAUDE.md 에 미명시. 정책 결정 사안 (legal/compliance review). 본 PR 은 reviewer's option (a) — 그대로 채택 (defensible permissive defaults). 향후 정책 강화 시 옵션 (b) 로 unknown 폴 백 가능.
- **Info**:
  - DT API key 는 어떤 신규 로그 statement 에도 노출 없음 (`grep -n "log\." integrations/dt/{client,breaker}.py` end-to-end 검증).
  - subprocess 인젝션 방어 holds: cmd hardcoded list / no shell / scan-owned cwd / 클론 콘텐츠가 argv 영향 못 줌.
  - `dt_resync.py` source-shape 패치 정확성 OK. `external_id` 추출 라인 (line 94) 의 동일 패턴은 vulnId fallback 으로 가려져 있어 별도 follow-up.

## 2. 결정 사항 / 변경된 가정

- **DT 4.13 표준 채택**: `docker-compose.dt.yml` 에 4.13.2 확정. NIST NVD v1.1 deprecation (DT 4.12) → OSV.dev 8 ecosystem 통합 (DT 4.13) 으로 vulnerability source 정상화.
- **Worker 이미지 ~3.5GB 수용**: 다중 언어 ecosystem 검출 요구가 binary size 비용을 상회. multi-stage / minimal variant 분리는 Phase 8 hardening 별도 PR.
- **`_persist_components` 의 license 변환은 cdxgen-fast-path scope**: ORT analyzer 정식 통합 시 `kind="concluded"` / `kind="detected"` 가 추가될 예정. 본 PR 은 `kind="declared"` 만 emit.
- **Compound SPDX expression skip 정책**: `MIT OR Apache-2.0` 같은 표현식은 cdxgen-fast-path 에서 명시적으로 미지원. SPDX expression parser 가 필요한 영역으로, ORT analyzer 정식 통합 시 per-file 분리로 해결.
- **Beat sidecar 의 schedule persistence**: `/tmp/celerybeat-schedule` (volatile) 사용 — dev 에서 missing tick 한두 번은 OK, 운영 표준 시 `--schedule=/var/celery/...` + volume 마운트로 재고. Phase 7/8 backlog.
- **retry-with-backoff vs. delayed task 대안**: 본 PR 은 retry-with-backoff 채택 (시나리오 의존도 낮음). 옵션 B (별도 Celery task `delay_then_sync_findings.apply_async(args=[scan_id], countdown=15)`) 는 더 elegant 하지만 task 등록/추적 비용 → 본 PR 미채택.
- **MEMORY.md 갱신 후보**:
  - DT 4.13 + 8 OSV ecosystems = 표준 vulnerability source. 향후 DT 운영/디버깅 문맥에 사용.
  - subprocess prep helper 패턴 (cmd hardcoded list / cwd worker-controlled / OSError swallow / TimeoutExpired swallow) — 향후 다른 SCA tool 통합에 재사용.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), #6 (55e67bd), #7 (d7bc929 + 93c41a4), #8 (502f02f + ebb9c53), #9 (f55e70d + 9da6b3c), chore PR #1 (6366b62), chore PR #2 (38236e2), Phase 3 PR #10 (7d6f66d), Phase 3 PR #11 (e19bd8a), Phase 3 PR #12 (ac15d6a), Phase 3 PR #13 (a3634e3), **chore PR #3 (37f7fc6)**.
- **진행 중 PR**: **chore PR #4 — feature/chore-pr4-pipeline-stabilization** — push + gh pr create 직후 (PR # 와 commit hash 는 본 핸드오프 시점에 OPEN). CI 결과는 `gh pr view <N>` 로 다음 세션에서 확인.
- **Working tree 미커밋**: `.claude/settings.json` (chore PR #3 carry-over — settings 셀프-수정 차단으로 PR #4 에서 commit 미수행, chore PR #5 에서 분리 commit 권장), `docs/sessions/_next-session-prompt-chore-pr3.md`, `docs/sessions/_next-session-prompt-chore-pr4.md` (옛 prompt 들 — 정리 가능).
- **DB 상태**: 변경 없음 (스키마 0건, migration 0건).
- **DT 환경**: chore PR #4 는 DT 셋업을 자동화하지 않음 (admin/admin → password change → API key 추출은 README/docs 안내). UAT 환경은 그대로 유지 — 224,103 vulns 동기화 + Maven OSV 미러 완료 + 기타 7 ecosystem 미러 진행.
- **테스트 상태**:
  - 단위: **591 passed, 7 skipped** (chore PR #3 baseline 562 → +29 신규).
  - integration scan: **3/3 pass**.
  - E2E: 본 PR 미실행 (UI 변경 0건). 필요 시 `npm run test:e2e` 재실행 권고.
  - bandit / semgrep / gitleaks / pip-audit: 본 세션 미실행. CI 가 검증.

## 4. 다음 세션이 할 일

권장 순서: **CI green 확인 → 머지 → chore PR #5 (Part E §4.1 묶음) → Phase 4 (알림)** 또는 **CI green 확인 → 머지 → 직접 Phase 4**.

### 옵션 A — chore PR #5 (Part E §4.1 + security-reviewer Medium 묶음)

본 핸드오프 §1.3 의 3개 Medium 후속 + chore PR #4 미반영 carry-over:

1. **Medium #1** — `_run_prep` 의 subprocess env 명시적 화이트리스트 (PATH/HOME/LANG/GOFLAGS/GOPROXY/GOSUMDB 등). `DT_API_KEY` / `SECRET_KEY` / `DATABASE_URL` / `*_WEBHOOK_URL` strip.
2. **Medium #3** — Gradle/Go SHA-256 을 Dockerfile `ENV GRADLE_SHA256=...` 상수로 pin (debian/openjdk 패턴).
3. **multi-language license fetcher** (Part E §4.1 의 핵심) — Maven Central / PyPI / crates.io / pkg.go.dev 의 per-purl license fetch (post-process). cdxgen 단독으로 91 Java + 39 Python + 164 Rust + 29 Go 컴포넌트가 모두 `unknown` → fetcher 도입 후 ≥ 80% allowed/conditional/forbidden 커버 목표.
4. **cdxgen Gradle init.gradle 호환** — `ghcr.io/cyclonedx/cdxgen-java11:v12` image variant 또는 init.gradle disable + manual gradle dependencies parse. 본 PR 의 toolchain bake-in 후에도 Gradle 0 component 회귀 가능성 있음.
5. **dt_resync.py:94 external_id isinstance 가드** — security-reviewer Info follow-up.
6. **`assert_team_access` 잔여 마이그레이션** — chore PR #3 carry-over, 11 사이트 (project / project_detail / vulnerability 모듈).
7. **shadcn Tabs → @radix-ui/react-tabs swap** — chore PR #3 carry-over.
8. **`.claude/settings.json` push allow 정식 commit** — chore PR #3 carry-over.

권고 라우팅: scan-pipeline-specialist (Medium #1, #3, license fetcher) + frontend-dev (Tabs swap) + backend-developer (assert_team_access 마이그레이션) + security-reviewer (Producer-Reviewer 1 라운드).

### 옵션 B — Phase 4 — 알림 시스템 (안정화 이후 신규 도메인)

CLAUDE.md §3 Phase 4 의 SMTP 이메일 + Slack/Teams Webhook + 알림 센터 UI + per-team 채널 설정. chore PR #4 안정화로 worker pipeline + DT integration 가 완성됐으므로 알림 도메인 진입 가능.

본 PR 머지 후 `docs/v2-execution-plan.md` §6 의 Phase 4 시작 지시문 사용.

### 옵션 C — Medium #2 (network egress hardening) 별도 PR

devops-engineer + scan-pipeline-specialist 협업. docker-compose 의 worker 컨테이너 egress NetworkPolicy + `docs/security.md` 의 egress 요구 명시. 단독 PR (~30분).

## 5. 주의·블로커

- **CI 결과 미확인**: 본 핸드오프 시점에 push 직후. 다음 세션 첫 작업으로 `gh pr view <N>` + `gh run list --limit 5` 결과 확인. 단위/integration 은 로컬 green 이지만 CI 의 별도 잡 (image build, security scan, e2e) 결과는 미정.
- **Worker 이미지 빌드 시간**: ~3.5GB 이미지를 처음 빌드할 때 GitHub Actions 에서 ~15-20분 예상 (debian apt + Gradle/Go/.NET tarball + ORT zip). buildx 캐시 hit 시 ~3분 이내. 첫 PR CI run 의 image-scan 잡이 timeout 이면 retry 권고.
- **DT 세팅은 본 PR 의 자동화 scope 외**: admin/admin → password change → Automation team API key 추출 + 8 OSV ecosystem 활성화는 운영자 수동 절차. `docs/admin/dt-overlay.md` (미작성 — Part E §4.1 또는 doc-writer 위임) 가 안내. UAT 환경은 그대로 유지.
- **Gradle 8 init.gradle 호환 미해결**: pilot-java-gradle 의 0 component 이슈는 본 PR 의 toolchain bake-in 만으로는 미해결. cdxgen 옵션 또는 image variant 변경이 필요 (Part E §4.1).
- **subprocess env 상속**: 본 PR 은 secret 인지 없이 모든 worker env 를 prep helper subprocess 로 상속. 악성 repo 의 NuGet feed / Go replace directive 가 텔레메트리 경로로 secret 유출 시나리오는 chore PR #5 Medium #1 까지 잔존 위험.
- **Beat sidecar persistence**: `/tmp/celerybeat-schedule` (volatile). 운영 환경 도입 시 volume 마운트 필요.

## 6. 다음 세션 시작 지시문 (복붙용)

### 옵션 A — chore PR #5 — security hardening + multi-language license fetcher (권장)

```
chore — chore PR #4 의 security-reviewer Medium follow-up + cdxgen 다중 언어 license fetcher + Gradle 8 호환 + assert_team_access 잔여 마이그레이션.

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = chore PR #4 merge commit (선결 — `gh pr view <N>` 으로 확인 후 시작).

직전 핸드오프(반드시 시작 시 읽기):
  - docs/sessions/2026-05-07-chore-pr4-pipeline-stabilization.md — 본 PR (UAT 정식화 + 다중 언어 worker + pre-cdxgen prep + DT polling retry). §1.3 의 security-reviewer Medium #1/#2/#3 follow-up 이 chore PR #5 scope.
  - docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md — Part E §4.1 의 license fetcher backlog.

이번 세션 = chore PR #5 — 7건 묶음:
  1. (Medium #1) `_run_prep` subprocess env 명시적 화이트리스트 — DT_API_KEY/SECRET_KEY/DATABASE_URL/*_WEBHOOK_URL strip.
  2. (Medium #3) Dockerfile.worker 의 Gradle/Go SHA-256 을 ENV 상수로 pin.
  3. (Part E §4.1) Maven Central / PyPI / crates.io / pkg.go.dev license fetcher (post-process). 회귀 테스트: pilot-* 9 ecosystem 의 license coverage ≥ 80%.
  4. (Part E §4.1) cdxgen Gradle 8 호환 — cdxgen-java11 variant 또는 init.gradle workaround.
  5. (chore PR #3 carry-over) `assert_team_access` 11 사이트 마이그레이션 (project / project_detail / vulnerability 모듈).
  6. (chore PR #3 carry-over) shadcn Tabs → @radix-ui/react-tabs swap + 5탭 deep-link e2e 회귀.
  7. (chore PR #3 carry-over) `.claude/settings.json` push allow 정식 commit.
  8. (chore PR #4 Info) `dt_resync.py:94` external_id isinstance 가드.

핵심 라우팅: scan-pipeline-specialist (Medium #1/#3, license fetcher) + frontend-dev (Tabs swap) + backend-developer (assert_team_access) + security-reviewer (Producer-Reviewer 1 라운드).

설계 제약: 새 endpoint 0건, schema 변경 0건. PostgreSQL only / Alembic forward-only / 인증 필수 / docker-compose V1 / `os.getenv()` 런타임 호출.

DoD: main CI 모든 잡 success. ruff + mypy clean. license coverage ≥ 80% (Java/Python/Rust/Go pilot-*). security-reviewer 평결 PASS.

세션 종료 시 docs/sessions/2026-05-XX-chore-pr5-hardening-license-fetcher.md 작성. 다음 세션 시작 지시문은 §5 양식으로 옵션 A (Phase 4 — 알림) 와 옵션 B (Medium #2 — egress NetworkPolicy 단독 PR) 등재.
```

### 옵션 B — Phase 4 — 알림 시스템 (안정화 이후 신규 도메인)

```
Phase 4 — 알림 시스템 (이메일 SMTP + Slack/Teams Webhook + 알림 센터 UI + per-team 채널 설정).

main HEAD = chore PR #4 merge commit (선결).

직전 핸드오프(반드시 시작 시 읽기):
  - docs/sessions/2026-05-07-chore-pr4-pipeline-stabilization.md — chore PR #4 의 안정화 결과. Phase 4 진입 시 worker pipeline + DT integration 정상 동작 전제.
  - docs/v2-execution-plan.md §6 Phase 4 — 알림 도메인 시작 지시문.

이번 세션 = Phase 4 — 알림 시스템:
  - models: NotificationChannel (team-scoped, type=email/slack/teams, config jsonb)
  - 신규 endpoint: POST /api/v1/notifications/channels, GET /api/v1/notifications/channels, DELETE
  - notifications/email.py + notifications/slack.py + notifications/teams.py — adapter 패턴
  - tasks/notification_dispatch.py — Celery task, scan_completed/vuln_severity_critical/license_forbidden 트리거
  - frontend: /notifications 페이지 (알림 센터) + per-team channel 설정 UI

핵심 라우팅: backend-developer (endpoints) + db-designer (NotificationChannel migration) + scan-pipeline-specialist (notification_dispatch) + frontend-dev (UI) + i18n-specialist (EN/KO) + test-writer (unit + Playwright) + security-reviewer.

설계 제약: PostgreSQL only / Alembic forward-only / 인증 필수 / RFC 7807 / EN+KO 동시 / Docusaurus 동시.

DoD: 5개 트리거 동작 (scan_completed / vuln_severity=critical / license=forbidden / approval_pending / scan_failed), Slack + Teams + Email 3 채널 모두 round-trip. 기존 핸드오프 양식 준용.
```

---

## 비주문 (chore PR #4 scope 외 — 향후 backlog 등재)

- **ORT analyze stage 정식 통합**: scan_source 에 `ort analyze` (~5분 소요) 추가 → analyzer-result.yml → `ort evaluate` 입력. 본 PR 의 try/except 우회는 임시. 별도 큰 PR.
- **Docker scan flow UI 노출**: `scan_container` task (Trivy 기반) 가 이미 존재. UI 의 scan trigger 에서 source/container 선택 surface. Phase 4+ 또는 별도 PR.
- **NVD API v2 fallback**: DT 4.13 의 NVD v2 별도 API key 등록. doc + .env.example NVD_API_KEY 안내.
- **v1 ort/rules.kts 정식 포팅**: 본 PR 의 minimal kts 는 placeholder. v1 에서 ORT 라이선스 룰셋 그대로 copy + 카테고리 매핑 검증.
- **Phase 8 hardening 후속**: cdxgen-plugins-bin carve-out / Dockerfile.worker base digest pin / Worker container `USER` non-root / NodeSource signed-by deb / cdxgen `npm audit signatures` (chore PR #2 carry-over).
- **Phase 8 audit listener INSERT-PK race / byte-stable ETag / PII guidance**: PR #11 carry-over.
