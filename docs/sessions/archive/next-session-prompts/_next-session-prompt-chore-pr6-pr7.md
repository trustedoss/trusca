chore PR #6 + #7 — security-reviewer Medium 후속 (cdxgen/ORT env scrub + Maven reference_url 차단) + UAT 재검증.

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = b23871b (chore PR #5 squash merge). 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 (deps hygiene) + chore PR #2 (worker image hardening + .trivyignore) + Phase 3 PR #10~#13 + chore PR #3 (size cap + nosniff + rate-limit + RFC 6266 + sql_safety + authz helper) + chore PR #4 (UAT 정식화 + 다중 언어 worker + pre-cdxgen prep + DT polling retry + celery-beat sidecar) + chore PR #5 (security follow-up + license fetcher + assert_team_access).

이번 세션 = **두 개 chore PR 을 순차 진행**. 단일 세션 / 별도 PR / 별도 Producer-Reviewer 패스. **새 도메인 0건 / 새 endpoint 0건 / schema 변경 0건**. Phase 4 (알림) 진입 직전 마지막 보안 라운드.

- **chore PR #6** = security-reviewer Medium #1 v2 (cdxgen/ORT subprocess env scrub) + Low L4 (license fetcher follow_redirects=False).
- **chore PR #7** = security-reviewer Medium #2 (Maven license `reference_url` phishing 차단) + chore PR #5 의 Part B/C 효과 UAT 재검증.

직전 핸드오프 (반드시 시작 시 읽기):
  - `docs/sessions/2026-05-07-chore-pr5-security-license-fetcher.md` — chore PR #5 의 11 commit + security-reviewer PASS with conditions 결과. **§1.2 의 M1 / M2 / L4 항목이 본 두 PR 의 단일 진실**. §6 의 옵션 B / 옵션 C 가 작업 범위 정의.
  - `docs/sessions/2026-05-07-chore-pr4-pipeline-stabilization.md` — Part A (env 화이트리스트 prep 한정) 의 원본 컨텍스트. M1 v2 가 본 PR 으로 확장하는 surface 정확히 파악 필요.
  - `docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md` — UAT 매트릭스 기준선. chore PR #7 의 재검증 비교 기준.

시작 시 검증 (반드시):
  ```
  docker-compose -f docker-compose.dev.yml ps               # 6/6 healthy (celery-beat 포함)
  docker-compose -f docker-compose.dev.yml -f docker-compose.dt.yml ps  # +dtrack-api healthy
  gh run list --limit 3                                      # main 최신 success (b23871b)
  git status                                                 # working tree 검증 (untracked 제외 클린)
  ```

  **중요 — main 의 working tree 잔여**:
  - `docs/sessions/_next-session-prompt-chore-pr5.md` (untracked, 완료된 prompt) — 세션 시작 시 `docs/sessions/archive/next-session-prompts/` 로 이동 + 별도 commit (chore PR #6 의 첫 commit).
  - `docs/sessions/_next-session-prompt-chore-pr6-pr7.md` (untracked, 본 prompt) — 세션 종료 시 archive 로 이동 (다음 세션 prompt 의 첫 commit 으로).
  - `.claude/scheduled_tasks.lock` — 무시.
  - `docs/review-binaryanalysis-ng.md` — 사용자 작업 중 doc, 무시.

브랜치 전략:
  - `feature/chore-pr6-cdxgen-ort-env-scrub` 먼저 생성 → PR #6 머지 → main pull → `feature/chore-pr7-maven-url-phishing-uat-revalidation` 생성. **두 PR 을 동일 브랜치에 묶지 말 것** — 각각 별도 Producer-Reviewer 패스를 통과해야 한다.

═══════════════════════════════════════════════════════════════
[chore PR #6] cdxgen/ORT subprocess env scrub + license fetcher follow_redirects=False
═══════════════════════════════════════════════════════════════

## 배경

chore PR #5 의 Part A 가 prep subprocess (`bundle lock` / `cargo generate-lockfile` / `go mod tidy` / `dotnet restore`) 의 env 누출을 막았지만, security-reviewer 가 검토 중 **동일 위협 모델이 cdxgen 과 ORT 에도 그대로 적용**된다는 사실을 식별했다 (Medium #1 v2). cdxgen 은 Node 바이너리로 attacker-controlled `package.json` / `pom.xml` / `cdxgen.config.json` 을 cloned repo 에서 로드하고, ORT 는 JVM 도구로 `--rules-file` / `--ort-file` 등 attacker-influenced 입력을 받는다. 두 surface 모두 worker env 의 secret (DT_API_KEY/SECRET_KEY/DATABASE_URL/`*_WEBHOOK_URL`) 에 접근 가능 → telemetry / crash 보고 / DNS 룩업으로 exfil 가능.

추가로, license fetcher (chore PR #5 의 Part B) 는 `httpx.Client(..., follow_redirects=True)` 로 작성되어 레지스트리 응답 redirect 를 무검증으로 따라간다 (Low L4). 정상 운영 하 무위험이지만 defense-in-depth 측면에서 차단 권고.

## 작업 절차

### 1. `_scrubbed_env` 헬퍼 모듈 승격

현재 `apps/backend/tasks/scan_source.py` 안의 `_PREP_ENV_ALLOWLIST` + `_scrubbed_env` 를 `apps/backend/integrations/_subprocess_env.py` 로 이동 (chore PR #5 의 Part A + L1 흡수 결과 ~80 LoC). 기존 import 경로 (`from tasks.scan_source import _scrubbed_env`) 는 깨지지 않도록 scan_source.py 에 re-export 1줄 유지 (단위 테스트 호환).

새 모듈 헤더:
```python
"""
Subprocess env scrubbing for the scan pipeline.

CLAUDE.md 핵심규칙 #11 (os.getenv 런타임) + security-reviewer Medium #1
(chore PR #4) + Medium #1 v2 (chore PR #6) 의 통합 진실. prep / cdxgen /
ORT 어떤 subprocess 든 worker 의 모든 env 를 그대로 상속하면 hostile
clone 이 telemetry / crash / DNS 경로로 secret 을 exfil 할 수 있다.

세 가지 헬퍼 export:
- `scrubbed_env_for_prep()` — prep (bundle lock / cargo / go / dotnet)
- `scrubbed_env_for_cdxgen()` — cdxgen (Node) — NPM / NODE_PATH 추가
- `scrubbed_env_for_ort()` — ORT (JVM) — JAVA_OPTS / GRADLE_USER_HOME 추가
"""
```

세 함수 모두 공통 base allowlist (PATH/HOME/LANG/LC_ALL/TZ + corporate CA/proxy 변수, chore PR #5 4d2c619 의 L1 변경 그대로 보존) 를 공유하고, 각자 ecosystem-specific 추가 키만 union.

### 2. cdxgen 통합

`apps/backend/integrations/cdxgen.py:238` (또는 현재 `env = dict(os.environ)` 위치) 의 `env = dict(os.environ)` → `env = scrubbed_env_for_cdxgen()`.

cdxgen 의 핵심 추가 변수 (worker 가 cdxgen 호출 시 전달해야 함, secret 아님):
- `NODE_PATH`, `NODE_EXTRA_CA_CERTS`, `NODE_OPTIONS`
- `npm_config_*` (모든 ENV 중 prefix 매칭) — npm 의 user config. 단 secret 일 수 있는 `npm_config_authToken` / `npm_config__auth` / `npm_config_password` 등은 명시적으로 제외 (deny-list within allow-list — 정확한 키는 npm docs 참조).
- `CDXGEN_*` — operator override (chore PR #5 의 Part C 의 `CDXGEN_GRADLE_ARGS` 포함). 단 `CDXGEN_AUTH_*` / `CDXGEN_TOKEN_*` 은 제외.
- `npm_lifecycle_event`, `npm_package_*` — npm script 컨텍스트, secret 아님.

**중요**: `scrubbed_env_for_cdxgen` 의 구현은 prefix 매칭 + 키별 deny-list 조합. 단순 frozenset 으로 표현 안 됨 → helper 함수로 분리. 단위 테스트로 핀.

### 3. ORT 통합

`apps/backend/integrations/ort.py:147` 또는 현재 `subprocess.run(...)` 의 위치 (현재 env 인자 미지정 → 부모 env 전체 상속) 에 `env=scrubbed_env_for_ort()` 추가.

ORT 의 핵심 추가 변수:
- `JAVA_HOME`, `JAVA_OPTS`, `_JAVA_OPTIONS`, `JDK_JAVA_OPTIONS`
- `GRADLE_USER_HOME`, `GRADLE_OPTS`
- `MAVEN_OPTS`, `MAVEN_HOME`, `M2_HOME`
- `ORT_*` — operator override. 단 `ORT_*_TOKEN` / `ORT_*_KEY` 은 제외.

ORT 호출 시점에 ortrules.kts 가 attacker-controlled 인지 검토 — 현재 `apps/backend/integrations/ort.py` 의 `--rules-file` 인자가 `/opt/trustedoss/ort/rules.kts` (worker-controlled) 면 OK, scan workspace 내부 파일이면 hostile clone 이 ruleset 을 덮어쓸 수 있어 추가 위험. **소스 검토 후 명확히 보고**.

### 4. License fetcher follow_redirects=False (L4)

`apps/backend/integrations/license_fetcher/{maven,pypi,crates,pkggo}.py` 4 파일 각각의 `httpx.Client(..., follow_redirects=True)` → `False`. 3xx 응답 시:
- `log.warning("license_fetcher_unexpected_redirect", url=..., location=...)`
- 함수는 `None` 반환 (negative cache 등록).

각 fetcher 마다 단위 테스트 1건 추가 (총 4건):
```python
def test_maven_fetcher_does_not_follow_redirects(...):
    with httpx_mock as mock:
        mock.add_response(url="https://repo1.maven.org/...", status_code=302, headers={"Location": "https://attacker.example/license"})
        result = MavenLicenseFetcher().fetch("pkg:maven/org.example/foo@1.0")
        assert result is None  # negative
        # attacker host 호출 안 됨 검증 (mock 의 호출 횟수)
```

### 5. 단위 테스트

기존 `tests/unit/tasks/test_scan_source_prep.py` 의 2건 (`test_run_prep_passes_only_allowlisted_env`, `test_run_prep_seeds_dotnet_telemetry_optout`) 와 동일 패턴으로:
- `tests/unit/integrations/test_cdxgen.py` (또는 신규 `test_cdxgen_subprocess_env.py`) — DT_API_KEY 등 secret 차단 + NODE_EXTRA_CA_CERTS 통과 + npm_config_authToken 차단.
- `tests/unit/integrations/test_ort.py` — DT_API_KEY 차단 + JAVA_OPTS 통과 + ORT_AUTH_TOKEN 차단.
- `tests/unit/integrations/_subprocess_env_test.py` — base allowlist + 3개 ecosystem 각각의 추가 키 + deny-within-allow 분기 핀.
- License fetcher follow_redirects 테스트 4건 (위 4 항목).

### 6. 회귀

`pytest tests/unit tests/integration` 전체 green. 신규/변경 line coverage ≥ 80%. ruff/mypy clean. npm lint/typecheck/test 그대로 (frontend 변경 없음).

### 7. security-reviewer Producer-Reviewer

scope:
- 새 헬퍼의 누락 키 (특히 cdxgen 의 npm_config_* prefix 매칭 정확성, ORT 의 JVM 옵션 누락)
- 4개 fetcher 의 redirect 차단 일관성
- 회귀: prep subprocess 의 동작 (chore PR #5 Part A) 가 깨지지 않는가
- 새 위험: helper 승격 과정에서 import cycle / monkeypatch 깨짐

### 8. 핵심 라우팅

- **scan-pipeline-specialist** (필수): 1~3 + 6 + 헬퍼 모듈 승격.
- **backend-developer** (필수): 4 (license fetcher follow_redirects).
- **test-writer** (필수): 5 (단위 11+건).
- **security-reviewer** (필수): 7 (1 라운드).

### 9. DoD

- 신규/변경 line coverage ≥ 80%.
- ruff / mypy clean.
- security-reviewer PASS (M1 v2 / L4 모두 closed, 새 발견 < Medium).
- PR #6 open + CI 9/9 green + squash merge.

### 10. 예상 변경

- 신규 파일: `apps/backend/integrations/_subprocess_env.py` (~120 LoC) + 단위 테스트 ~5 파일 (~250 LoC).
- 수정: scan_source.py / cdxgen.py / ort.py / license_fetcher/{4} = 7 파일.
- 총 ~400 LoC 추가, ~50 LoC 삭제.
- commit 4~5건 (logical 단위): (1) helper 모듈 승격 + scan_source re-export, (2) cdxgen 통합, (3) ORT 통합, (4) license fetcher follow_redirects, (5) 단위 테스트 일괄 (또는 각 통합 commit 안에 동반).

═══════════════════════════════════════════════════════════════
[chore PR #7] Maven license `reference_url` phishing 차단 + UAT 재검증
═══════════════════════════════════════════════════════════════

**chore PR #6 머지 후 시작**. main 에서 새 브랜치.

## 배경

security-reviewer Medium #2 — license fetcher 의 4 ecosystem (Maven/PyPI/crates/pkg.go.dev) 모두 attacker-controlled 메타데이터 (POM `<url>`, PyPI `home_page`, crates.io `homepage`, pkg.go.dev HTML) 를 그대로 `reference_url` 로 저장. 프론트의 `LicenseDrawer.tsx:51-59` 의 `isSafeUrl()` 는 `javascript:` / `data:` 만 차단하고 임의 HTTPS phishing URL 은 그대로 클릭 가능한 링크로 렌더. NOTICE / Excel / PDF 템플릿 (Phase 5+) 은 동일 검증 없을 수 있음 → 향후 위험 누적.

추가로, chore PR #5 의 Part B (license fetcher) + Part C (cdxgen Gradle 8 호환) 는 단위 테스트로 핀했지만 **실제 5 pilot 레포에서의 효과 (license unknown ≤ 20%, gradle component ≥ 30) 는 아직 검증 안 됨**. UAT 재검증 필수.

## 작업 절차

### 1. `reference_url` 처리 결정

권고: **(b) fetcher-derived `reference_url` 전체 drop**. 이유:
- 프론트는 이미 `LicenseDrawer.tsx:194` 에서 SPDX id 기반 fallback (`https://spdx.org/licenses/<id>.html`) 보유.
- (a) allow-list 는 유지 비용 (license-text host 변경 시 추적) + 누락 위험.
- 단순 + 일관성 + i18n / NOTICE / PDF 영향 최소.

대안 (a) 를 채택할 경우 hosts:
```
opensource.org, www.opensource.org, apache.org, www.apache.org, gnu.org,
www.gnu.org, creativecommons.org, www.creativecommons.org, spdx.org,
www.spdx.org, eclipse.org, www.eclipse.org, mozilla.org, www.mozilla.org,
opensource.linuxfoundation.org, choosealicense.com
```
(scheme `https://` 강제 + path 검증 X).

**추천 (b) 진행**:

### 2. 코드 변경 (b 채택)

```python
# apps/backend/integrations/license_fetcher/base.py
@dataclass(frozen=True)
class LicenseFetchResult:
    spdx_id: str
    reference_url: str | None = None  # 보존 (cache 호환), 단 항상 None 으로 emit
    source: str
```

각 fetcher (`maven.py`, `pypi.py`, `crates.py`, `pkggo.py`) 의 `LicenseFetchResult(...)` 호출에서 `reference_url=None` 명시 (또는 default 사용). POM / API 응답에서 url 추출 로직은 유지하되 emit 시 누락.

### 3. cache 마이그레이션 정책

`license_fetch_cache.reference_url` 컬럼은 forward-only 로 보존 (스키마 변경 X). 24h TTL 내 기존 row 는 자연스럽게 갱신 (재fetch 시 reference_url=None 으로 update). DB 정리 SQL 마이그레이션은 X.

UI 영향: 기존 cache row 가 24h 동안 phishing URL 을 보유할 수 있음 → **즉시 무효화 권고**:
```sql
UPDATE license_fetch_cache SET reference_url = NULL, fetched_at = '1970-01-01';
```
or Alembic data migration 1건 (chore PR #4 의 정책 #6 과 일관 — schema/data 분리). data migration 권고.

### 4. 단위 + integration 테스트

- 각 fetcher 단위 테스트에서 `reference_url is None` 검증 (4건).
- VCR cassette 갱신 불필요 (응답 데이터는 같고, 파싱만 다름).
- License fetcher integration test (`test_license_fetcher_integration.py`) 의 LicenseFinding emit 시 `reference_url is None` 검증.

### 5. UAT 재검증 (5 pilot)

기준선: `docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md` 의 Part E §4.1.

**자동화 권고** (devops-engineer / test-writer):
- `scripts/uat-license-coverage.sh` 신규 (or `apps/backend/tests/uat/test_license_coverage.py` mark `live`):
  - 5 pilot 레포 (config / env 에서 URL 읽기) 클론.
  - portal 의 trigger_scan endpoint (또는 직접 Celery task) 호출.
  - scan 완료 후 portal API `/api/v1/projects/{id}/components` 와 `/licenses` 에서 카운트:
    - `licenses_unknown_ratio = unknown / total`
    - `components_count`
  - assertion: java-maven / python / rust ≤ 20%, go ≤ 30%, java-gradle 의 components ≥ 30.

5 pilot 정의 (UAT 매트릭스 §4.1 그대로):
- pilot-java-maven: e.g. `https://github.com/spring-projects/spring-petclinic` (마이크로 + 91 deps).
- pilot-java-gradle: e.g. `https://github.com/spring-projects/spring-petclinic-rest` (Gradle build).
- pilot-python: e.g. `https://github.com/pallets/flask` (39 deps).
- pilot-rust: e.g. `https://github.com/clap-rs/clap` (164 deps).
- pilot-go: e.g. `https://github.com/spf13/cobra` (29 deps).

UAT 결과 → `docs/sessions/2026-05-XX-uat-multi-ecosystem-matrix-v2.md`. 기준선 대비 delta + chore PR #5 의 효과 측정 + chore PR #7 의 reference_url=None drop 후 결과 (시각적 회귀 X 검증).

### 6. security-reviewer Producer-Reviewer

scope:
- (b) drop 의 회귀 검증: 모든 fetcher 가 None emit, 4 fetcher 일관성.
- data migration 의 멱등성 + rollback 정책.
- frontend 의 `null reference_url` 렌더 회귀 (이미 fallback 로직 보유 — 시각적 확인 권고).
- UAT 결과의 신뢰성: 5 pilot 의 절대값 + chore PR #5 효과 (license unknown 비율) 가 약속한 수준에 도달했는가.

### 7. 핵심 라우팅

- **backend-developer** (필수): 1~4 (fetcher reference_url drop + data migration + 단위 테스트).
- **db-designer** (옵션): 3 의 data migration revision 작성 (Alembic 명시).
- **test-writer** (필수): 5 (UAT 자동화 또는 manual report).
- **devops-engineer** (옵션): 5 의 UAT 자동화 스크립트 (5 pilot 레포 fetch + portal scan trigger).
- **security-reviewer** (필수): 6 (1 라운드).
- **doc-writer** (옵션): UAT v2 결과 문서.

### 8. DoD

- 신규/변경 line coverage ≥ 80%.
- ruff / mypy / lint clean.
- UAT 5 pilot 의 license-coverage 기준 달성 (java-maven / python / rust ≤ 20%, go ≤ 30%, java-gradle components ≥ 30).
- security-reviewer PASS (M2 closed, 새 발견 < Medium).
- PR #7 open + CI 9/9 green + squash merge.
- UAT v2 핸드오프 문서 작성.

### 9. 예상 변경

- 수정: 4 fetcher 파일 (각 1~3 LoC 변경) + base.py (default 변경) + scan_source 의 _persist_component_licenses 호출 (변경 없음, dataclass 가 None 제공).
- 신규: data migration `apps/backend/alembic/versions/0005_clear_license_fetch_cache_reference_url.py` (멱등성 보장).
- 신규: UAT 자동화 (별도 폴더 `scripts/uat/` 또는 `apps/backend/tests/uat/`) — 100~200 LoC.
- 신규: UAT v2 결과 문서 ~150 LoC.
- 총 ~500 LoC 변경 (대부분 UAT + 문서).
- commit 3~4건: (1) reference_url drop + 단위, (2) data migration, (3) UAT 자동화 + 결과, (4) 문서.

═══════════════════════════════════════════════════════════════
공통 설계 제약
═══════════════════════════════════════════════════════════════

- PostgreSQL only / Alembic forward-only / 인증 필수 / docker-compose V1 (하이픈) / `os.getenv()` 런타임 호출 (모듈 레벨 캐싱 X) / docker image `:latest` 금지.
- VEX 상태 enum 7-state 보존 (수정하지 말 것).
- Optimistic concurrency 패턴 (`if_match` echo + `SELECT FOR UPDATE`) 보존.
- Trivy soft-fail 정책 유지 (chore PR #4 의 `continue-on-error: true`).
- 새 도메인 / endpoint / schema 변경 0건 (chore PR #7 의 data migration 1건 제외, schema 변경 아님).
- 본 두 PR 은 **하나의 세션에서 순차 처리**. PR #6 머지 → main pull → PR #7 시작.
- 핵심 보안 코드 (subprocess env scrub, fetcher URL 처리) 는 Producer-Reviewer 패스 통과 필수.
- 한 commit 이 너무 커지면 logical 단위로 분할 (예: PR #6 의 cdxgen / ORT 통합은 별도 commit).
- 사용자가 직접 push / merge 권한 보유 (chore PR #5 settings.json 정책). force-push 는 명시 승인 필요.

═══════════════════════════════════════════════════════════════
세션 종료 시
═══════════════════════════════════════════════════════════════

1. `docs/sessions/2026-05-XX-chore-pr6-cdxgen-ort-env-scrub.md` 작성 (chore PR #6 핸드오프, `docs/v2-execution-plan.md` §7 양식).
2. `docs/sessions/2026-05-XX-chore-pr7-maven-url-uat-revalidation.md` 작성 (chore PR #7 핸드오프).
3. UAT v2 결과 별도 문서 (`docs/sessions/2026-05-XX-uat-multi-ecosystem-matrix-v2.md`) 작성.
4. `docs/sessions/_next-session-prompt-chore-pr6-pr7.md` (본 prompt) 를 archive 로 이동.
5. 다음 세션 prompt 작성: **Phase 4 (알림 시스템) PR #14 진입** — chore PR #5 핸드오프 §6 의 옵션 A 그대로 사용.

═══════════════════════════════════════════════════════════════
주의·블로커
═══════════════════════════════════════════════════════════════

- **cdxgen 의 npm_config_* prefix 매칭** — 누락 시 cdxgen 동작 깨질 수 있음 (NPM 의 user config 가 못 닿음). 단위 테스트로 핀 + integration test 에서 cdxgen 실제 호출 검증.
- **ORT 의 JVM 옵션** — `JAVA_OPTS` 누락 시 ORT 가 OOM. chore PR #4 의 worker image 의 JVM 메모리 설정 확인.
- **License fetcher cache 무효화** — chore PR #7 의 data migration 이 멱등성 (재실행 시 추가 영향 없음) + rollback (downgrade) 정책 명확.
- **UAT 재검증의 환경 의존성** — 5 pilot 레포가 GitHub 의 master/main 브랜치 변경에 노출. 본 PR 에서는 commit SHA 핀 권고 (재현성).
- **chore PR #6 와 #7 의존성** — PR #7 의 fetcher 수정은 PR #6 의 follow_redirects=False 변경과 같은 파일 영향. **PR #6 먼저 머지 → main pull → PR #7 시작**. merge conflict 회피.
- **CI image-scan soft-fail** — Trivy 가 새로운 HIGH 발견 가능 (npm transitive / Go transitive 등). 본 PR 의 변경은 의존성 bump 없으므로 신규 CVE 거의 없을 것이지만 monitoring 필요.

본 prompt 의 작업량: **단일 세션 내 처리 가능** (chore PR #6 ~3시간, chore PR #7 ~3시간 + UAT 재검증 ~1~2시간 = 총 7~8시간). 백그라운드 에이전트 활용 적극 권고. 세션 시간이 부족하면 **chore PR #6 만 처리하고 PR #7 은 별도 세션** 으로 분리해도 OK (그럴 경우 본 prompt 의 PR #7 부분만 다음 세션 prompt 로 발췌하여 archive 가 아닌 다음 세션 시작 지시문으로 재명명).
