# Session Handoff — 2026-05-07 — UAT + 다중 언어 ecosystem 테스트 매트릭스 + chore PR #4 scope 도출

## 1. 무엇을 했나

본 세션은 **UAT (User Acceptance Testing) + 다중 언어 ecosystem 매트릭스 테스트**. PR commit 0건, 발견·정리 위주. 결과는 chore PR #4 의 scope 도출 + Part E backlog 의 처리 로드맵.

### 1.1 환경 복구 + UAT 셋업

- **postgres disk-full restart loop 해소**: 4 세션 연속 issue 였던 disk pressure 를 `docker system prune -a -f` 으로 52 GB 회수 후 재기동. 5/5 healthy.
- **PR #7 (chore PR #3) 머지**: feature/chore-pr3-hardening → main (37f7fc6). CI 9/9 success, security-reviewer PASS.
- **시드 데이터 (빠른 path)**: `seed_e2e_user.py --component-count 30 --vulnerability-count 25 --with-obligations` 로 `uat@example.com` (alpha + beta-empty) + `uat-kr2@example.com` (한글-프로젝트 + オープンソース) 두 계정 시드.
- **사용자 UAT 검증 결과**: 5 탭 UI/UX 모두 만족. "UI/UX가 너무 마음에 들어" — Black Duck/Snyk 수준의 enterprise SCA 컨셉이 의도대로 자리 잡음.

### 1.2 실제 GitHub 프로젝트 분석 — 3 → 10 ecosystems 확장

사용자 요청: 본인의 pilot 레포 3개 (nodejs / java-maven / java-gradle) 로 시작, 이후 7개 추가 (php / python / ruby / rust / go / dotnet / docker) 까지 매트릭스 검증.

**DT 셋업 (overlay 신규)**:
- `docker-compose.dt.yml` 신규 생성 (working tree only — **commit 안 됨**) — `dependencytrack/apiserver:4.13.2` + 4 GB heap + 8 OSV ecosystems pre-enabled (npm + Maven + PyPI + RubyGems + crates.io + Go + Packagist + NuGet)
- DT 4.12 → 4.13 업그레이드 (4.12 의 NIST NVD v1.1 deprecation 회피 + OSV.dev 통합 활용)
- 첫 부팅 시 admin/admin 강제 패스워드 변경 → `UATadmin1234!` + Automation team API key (`odt_LI3gqllEKj5r0aeoD9xsMPNaeD1SQsmq`) 추출 + Backend `.env` 의 `DT_API_KEY` 갱신
- Automation team 권한 추가: `BOM_UPLOAD` + `VIEW_PORTFOLIO` (default) → `PROJECT_CREATION_UPLOAD` + `VIEW_VULNERABILITY` + `VULNERABILITY_ANALYSIS` + `PORTFOLIO_MANAGEMENT` 추가
- OSV 미러 진척: Maven 완료 (157k advisories) + npm 진행 중 (수 만 개) + 나머지 6 ecosystem 활성화만 함 (미러는 시간 소요)
- 224,103 vulnerabilities 가 `dt_resync_task` 로 portal `vulnerabilities` 테이블에 동기화 완료 (449 pages, 0 skipped)

**scan pipeline 임시 패치 4건** (working tree only — **모두 commit 안 됨**):

1. `scan_source.py:_fetch_source(mock_only=False)` — Phase 2 PR #9 의 dead-code real git clone 활성화 (1 line 변경)
2. `scan_source.py:ORT stage try/except` — `ort evaluate` 가 cdxgen CycloneDX SBOM 을 OrtResult JSON 으로 잘못 파싱하는 broken integration 을 우회 (KotlinInvalidNullException 흡수, 파이프라인 계속)
3. `scan_source.py:_persist_components 확장` — UAT 의 핵심 추가 (~150줄). cdxgen SBOM 의 `licenses[].license.id` (SPDX) → `License` + `LicenseFinding` 변환 + `_LICENSE_CATEGORY_DEFAULTS` 30 SPDX 매핑 + `_classify_license_category` / `_extract_spdx_ids` / `_get_or_create_license` / `_persist_component_licenses` 신규 함수
4. `dt_resync.py:source field shape` — DT 4.12 (`{"name": "..."}`) → DT 4.13 (`"<name>"`) 호환 1줄 패치

