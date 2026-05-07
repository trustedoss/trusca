chore — UAT 패치 정식화 + 다중 언어 worker 이미지 + pre-cdxgen prep + DT polling race fix.

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = 37f7fc6 (chore PR #3 merge). 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 (deps hygiene) + chore PR #2 (worker image hardening + .trivyignore) + Phase 3 PR #10 (Project Detail Overview + Components) + Phase 3 PR #11 (Vulnerabilities 탭) + Phase 3 PR #12 (Licenses 탭) + Phase 3 PR #13 (Obligations 탭 + NOTICE generator) + chore PR #3 (size cap + nosniff + rate-limit + RFC 6266 + sql_safety + authz helper).

이번 세션 = chore PR #4 — 2026-05-07 UAT 도중 발견된 임시 패치들을 정식 PR 로 묶고, 다중 언어 ecosystem (PHP / Python / Ruby / Rust / Go / .NET / Docker) 의 종속성 검출 차원에서 worker 이미지 + scan pipeline 보강. **새 도메인 0건**, scan-pipeline 의 깨진 통합 정상화 + 다중 언어 baseline 확보.

직전 핸드오프(반드시 시작 시 읽기):
  - docs/sessions/2026-05-07-chore-pr3-hardening.md — chore PR #3 의 hardening 묶음 (size cap / nosniff / rate-limit / RFC 6266 / authz / sql_safety) + 부분 마이그레이션 정책.
  - docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md — 본 세션 (UAT + 7 ecosystem 테스트 + chore PR #4 scope 도출). **반드시 읽을 것** — 작업 시작 시 working tree 의 미커밋 임시 패치 + worker 컨테이너의 휘발성 ad-hoc 도구 설치 상태 확인 필요.
  - docs/sessions/2026-05-07-phase3-pr13-obligations.md — Phase 3 PR #13 컨텍스트 (Obligation 도메인 read-only catalog, scan pipeline 의 license/obligation 자동 채움 미구현 — 본 PR 에서 license_findings 변환 코드 정식화).

시작 시 검증 (반드시):
  ```
  docker-compose -f docker-compose.dev.yml ps        # 5/5 healthy
  docker ps | grep dtrack-api                         # 만약 부재면 dtrack 정지 상태 — overlay 살리기
  gh run list --limit 3                               # main 최신 success
  git status                                          # working tree 의 미커밋 패치들 식별
  ```

  **중요 — UAT 직후 working tree 상태**:
  - **신규 untracked 파일** (정식 commit 대상):
    - `docker-compose.dt.yml` — DT 4.13 overlay (dependencytrack/apiserver:4.13.2 + DT_URL/DT_API_KEY env 주입 + ort/ 마운트)
    - `ort/rules.kts` — UAT 용 minimal kts (TODO: v1 ruleset 정식 포팅)
  - **modified 파일** (정식 commit 대상):
    - `apps/backend/tasks/scan_source.py` — 3 곳 패치:
      1. `_fetch_source(mock_only=False)` (line ~190) — real git clone 활성화
      2. ORT stage try/except 우회 (line ~209) — broken integration 감싸기
      3. `_persist_components` 확장 (line ~495+) — cdxgen SBOM → license_findings 변환 + `_LICENSE_CATEGORY_DEFAULTS` 30개 SPDX 매핑 + `_classify_license_category` / `_extract_spdx_ids` / `_get_or_create_license` / `_persist_component_licenses` 신규 함수 (~150줄)
    - `apps/backend/tasks/dt_resync.py` — line 110 의 `source = (raw.get("source") or {}).get("name")` 를 dict|str 양쪽 처리 (DT 4.13 호환)
    - `.env` — `DT_API_KEY=odt_LI3gqllEKj5r0aeoD9xsMPNaeD1SQsmq` (UAT 한정 값, **commit 금지** — `.env.example` 만 갱신)
    - `.claude/settings.json` — chore PR #3 세션의 push allow (이미 main 에 있음 — 차분 0)
  - **worker 컨테이너의 휘발성 ad-hoc 도구 설치** (재시작 시 사라짐 — Dockerfile.worker 정식화 필요):
    - `apt install maven gradle composer ruby ruby-bundler cargo golang-go`
    - Gradle 4.4 (apt) → Gradle 8.10 (`/opt/gradle-8.10` 수동 설치, `/usr/local/bin/gradle` 심링크)
    - Go 1.19 (apt) → Go 1.22.0 (`/opt/go` 수동 설치, `/usr/local/bin/go` 심링크)
    - `pip install poetry` — Python deep dep resolver

작업 내용 (Part A → D 순서, dependency 따라):

[Part A] **UAT 패치 정식 commit** — working tree 의 미커밋 변경을 PR 로 정착:

1. **`Dockerfile.worker` 정식 수정** — 모든 SCA 빌드 도구 baked-in:
   - 기존 base: cdxgen 12+ + Java 21 (Temurin) + ORT + Trivy
   - 추가: `composer` + `maven` (3.8+) + `gradle 8.10+` + `ruby` + `ruby-bundler` + `cargo` + `go 1.22+` + 옵션 `dotnet-sdk-8.0` (Microsoft repo)
   - .trivyignore 카테고리 (chore PR #2) 보존, 이미지 크기 ~3.5GB 예상
   - 테스트: `docker exec celery-worker sh -c 'mvn --version && gradle --version && composer --version && ruby --version && bundle --version && cargo --version && go version'` 모두 동작

2. **`docker-compose.dt.yml` 정식 commit** — DT overlay:
   - dependencytrack/apiserver:4.13.2
   - 4 GB heap (-Xmx4G -Xms2G)
   - 8 OSV ecosystems pre-enabled (npm + Maven + PyPI + RubyGems + crates.io + Go + Packagist + NuGet)
   - backend / celery-worker 에 DT_URL + DT_API_KEY env 주입
   - ort/ 디렉터리 worker 에 ro 마운트
   - 첫 부팅 시 admin/admin 강제 패스워드 변경 → API key 자동 등록 hook (init script 또는 README 안내)

3. **`ort/rules.kts` 정식 commit** — UAT minimal kts (CLAUDE.md §v1 carry-over 의 v1 ruleset 정식 포팅 backlog 참조 코멘트 포함)

4. **`apps/backend/tasks/scan_source.py` 패치 정식 commit**:
   - `_fetch_source(mock_only=False)` — 본문 docstring 갱신 ("Phase 2 PR #9 의 mock_only fast-path 가 chore PR #4 에서 활성화됨")
   - ORT stage try/except — 신규 `OrtSkipped` 예외 또는 `log.warning("ort_stage_skipped", ...)` + scan 은 succeeded 로 종료. 코멘트에 ORT analyze stage 정식 추가 backlog 명시 (ortKotlin 통합 버그 → 별도 PR)
   - `_persist_components` 확장 — UAT 패치 그대로 정식화. `_LICENSE_CATEGORY_DEFAULTS` 의 30개 SPDX 매핑은 CLAUDE.md §라이선스 분류 와 정확히 일치 검증

5. **`apps/backend/tasks/dt_resync.py` 패치** — DT 4.13 source field shape 호환 (1줄)

6. **`.env.example` 갱신** — DT 4.13 + 8 OSV ecosystems 안내 + DT API key 등록 절차 README 링크

[Part B] **scan_source pipeline 의 다중 언어 pre-cdxgen 훅** — `_run_pipeline` 의 stage 2.5 신규:

```python
def _prepare_for_cdxgen(source_dir: Path, scan_uuid: uuid.UUID) -> None:
    """Run language-specific lockfile / dependency-resolution prep BEFORE cdxgen.

    cdxgen needs lockfiles to enumerate transitive deps; without them
    Ruby / Rust / Go / .NET return only direct deps (or 0) per the
    chore PR #4 ecosystem matrix (see docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md).
    """
    timeout = 300  # 5 min per language step

    if (source_dir / "Gemfile").exists() and not (source_dir / "Gemfile.lock").exists():
        _run_prep("bundle lock", ["bundle", "lock"], source_dir, timeout)
    if (source_dir / "Cargo.toml").exists() and not (source_dir / "Cargo.lock").exists():
        _run_prep("cargo generate-lockfile", ["cargo", "generate-lockfile"], source_dir, timeout)
    if (source_dir / "go.mod").exists():
        _run_prep("go mod tidy", ["go", "mod", "tidy"], source_dir, timeout)
    if any(source_dir.glob("*.csproj")) and shutil.which("dotnet"):
        _run_prep("dotnet restore", ["dotnet", "restore"], source_dir, timeout)


def _run_prep(name: str, cmd: list[str], cwd: Path, timeout: int) -> None:
    """Best-effort prep — log failure but don't abort the scan.
    cdxgen still produces partial SBOM from raw source if prep fails."""
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
        log.info("prep_finished", step=name, returncode=result.returncode)
        if result.returncode != 0:
            log.warning("prep_failed", step=name, stderr=result.stderr[:500])
    except subprocess.TimeoutExpired:
        log.warning("prep_timeout", step=name, timeout=timeout)
```

테스트:
- 단위: 7 ecosystem 별 fixture (Gemfile only / Gemfile + Gemfile.lock / Cargo.toml only / etc) 로 호출 분기 검증
- integration (docker-compose 환경): pilot-ruby + pilot-rust + pilot-go 가 portal scan 후 정상 component 수 검출 (Ruby ≥ 9, Rust ≥ 100, Go ≥ 20)

[Part C] **Findings polling race fix** — `_run_pipeline` 의 stage 6 (DT findings poll):

```python
def _poll_dt_findings_with_retry(
    *, dt_client: DTClient, breaker, dt_project_uuid: str, scan_uuid: uuid.UUID
) -> list[dict[str, Any]]:
    """Retry-with-backoff for the DT findings poll.

    DT runs vulnerability matching asynchronously after BOM upload
    (BOM_UPLOAD_ANALYSIS event). The first poll within ~1 second of
    upload typically returns 0 findings even when matches exist. This
    helper retries with exponential backoff up to 60 seconds total."""
    delays = [2, 4, 8, 16, 30]  # ~60s total budget
    for attempt, delay in enumerate(delays):
        time.sleep(delay)
        findings = breaker.call(lambda: dt_client.get_findings(project_uuid=dt_project_uuid))
        log.info("dt_findings_poll", attempt=attempt + 1, count=len(findings))
        if findings:
            return findings
    return []
```

대안 (더 깔끔): 별도 Celery task `delay_then_sync_findings.apply_async(args=[scan_id], countdown=15)` — 15초 후 finding sync 만 실행. 본 PR 에서는 retry-with-backoff 권장 (단순 + 시나리오 의존도 ↓).

[Part D] **DT 4.13 OSV 8 ecosystems pre-config + dt_resync 자동 스케줄** :

- backend lifespan 또는 별도 init script 가 DT REST 로 `vuln-source.google.osv.enabled` 를 8 ecosystem list 로 자동 설정 (idempotent — 기존 값과 다를 때만 PUT)
- Celery Beat 스케줄에 `dt_resync_task` 등록: `crontab(hour=2, minute=0)` 매일 새벽 2시 + 시작 시 1회 (CLAUDE.md §운영 표준)
- DT API key 등록 자동화: 첫 부팅 시 admin/admin 강제 패스워드 변경 + Automation team 의 default API key 추출 → 워커 환경변수로 주입 (정식 init script 또는 README 안내)
- Findings polling 로직이 OSV 미러 미완료 ecosystem 에 대해 friendly 한 partial 결과 반환 (현재는 0 returns silently)

핵심 라우팅:
  - **devops-engineer** (필수): Dockerfile.worker 빌드 도구 추가 + docker-compose.dt.yml 정식화 + 이미지 크기 / build cache 검증
  - **scan-pipeline-specialist** (필수): scan_source.py 의 5개 패치 정식화 + pre-cdxgen 훅 + retry-with-backoff
  - **backend-developer** (옵션): dt_resync.py 4.13 호환 + Celery Beat 등록 + DT init script
  - **test-writer** (필수): pre-cdxgen 단위 + integration test (7 ecosystem fixture) + retry-with-backoff 테스트
  - **db-designer**: 본 PR 은 schema 변경 0 — 호출 불필요
  - **security-reviewer** (Producer-Reviewer 라운드): subprocess 호출 시 cmd injection 방어 + DT API key 평문 로깅 금지 + 30개 SPDX 카테고리 매핑이 CLAUDE.md §라이선스 분류 와 정확히 일치하는지 검증

설계 제약:
  - **Phase 4 (알림) 시작 전 안정화 PR**. 본 PR 머지 후 다음 세션에서 Phase 4 진입 가능.
  - **새 endpoint 0건**, schema 변경 0건. 모든 변경은 scan pipeline + worker image + DT integration 레이어.
  - PostgreSQL only / Alembic forward-only / 인증 필수 / docker-compose V1 / `os.getenv()` 런타임 호출.
  - `_persist_findings` 의 `Vulnerability` 메타 의존은 dt_resync 가 선행 채워두는 패턴 유지 (UAT 에서 224k vulns 동기화 검증).
  - Dockerfile.worker 의 이미지 크기 증가 (~3.5GB) 는 Phase 8 hardening 에서 multi-stage build / minimal variant 분리 검토 (별도 PR backlog).

DoD (Definition of Done):
  - main CI 모든 잡 success.
  - `ruff check apps/backend` clean / `mypy apps/backend` clean.
  - `npm run lint` 0 errors / `npm run typecheck` clean.
  - 신규/변경 backend coverage ≥ 80%.
  - `docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml up -d` 만으로 모든 stack + DT 부팅.
  - **Ecosystem matrix 회귀 테스트** (직전 핸드오프 의 7개 pilot repo 가 portal scan 후 다음 결과 충족):
    - pilot-nodejs: components ≥ 470, licenses ≥ 460, vulns ≥ 1 (npm OSV 미러 완료 후)
    - pilot-java-maven: components ≥ 90, vulns ≥ 50
    - pilot-php: components ≥ 15, licenses ≥ 15
    - pilot-python: components ≥ 39, vulns ≥ 1 (PyPI OSV 미러 완료 후)
    - pilot-ruby: components ≥ 9 (이전 0 → 9 증가 검증)
    - pilot-rust: components ≥ 150 (이전 5 → 150+ 증가 검증)
    - pilot-go: components ≥ 20 (이전 3 → 20+ 증가 검증)
  - security-reviewer 평결 PASS.

비주문 (Part E — 본 PR scope 외, backlog 등재):
  - **ORT analyze stage 정식 통합** — 별도 PR (큰 변경, 통합 재설계 필요)
  - **cdxgen 다중 언어 license metadata fetch 보강** — 별도 PR (per-ecosystem post-process)
  - **cdxgen Gradle init.gradle 호환** — 별도 chore (ghcr.io/cyclonedx/cdxgen-java11:v12 또는 cdxgen 옵션 조정)
  - **Docker scan flow 통합** — `scan_container` task 활성화 + UI surface (Phase 4+ 또는 별도 PR)
  - **shadcn Tabs → @radix-ui/react-tabs swap** — chore PR #3 carry-over
  - **`assert_team_access` 잔여 마이그레이션** — chore PR #3 carry-over (project / project_detail / vulnerability 모듈)
  - **Phase 8 audit listener INSERT-PK race / byte-stable ETag / PII guidance** — Phase 8 hardening
  - 자세한 처리 계획은 `docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md` §"Part E backlog 처리 로드맵" 참조

세션 종료 시 docs/sessions/2026-05-XX-chore-pr4-pipeline-stabilization.md 를 docs/v2-execution-plan.md §7 양식으로 작성. 다음 세션 시작 지시문은 §5 양식으로 옵션 A (Phase 4 — 알림 시스템) + 옵션 B (chore PR #5 — Part E 의 cdxgen 다중 언어 license fetch 보강) 두 옵션 모두 등재.
