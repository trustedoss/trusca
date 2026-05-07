chore — security Medium 후속 + 다중 언어 license fetcher + Gradle 8 cdxgen 호환 + assert_team_access 잔여 + Trivy 5 HIGH 처리.

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = e3c4418 (chore PR #4 merge). 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 (deps hygiene) + chore PR #2 (worker image hardening + .trivyignore) + Phase 3 PR #10~#13 + chore PR #3 (size cap + nosniff + rate-limit + RFC 6266 + sql_safety + authz helper) + chore PR #4 (UAT 정식화 + 다중 언어 worker + pre-cdxgen prep + DT polling retry + celery-beat sidecar).

이번 세션 = chore PR #5 — chore PR #4 가 남긴 security follow-up + license-coverage 보강 + carry-over 정리. **새 도메인 0건 / 새 endpoint 0건 / schema 변경 0건**. license fetcher 만 신규 모듈 (post-process — scan pipeline 에 stage 신설 없음). chore PR #4 머지 후 Phase 4 (알림) 진입 전 안정화 라스트 마일.

직전 핸드오프(반드시 시작 시 읽기):
  - docs/sessions/2026-05-07-chore-pr4-pipeline-stabilization.md — chore PR #4 의 6 commit + security-reviewer Producer-Reviewer 결과 (PASS, Low #4 적용, Medium #1 #2 #3 이연). **§1.3 의 Medium #1 / #2 가 본 PR scope 핵심**, Medium #3 은 chore PR #4 의 fix(worker) commit `33bb921` 에서 이미 흡수됨.
  - docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md — Part E §4.1 의 license fetcher backlog 원본. pilot-* 9 ecosystem matrix (현재 Java/Python/Rust/Go 의 license unknown 비율 핀).
  - docs/sessions/2026-05-07-chore-pr3-hardening.md — assert_team_access helper 도입 컨텍스트 + license/obligation 5 사이트 부분 마이그레이션 결과 (carry-over 11 사이트 = project / project_detail / vulnerability 모듈).

시작 시 검증 (반드시):
  ```
  docker-compose -f docker-compose.dev.yml ps               # 6/6 healthy (celery-beat 추가됨)
  docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml ps  # +dtrack-api healthy
  gh run list --limit 3                                      # main 최신 success
  git status                                                 # working tree 검증
  ```

  **중요 — main 의 working tree 잔여**:
  - `.claude/settings.json` (modified) — chore PR #3 carry-over. push allow 정식 commit. **Claude 가 직접 commit 시도 시 self-modification 차단** — 사용자가 직접 `git add .claude/settings.json && git commit` 하거나, 본 세션의 별도 commit 으로 처리 (단, Claude 가 직접 settings 수정 권한 부여를 commit 으로 만들면 안 됨 — 이미 working tree 에 있는 사용자 변경사항을 그대로 stage 해서 commit 하는 것은 가능).
  - `docs/sessions/_next-session-prompt-chore-pr3.md` / `_next-session-prompt-chore-pr4.md` (untracked) — 완료된 prompt 들. **삭제 또는 archive/** 로 이동 권고.

작업 내용 (Part A → E 순서, dependency 따라):

[Part A] **subprocess env 화이트리스트** — security-reviewer Medium #1:

`apps/backend/tasks/scan_source.py:_run_prep` 가 worker process 의 모든 환경변수를 자식 subprocess (`bundle lock` / `cargo generate-lockfile` / `go mod tidy` / `dotnet restore`) 에 상속. `DT_API_KEY` / `SECRET_KEY` / `DATABASE_URL` (password 포함) / `*_WEBHOOK_URL` 모두 노출. 악성 NuGet feed (`nuget.config` 의 source override) 또는 Go `replace` directive 가 텔레메트리/크래시 경로로 secret 유출 가능.

```python
# 새 모듈 또는 _run_prep 내부 헬퍼:
_PREP_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "TZ",
    # Go-specific
    "GOFLAGS", "GOPROXY", "GOSUMDB", "GOMODCACHE",
    # Cargo-specific
    "CARGO_HOME", "RUSTUP_HOME",
    # .NET-specific
    "DOTNET_CLI_TELEMETRY_OPTOUT", "DOTNET_NOLOGO", "NUGET_PACKAGES",
    # Java/Maven
    "JAVA_HOME", "MAVEN_OPTS", "GRADLE_USER_HOME",
})

def _scrubbed_env() -> dict[str, str]:
    """Build a minimal env for prep subprocesses.

    Worker secrets (DT_API_KEY / SECRET_KEY / DATABASE_URL credentials /
    *_WEBHOOK_URL) must NOT be inherited by `bundle lock` / `cargo
    generate-lockfile` / `go mod tidy` / `dotnet restore` — those
    resolvers may fetch from attacker-controlled sources (NuGet feed
    / Go replace) per a hostile cloned repo, and any inherited env
    becomes a covert exfil channel."""
    base: dict[str, str] = {}
    for key in _PREP_ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value is not None:
            base[key] = value
    # Sensible defaults so the resolvers don't fall back to localized
    # behaviour or unknown-host telemetry.
    base.setdefault("HOME", "/tmp")
    base.setdefault("LANG", "C.UTF-8")
    base.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
    base.setdefault("DOTNET_NOLOGO", "1")
    return base
```

`_run_prep` 의 `subprocess.run(..., env=_scrubbed_env())` 으로 변경. 단위 테스트:
- `test_run_prep_passes_only_allowlisted_env` — DT_API_KEY/SECRET_KEY 가 자식 env 에 없음 검증 (capture_output 으로 자식이 echo 하는 env 를 확인)
- `test_run_prep_seeds_dotnet_telemetry_optout` — `.NET` telemetry 차단 기본값 핀

[Part B] **다중 언어 license fetcher** — Part E §4.1 핵심:

UAT matrix 의 license unknown 문제: Java/Maven 91 / Python 39 / Rust 164 / Go 29 컴포넌트가 모두 `kind="declared"` + `category="unknown"` (cdxgen 단독으로는 transitive 의존성의 라이선스 메타데이터 추출 미흡).

`apps/backend/integrations/license_fetcher/` 신규 모듈:

```
apps/backend/integrations/license_fetcher/
├── __init__.py            # dispatch by purl prefix
├── base.py                # LicenseFetcher protocol + cache layer
├── maven.py               # Maven Central API: search.maven.org
├── pypi.py                # PyPI JSON API: pypi.org/pypi/<name>/json
├── crates.py              # crates.io API: crates.io/api/v1/crates/<name>
├── pkggo.py               # pkg.go.dev (HTML scrape) or proxy.golang.org
└── tests/
    └── (per-fetcher tests with VCR fixtures)
```

각 fetcher 는:
- `fetch(purl: str) -> LicenseFetchResult | None` — 라이선스 SPDX id (또는 free-text → 기존 `_extract_spdx_ids` 와 같은 정규화) + reference URL.
- 결과는 PostgreSQL `licenses` 테이블 캐시 (`spdx_id` unique). 24h TTL 기준으로 신선도 확인 (또는 별도 `license_fetch_cache` 캐시 테이블 — schema migration 1건 동반).
- Rate-limit 친화: per-host concurrency 1, exponential backoff. crates.io 는 1 req/sec.

`scan_source._persist_components` 의 후처리:
- cdxgen 이 `licenses` 를 비워둔 컴포넌트만 fetcher 호출 (cdxgen 결과 우선).
- 결과를 `kind="concluded"` LicenseFinding 으로 emit (cdxgen 의 `kind="declared"` 와 구분). Phase 8 의 ORT analyzer 통합 시 양쪽 reconcile.

회귀 테스트:
- 단위: VCR cassettes (`pytest-recording`) 로 각 fetcher 의 happy path + 404 + 5xx + rate-limit 응답 핀.
- integration: pilot-java-maven / pilot-python / pilot-rust / pilot-go 4개 repo 에 대해 portal scan 후 license coverage ≥ 80% 검증 (각 ecosystem 별 separate test).

[Part C] **cdxgen Gradle 8 호환** — Part E §4.1:

UAT 의 pilot-java-gradle 0 component 이슈. cdxgen 의 init.gradle 주입이 Gradle 8 에서 실패 (`Could not get unknown property 'allprojects' for root project`).

대안 평가:
1. **`ghcr.io/cyclonedx/cdxgen-java11:v12` image variant** — 별도 worker image 또는 cdxgen 호출을 docker run 으로 위임. 무겁고 복잡.
2. **cdxgen 옵션 `--no-init-gradle` 또는 `--gradle-skip-init`** — 사용 가능하면 가장 깨끗.
3. **수동 init.gradle 주입** — `apps/backend/integrations/cdxgen.py` 에서 source_dir 에 임시 `init.gradle.kts` 작성 후 cdxgen 에 `--init-script` 전달.

권고: (2) → (3) → (1) 순서로 시도. (2) 가 동작하면 1줄 변경으로 끝. 동작 안 하면 (3) 으로 fallback.

회귀 테스트: pilot-java-gradle scan 후 component ≥ 30 (cdxgen manual scan 기준선).

[Part D] **assert_team_access 잔여 마이그레이션** — chore PR #3 carry-over:

`apps/backend/services/{project,project_detail,vulnerability}_service.py` 의 11 사이트:
- `_can_access_team(actor, team_id)` 호출 → `assert_team_access(actor, team_id, log=log, resource="project", resource_id=project.id, deny=lambda: ProjectNotFound(...))` 패턴으로 일괄 교체.
- 각 모듈의 `_can_access_team` alias 제거 (`from core.authz import can_access_team as _can_access_team` 삭제).
- IDOR 회귀 테스트: 각 endpoint 에 cross-team access 시 403 (or 404) + audit log 단일 라인 검증.

기계적 작업이라 한 commit. 11 사이트 grep 후 일괄 sed:
```
grep -rn "_can_access_team" apps/backend/services/{project,project_detail,vulnerability}_service.py | head -30
```

[Part E] **chore PR #4 carry-over 처리**:

1. **dt_resync.py:94 external_id isinstance 가드** — chore PR #4 security-reviewer Info follow-up. `raw.get("source", {}).get("name")` 가 DT 4.13 의 string source shape 에서 `AttributeError`. `vulnId` fallback 으로 가려지지만 일관성을 위해 동일 isinstance 분기 적용 (line 110-118 패턴).

2. **Trivy 신규 5 HIGH 처리** — chore PR #4 의 Maven 도구 도입으로 새로 노출:
   - `io.netty:netty-codec` 4.1.132.Final → 4.1.133.Final (CVE-2026-42583)
   - `io.netty:netty-codec-http` → 4.2.13.Final / 4.1.133.Final (CVE-2026-42584)
   - `org.bouncycastle:bcpg-jdk18on` 1.78.1 → 1.84 (CVE-2026-3505)
   - `org.codehaus.plexus:plexus-utils` 3.5.1 → 4.0.3 / 3.6.1 (CVE-2025-67030)
   - `python-multipart` 0.0.22 → 0.0.27 (CVE-2026-42561)
   처리 방안 (선호 순서):
   - **(a) dep bump**: python-multipart 는 `apps/backend/requirements.txt` 에서 직접 bump 가능. Maven JARs 는 ORT 85.0.0 릴리스에 종속 — 업스트림이 bump 안 했으면 (b) 로.
   - **(b) `.trivyignore` 등재**: 각 CVE 에 reach 분석 (`mvn dependency:tree` 가 SBOM enumeration 만 호출, 실제 jar 코드는 unreached). Category (3) — runtime-UNREACHED. 정책 헤더 양식대로.
   - hard-fail 정책 복원은 Phase 8 GA hardening 시점 — 본 PR 은 soft-fail 유지.

3. **shadcn Tabs → @radix-ui/react-tabs swap** — chore PR #3 carry-over. UI primitive 1:1 교체 + 5탭 deep-link e2e 회귀.

4. **`.claude/settings.json` push allow 정식 commit** — main working tree 의 사용자 의도된 변경 사항을 그대로 stage + commit. Claude 가 직접 settings 를 수정하는 것이 아니라, 이미 working tree 에 있는 변경을 commit 하는 것은 OK.

[Part F (옵션, 별도 PR 권고)] **Medium #2 — egress NetworkPolicy**:

본 PR 에 포함하면 scope 비대화. 별도 PR 분리 권고:
- `docker-compose.dev.yml` 의 `celery-worker` 에 egress allowlist (proxy 컨테이너 + iptables 또는 NetworkPolicy 시뮬레이션). dev 환경에서는 best-effort.
- `charts/trustedoss/` (Helm chart) 의 NetworkPolicy 리소스 — Phase 7/8.
- `docs/security.md` 의 egress 요구 명시 + 외부 호스트 allowlist (proxy.golang.org, repo1.maven.org, pypi.org, crates.io, registry.npmjs.org, dl.google.com).

권고: 본 PR 머지 후 별도 chore PR (devops-engineer 단독). 본 PR 의 §6 옵션으로만 등재.

핵심 라우팅:
  - **scan-pipeline-specialist** (필수): Part A (env 화이트리스트) + Part B (license fetcher base + dispatch) + Part C (Gradle 호환) + Part E.1 (dt_resync 가드).
  - **backend-developer** (필수): Part B 의 per-fetcher 구현 (Maven/PyPI/crates/pkg.go.dev) + Part D (assert_team_access 11 사이트).
  - **db-designer** (옵션): Part B 의 license_fetch_cache 캐시 테이블 도입 시 Alembic migration 1건. 또는 기존 `licenses` 테이블에 `last_fetched_at` 컬럼 추가 (forward-only).
  - **frontend-dev** (옵션): Part E.3 (Tabs swap).
  - **test-writer** (필수): Part A 단위 + Part B per-fetcher VCR + Part B integration (pilot-* 4 ecosystem) + Part C (Gradle pilot 회귀) + Part D (IDOR 회귀).
  - **devops-engineer** (옵션): Part E.2 의 .trivyignore 작성 (reach 분석 포함).
  - **security-reviewer** (필수): Producer-Reviewer 1 라운드 — env 화이트리스트의 누락 키 / fetcher 의 cache poisoning / Tabs swap 의 a11y 회귀.

설계 제약:
  - **Phase 4 (알림) 시작 전 마지막 안정화 PR**. 본 PR 머지 후 Phase 4 진입.
  - **새 endpoint 0건**, schema 변경 ≤ 1건 (license cache, forward-only).
  - PostgreSQL only / Alembic forward-only / 인증 필수 / docker-compose V1 / `os.getenv()` 런타임 호출.
  - License fetcher 는 외부 네트워크 의존 — 모든 호출은 timeout (30s) + retry (max 3) + per-host rate-limit. pytest 는 VCR cassettes 로 결정론적.
  - `.trivyignore` 추가는 CVE 별 reach 분석 + category (1/2/3) 명시 (정책 헤더 양식 그대로).
  - Trivy soft-fail 정책 변경 없음 (chore PR #4 의 `continue-on-error: true` 유지).

DoD (Definition of Done):
  - main CI 모든 잡 success (image-scan 는 soft-fail 이지만 step 결과는 검토).
  - `ruff check apps/backend` clean / `mypy apps/backend` clean.
  - `npm run lint` 0 errors / `npm run typecheck` clean.
  - 신규/변경 backend coverage ≥ 80%.
  - **License coverage 회귀**:
    - pilot-java-maven: licenses unknown ≤ 20% (이전 100% → 목표 ≥ 80% known)
    - pilot-python: licenses unknown ≤ 20%
    - pilot-rust: licenses unknown ≤ 20%
    - pilot-go: licenses unknown ≤ 30% (pkg.go.dev 의 메타 한계)
    - pilot-java-gradle: components ≥ 30 (Part C 의 효과)
  - **IDOR 회귀**: project / project_detail / vulnerability 의 cross-team 접근 시 403 + audit log 단일 라인.
  - security-reviewer 평결 PASS.

비주문 (본 PR scope 외, backlog 등재):
  - **Medium #2** — egress NetworkPolicy → 별도 chore PR (Part F).
  - **ORT analyze stage 정식 통합** → 별도 큰 PR.
  - **NVD API v2 fallback** → docs + .env.example 안내.
  - **Phase 8 audit listener INSERT-PK race / byte-stable ETag / PII guidance** → Phase 8.
  - **License cache TTL refresh background task** → Celery Beat 등록 (본 PR 의 fetcher 만으로 24h TTL 만료 시 인라인 재fetch 가능, 백그라운드 prefetch 는 별도).

세션 종료 시 docs/sessions/2026-05-XX-chore-pr5-security-license-fetcher.md 를 docs/v2-execution-plan.md §7 양식으로 작성. 다음 세션 시작 지시문은 §5 양식으로 옵션 A (Phase 4 — 알림) + 옵션 B (Medium #2 egress NetworkPolicy 단독 PR) 두 옵션 모두 등재.