**Worker 컨테이너 ad-hoc 도구 설치** (휘발성 — 컨테이너 재시작 시 사라짐):
- `apt install maven gradle composer ruby ruby-bundler cargo golang-go`
- Gradle 4.4 (Debian) → Gradle 8.10 (`/opt/gradle-8.10` 수동 설치 + 심링크)
- Go 1.19 (Debian) → Go 1.22.0 (`/opt/go` 수동 설치 + 심링크)
- `pip install poetry`

**ORT minimal rules.kts** 작성 (`ort/rules.kts` 신규 — **commit 안 됨**, ORT analyze stage 정식 통합 시까지 우회용)

### 1.3 Ecosystem 매트릭스 결과 (10개 pilot repo)

`✓` = 정상 / `△` = 부분/조건부 / `✗` = 미동작

| Ecosystem | Components (manual cdxgen) | Portal scan | Licenses | Transitive | DT Vulns | Worker tools / 전처리 요구 |
|---|---|---|---|---|---|---|
| **Node.js (npm)** | 470 | 470 ✓ | 465 allowed + 5 unknown ✓ | yes | (npm OSV 미러 완료 대기 중) | npm (built-in) |
| **Java/Maven** | 91 | 91 ✓ | 91 unknown △ | yes | **54 ✓** | `mvn` — apt 임시 |
| **Java/Gradle** | 0 | 0 ✗ | — | — | — | `gradle 8.10` — cdxgen init.gradle 호환 이슈 (별도) |
| **PHP** | 15 | 15 ✓ | 15 ✓ | yes | 0 (Packagist OSV 미러 대기) | `composer` — apt |
| **Python (pip)** | 39 | 39 ✓ | 0 ✗ | yes | 0 (PyPI OSV 미러 대기) | `pip` (built-in), license resolver 부재 |
| **Ruby** | 9 | **0 ✗** | 0 | 11 (manual) | — | `ruby` + `bundler` + `bundle lock` 사전실행 |
| **Rust** | **164** | **5 △** | 0 | 352 (manual) | — | `cargo` + `cargo generate-lockfile` 사전실행 |
| **Go** | 29 | **3 △** | 0 | 3 | — | `go 1.22+` + `go mod tidy` 사전실행 |
| **.NET** | 3 | 3 △ | 0 | direct only | — | `dotnet SDK` + `dotnet restore` 사전실행 |
| **Docker** | n/a | 0 ✗ | — | — | — | 별도 pipeline (`-t docker` 또는 `scan_container` task) |

**Manual ↔ Portal 차이의 의미**: Ruby / Rust / Go / .NET 의 portal scan 결과 < manual 결과. cdxgen 단독으로는 lockfile / dep-resolution prep 을 자동 수행하지 않으므로 transitive 가 0 또는 매우 낮음. **scan_source pipeline 에 stage 2.5 (pre-cdxgen prep) 추가 필요** — chore PR #4 Part B.

### 1.4 Vulnerabilities 검출 검증

- **Maven (54 vulns)** — DT OSV Maven 미러 후 `_persist_findings` 수동 sync (UAT 인라인 스크립트). 우리 portal 의 race condition (SBOM upload 직후 1초 내 finding poll → DT OSV 매칭 미완료) 으로 정상 scan flow 에서는 0. **Polling retry-with-backoff 필요** — chore PR #4 Part C.
- **다른 ecosystem (PHP / Python / Ruby / Rust / Go / .NET)** — DT OSV ecosystem 활성화는 했지만 미러 미완료 시점에 scan 실행 → 0 vulns. 미러 완료 후 재 scan 또는 finding sync 시 검출 예상.

## 2. 결정 사항 / 변경된 가정

- **chore PR #4 = pipeline stabilization PR** — 본 UAT 패치 4건 정식 commit + 다중 언어 worker 이미지 정식화 (Dockerfile.worker baked-in toolchain) + pre-cdxgen 훅 신규 + DT polling race fix. **새 도메인 0건**.
- **DT 4.13 표준 채택** — DT 4.12 의 NIST NVD v1.1 deprecation 으로 vulnerability matching 불가. DT 4.13 + OSV.dev 통합으로 8 ecosystem 커버. 본 UAT 가 정식 결정 근거.
- **OSV.dev 가 NVD 보다 우선 vulnerability source** — 8 ecosystem (npm + Maven + PyPI + RubyGems + crates.io + Go + Packagist + NuGet) 커버. NVD 는 DT 4.13 의 v2 API 로 보조 (별도 NVD API key 등록 필요 — backlog).
- **Worker 이미지 크기 증가 수용** — ~2.5 GB → ~3.5 GB. 이유: 다중 언어 ecosystem 검출의 핵심은 빌드 도구 가용성. Phase 8 hardening 에서 multi-stage / minimal variant 검토 (별도 PR).
- **`_persist_components` 의 license 변환 코드는 chore PR #4 정식화** — 원래 ORT analyze stage 의 책임이었으나 ORT 통합 broken 상태. cdxgen SBOM 의 `licenses[].license.id` 가 충분한 1차 source. 향후 ORT analyze 정식 통합 시 `concluded` / `detected` kind 추가.
- **Docker scan 은 source flow 와 분리** — `scan_container` task (Trivy 기반) 가 이미 존재. UI surface 활성화는 Phase 4+ 작업 (현재 source scan 만 UI 노출).

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), #6 (55e67bd), #7 (d7bc929 + 93c41a4), #8 (502f02f + ebb9c53), #9 (f55e70d + 9da6b3c), chore PR #1 (6366b62), chore PR #2 (38236e2), Phase 3 PR #10 (7d6f66d), Phase 3 PR #11 (e19bd8a), Phase 3 PR #12 (ac15d6a), Phase 3 PR #13 (a3634e3), **chore PR #3 (37f7fc6)**.
- **GitHub origin/main**: `37f7fc6` (chore PR #3 hardening 머지).
- **본 세션 commit 0건** — UAT 만 진행. 모든 발견 사항 다음 세션 chore PR #4 에서 정식 commit.
- **Working tree 미커밋 변경** (chore PR #4 의 정식 commit 대상):
  - 신규 파일: `docker-compose.dt.yml`, `ort/rules.kts`
  - 수정 파일: `apps/backend/tasks/scan_source.py` (3 곳), `apps/backend/tasks/dt_resync.py` (1줄), `.env` (DT_API_KEY — UAT 한정 값, **commit 금지**)
- **Worker 컨테이너 ad-hoc 도구** (재시작 시 사라짐): mvn / gradle 8.10 / composer / ruby / bundler / cargo / go 1.22 / poetry. **chore PR #4 의 Dockerfile.worker 정식화 필수**.
- **DT 환경**: 4.13.2 작동 중. admin/UATadmin1234!. Automation team API key 등록 완료. 8 OSV ecosystems 활성화 (Maven 미러 완료, 나머지 미러 진행중).
- **DB 상태**:
  - portal `vulnerabilities` 테이블: 224,103 rows (dt_resync_task 1회 호출 결과)
  - portal `vulnerability_findings` 테이블: pilot-java-maven 의 latest scan 에 54 rows + seed (alpha 45 + uat-kr2 7)
  - 7 ecosystem pilot 프로젝트 모두 portal 에 등록됨 (`uat@example.com` 의 team)
- **CI 미실행** — main 의 마지막 CI 는 chore PR #3 머지 시점.

## 4. Part E backlog 처리 로드맵

본 UAT 도중 발견된 항목 중 chore PR #4 scope 외로 분류한 것들. 처리 우선순위 / 분류 / 예상 PR 단위:

### 4.1 chore PR #5 후보 (작은 인프라 / 호환성 — 1세션 이내)

| 항목 | 이슈 | 처리 방안 | 우선순위 |
|---|---|---|---|
| **cdxgen Java transitive license 미추출** | pilot-java-maven 의 91 컴포넌트가 모두 `unknown` 라이선스 — pom.xml 의 `<licenses>` 가 transitive 의존성에는 자주 누락 | (1) cdxgen `--include-formulation` + `--profile research` 옵션 시도, (2) post-process 로 Maven Central API 에서 라이선스 fetch (per purl), (3) ORT analyzer 정식 통합 시 자동 해결 | 중 (Java 사용자에게는 핵심) |
| **cdxgen Python license resolver** | pilot-python 의 39 컴포넌트가 모두 `unknown` — pip metadata 의 license 필드 미추출 | cdxgen `--enable-license-fetcher` 또는 PyPI metadata 별도 fetch 후 `_persist_components` 에 주입 | 중 |
| **cdxgen Rust license** | 164 컴포넌트 모두 `unknown` — Cargo.toml 의 `license` field 가 transitive 에는 미포함 | crates.io API 로 per-crate license fetch (rate-limit 주의) | 낮 |
| **cdxgen Go license** | 동일 패턴 | pkg.go.dev API 또는 GitHub repo license fetch | 낮 |
| **cdxgen Gradle init.gradle 호환** | pilot-java-gradle 0 컴포넌트 — cdxgen 의 init.gradle 주입이 Gradle 8 에서 실패 | (1) `ghcr.io/cyclonedx/cdxgen-java11:v12` image variant 사용, (2) cdxgen 옵션으로 init script disable + manual gradle dependencies parse | 중 (Java 사용자) |
| **shadcn Tabs → @radix-ui/react-tabs swap** | chore PR #3 carry-over | UI primitive 1:1 교체 + 5탭 deep-link e2e 회귀 | 낮 |
| **`assert_team_access` 잔여 마이그레이션** | chore PR #3 의 license/obligation 만 마이그레이션, 나머지 3 모듈 (project, project_detail, vulnerability) carry-over | 11 사이트 기계적 교체 + IDOR 회귀 테스트 | 낮 |
| **DT findings polling Celery Beat 스케줄** | 현재 `dt_resync_task` 자동 주기 미실행 (수동 호출만) | `core/celery_beat.py` 에 등록: `crontab(hour=2)` + 시작 시 1회 | 중 (운영 표준) |
| **`.claude/settings.json` push allow 정식 commit** | UAT 도중 사용자 추가, 본 세션 미커밋 | 별도 1줄 commit | 낮 |

**chore PR #5 통합 권고**: 위 항목 중 license fetch 보강 (Java/Python/Rust/Go) + Gradle 호환 + Celery Beat 등록 4건을 한 PR 로 묶음. ~1.5 시간.

### 4.2 chore PR #6 후보 (Phase 8 hardening 일부)

| 항목 | 이슈 | 처리 방안 |
|---|---|---|
| **Phase 8 audit listener INSERT-PK race** | PR #11 review 발견. Vulnerability 상태 변경 시 audit row 의 target_id 가 INSERT 전 PK 사용 | SQLAlchemy `after_insert` 리스너로 변경 + 트랜잭션 커밋 보장 |
| **byte-stable ETag for vulnerability_findings** | PR #11 carry-over. JS Date.toISOString() ms 절단 회귀 위험 | row-version (BIGINT) 컬럼 신규 + Alembic migration |
| **`analysis_justification` PII guidance** | PR #11 Low #4. 사용자가 PII 를 justification 에 입력하는 위험 | doc-writer + regex secret reject + UI placeholder |
| **server-side references URL scheme allow-list** | PR #11 Info #2. 라이선스/취약점 reference URL 의 scheme 검증 | http(s) 제한 + javascript:/data: 차단 |
| **`audit_logs` lookup defense-in-depth team filter** | PR #11 Info #3. 팀 격리 추가 검증 | audit query 에 team_id 필터 |

### 4.3 큰 통합 작업 (별도 큰 PR)

| 항목 | 이슈 | 처리 방안 |
|---|---|---|
| **ORT analyze stage 정식 통합** | scan_source.py 가 `ort evaluate` 를 직접 호출하는데 cdxgen SBOM 을 OrtResult JSON 으로 잘못 입력. analyze stage 가 누락. **본 PR 의 try/except 우회는 임시** | (1) scan_source 에 ORT analyze stage (~5분 소요) 추가 → analyzer-result.yml 생성, (2) evaluate 에 analyzer-result 입력, (3) evaluate 결과의 `evaluated_packages[].license_findings` 를 `_persist_components` 와 합침 |
| **cdxgen → ORT analyzer 출력 마이그레이션** | 현재 cdxgen 만 사용. ORT analyzer 가 더 정확한 라이선스 + 라이센스 텍스트 식별 | 큰 변경 — 두 도구 결과 reconcile 또는 ORT 만 사용 |
| **Docker scan flow UI 노출** | `scan_container` task 가 이미 존재 (Trivy 기반). UI 의 scan trigger 에서 source / container 선택 surface | 1 PR — schema 의 ScanKind enum, UI scan trigger dropdown, 결과 페이지 단순 mapping |
| **NVD API v2 fallback** | DT 4.13 의 NVD API v2 사용 시 별도 API key 등록 필요 (free, nvd.nist.gov) | doc + .env.example NVD_API_KEY 안내 + DT config |

### 4.4 Phase 4 (다음 phase — 알림 시스템)

본 UAT 가 직접 영향을 주지 않음. CLAUDE.md §3 Phase 4 의 SMTP / Slack / Teams Webhook + 알림 센터 + per-team 채널 설정. chore PR #4 머지 후 진행.

### 4.5 v1 carry-over backlog (장기)

| 항목 | 이슈 | 처리 방안 |
|---|---|---|
| **v1 ort/rules.kts 정식 포팅** | 본 UAT 의 minimal kts 는 placeholder | v1 에서 ORT 라이선스 룰셋 그대로 copy + 카테고리 매핑 검증 |
| **PR #10 raw_data redaction (`mask_pii`)** | scan_components.raw_data 가 cdxgen 출력 그대로 — credential 포함 가능 | mask_pii 헬퍼 적용 |
| **PR #10 backlog Low #1** | severity / license_category enum router-level 검증 일부 도메인 미해결 | 도메인 별 검증 |
| **PR #9 follow-up backlog 7개** | python-jose → PyJWT, 야간 Trivy soft-fail 등 | 별도 chore PR 묶음 |
| **chore PR #2 carry-over** | cdxgen-plugins-bin, Dockerfile.worker base digest pin, Worker container `USER` 지시문, NodeSource signed-by deb, cdxgen `npm audit signatures` | Phase 8 hardening |

### 4.6 DB 인덱스 measure-first

50k+ rows 도달 시 검토:
- `audit_logs (target_table, target_id, created_at)` 복합 인덱스
- `license_findings (scan_id, license_id) INCLUDE (component_version_id)` partial composite
- `vulnerability_findings (scan_id, severity_rank, cvss_score DESC)` partial index
- NOTICE materialized view per-scan (Q2 p95 > 1s 일 때)

## 5. 다음 세션 시작 지시문

### 옵션 A — chore PR #4 — pipeline stabilization (권장, 안정화 우선)

```
chore — UAT 패치 정식화 + 다중 언어 worker 이미지 + pre-cdxgen prep + DT polling race fix.
[전문 → docs/sessions/_next-session-prompt-chore-pr4.md 참조]
```

본 PR 머지 후 다음 세션에서 Phase 4 (알림) 또는 chore PR #5 (Part E §4.1 묶음) 진입 가능.

### 옵션 B — chore PR #5 — multi-language license fetcher + Gradle 호환 (Part E §4.1)

```
chore — cdxgen 다중 언어 license fetcher 보강 + Gradle 호환 + Celery Beat 등록.

main HEAD = chore PR #4 merge commit (선결).

이번 세션 = Part E §4.1 의 5건 묶음:
  1. Maven Central / PyPI / crates.io / pkg.go.dev 의 per-purl license fetcher 도입 (post-process)
  2. cdxgen-java11 image variant 또는 init.gradle workaround 로 Gradle 8 호환
  3. dt_resync_task 의 Celery Beat 등록 (매일 새벽 2시)
  4. .claude/settings.json push allow 정식 commit
  5. 회귀 테스트: pilot-* 9 ecosystem 모두 license coverage ≥ 80%

[ ... 자세한 내용은 chore PR #4 머지 후 작성 ]
```

### 옵션 C — Phase 4 — 알림 시스템 (안정화 이후)

```
Phase 4 — 알림 시스템 (이메일 SMTP + Slack/Teams Webhook + 알림 센터 UI + per-team 채널 설정).

main HEAD = chore PR #4 merge commit (선결).

[ ... 핸드오프 양식은 chore PR #4 핸드오프 의 §5 옵션 A 참조 ]
```

## 권장 진행 순서

**chore PR #4 (안정화) → chore PR #5 (license fetcher + Gradle) → Phase 4 (알림)** 순서.

이유: chore PR #4 가 Phase 4 의 안정 기반 (worker 이미지 / DT 동작 / scan pipeline 정상화). chore PR #5 는 chore PR #4 의 ecosystem matrix 회귀 테스트 결과를 활용 (예: Java license unknown 91 → ≥ 80 allowed/conditional/forbidden). Phase 4 는 안정화 완료 후 신규 도메인 진입.
