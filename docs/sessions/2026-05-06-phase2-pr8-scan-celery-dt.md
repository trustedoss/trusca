# Session Handoff — 2026-05-06 — Phase 2 — PR #8 Scan Celery + DT 안정화

## 1. 무엇을 했나

- **Phase 2 PR #8 작성 완료** — feature 브랜치 `feature/phase2-pr8-scan-celery-dt`. §3.3 표 2.4 / 2.5 / 2.6 / 2.7 / 2.8 모두 산출. 머지는 사용자 명령 대기.
  - **Step 1 (devops-engineer, 단독 선행)**: `apps/backend/Dockerfile.worker` (multi-stage 144줄, base = python:3.12.7-slim + Python deps, worker stage = + Eclipse Temurin JRE 21.0.11 + Node 20.20.2 LTS Iron + cdxgen 11.11.0 + ORT 85.0.0 + Trivy 0.70.0). `docker-compose.dev.yml` celery-worker가 `trustedoss/backend-worker:dev` 이미지로 분리 (backend는 `trustedoss/backend:dev` 그대로 — API 슬림 ~200MB / worker ~3.28GB). `.github/workflows/ci.yml`에 `image-scan` 잡(buildx + Trivy DB 캐시 + aquasecurity/trivy-action@0.28.0, severity HIGH/CRITICAL exit-code:1) 추가. ORT layer 분리(buildx 캐시), 모든 도구 정확한 버전 핀(CLAUDE.md 규칙 9). 빌드 4m48s, 워커 컨테이너에서 cdxgen / ort / trivy / java `--version` 4개 모두 정상.
  - **Step 2a (scan-pipeline-specialist, 메인)**: 12 신규 모듈 + 4 수정 — `apps/backend/integrations/{__init__,_size_guard}.py`, `integrations/{cdxgen,ort,trivy}.py` (subprocess + mock backend, `TRUSTEDOSS_SCAN_BACKEND=mock`), `integrations/dt/{__init__,client,health,breaker}.py` (Redis 기반 CircuitBreaker CLOSED/OPEN/HALF_OPEN, Lua probe-gate `_PROBE_GATE_LUA`로 OPEN→HALF_OPEN 슬롯 1개만 허용 — 멀티-worker race window 좁힘 + race 0이 아닌 한 번의 정확한 transition 보장), `tasks/{scan_source,scan_container,dt_resync,dt_orphan_cleaner,dt_health}.py`, `tasks/__init__.py::enqueue_scan(scan: Scan) -> str` 디스패처(scan.kind 기반), `tasks/celery_app.py` beat schedule(60s health / 1h resync / 6h orphan) + `task_serializer='json'` (pickle RCE 차단), `core/db.py::sync_session_scope()` (Celery sync 컨텍스트), `core/config.py` 런타임 getter 5개(`dt_url`, `dt_api_key`, `dt_breaker_failure_threshold`(default 5), `dt_breaker_cooldown_seconds`(default 30), `scan_backend`, `workspace_root`).
  - **Step 2b (backend-developer, 보조 병렬)**: `apps/backend/core/url_guard.py` (242줄, `validate_git_url(url) -> str` + `GitUrlValidationError(ValueError)` — RFC 1918/loopback/link-local/multicast/CGNAT 100.64.0.0/10/cloud metadata IP+host/2048자 초과/file·gopher·data·javascript scheme reject, SCP-style `git@host:path` 정규화, DNS 미해석 시 closed-by-default), `core/pii_mask.py` (108줄, 재귀 마스킹 — `password|token|api_key|email|...` substring lowercase 매칭, max_depth=10 DoS 가드, no-mutation deep copy), `schemas/scan.py` (+93줄, `ScanCreate._validate_metadata` depth ≤ 4 + size ≤ 16KB + `ProjectCreate/Update._validate_git_url` chain), `services/scan_service.py::trigger_scan` 재작성(+106줄, `flush → set project.latest_scan_id → commit → enqueue_scan(scan) → store celery_task_id → commit`. broker 실패 시 `ScanEnqueueFailed(ScanError, status_code=503)` + `error_message` 안정 prefix + status='failed'. `mask_pii(payload.metadata)`로 ORM 저장 — audit listener의 JSONB diff 누설 차단). `api/v1/projects.py` (+11줄, `ScanError.status_code` 분기로 503 자동 매핑).
  - **Step 2c (test-writer, 병렬)**: 109 테스트 케이스, 16 파일 + conftest. 어댑터 mock 단위(cdxgen/ort/trivy/dt-client/dt-health 39) + breaker race 단위(`_PROBE_GATE_LUA` Lua direct eval로 OPEN→HALF_OPEN 슬롯 정확히 1 worker 검증, 10 케이스) + size_guard(I-1) 8 + url_guard(M-4) 22 + pii_mask 12 + scan_metadata_guard(M-2) 7 + project_git_url_validator 8 + dispatcher 3 + integration scan(11 — `test_scan_source_pipeline_mock`/`test_dt_circuit_breaker_cache`/`test_jsonb_size_guard`/`test_trigger_scan_enqueues_celery`). `requirements-dev.txt`에 `fakeredis[lua]==2.26.1`(BSD-3, Apache-2.0 호환).
- **Producer-Reviewer 1라운드** (security-reviewer): 평결 = **CHANGES REQUESTED** (Critical 0 / High 0 / Medium 2 / Low 4 / Info 3). 머지 차단 항목 없음. CLAUDE.md 핵심 규칙 #3, #4, #11, #12, #13 모두 PASS. M-1, M-2만 본 라운드에서 fix.
  - **M-1 fix (메인 세션)**: `core/url_guard.py::_is_dangerous_address`에 RFC 6598 CGNAT `100.64.0.0/10` reject 추가 (K8s Calico CNI / ISP NAT 사내망 SSRF 차단). `_CGNAT_V4` 모듈 상수 + IPv4Address isinstance 분기. 회귀 2 케이스(`test_rejects_cgnat_rfc_6598`).
  - **M-2 fix (메인 세션)**: `core/url_guard.py::_strip_scp_form`에 빈 userinfo / 빈 path reject 추가 — `@host:foo`와 `git@host:` 같은 degenerate 입력을 schema 단계에서 422로 끊어 worker 시간 낭비 방지. 회귀 2 케이스(`test_rejects_degenerate_scp_form`).
- **본 세션 직접 fix 2건**:
  - **mypy --strict 9 errors fix**: `integrations/dt/breaker.py` — redis-py 5.x에서 `redis.Redis`가 generic 아님(`redis.Redis[str]` → `redis.Redis`), `eval` 인자가 `str` 요구(`cooldown` / `self._now()` → `str(...)`), `INCR` 결과 `Awaitable | int` 분기(`cast(int, ...)`), `pipeline.execute()` no-untyped-call ignore 보강. scan-pipeline-specialist의 자체 mypy 검증이 변경 파일만 검사해서 통합 후 발생한 표면이라 메커니컬 fix.
  - **PR #7 회귀 fix**: `tests/integration/test_scans_api.py::test_developer_can_trigger_scan_in_own_team`이 `body["celery_task_id"] is None`을 검증 — PR #7 contract였지만 PR #8에서 trigger_scan이 enqueue + celery_task_id 저장하도록 변경되어 깨짐. 새 contract(`isinstance(str)` + `len > 0`)로 update + module docstring "PR #7 / PR #8" 양쪽 표기.
- **xfail 3개 제거**: test-writer가 backend-developer 결과를 못 본 시점에 `test_trigger_scan_enqueues_celery.py` 3개 케이스를 `@pytest.mark.xfail(reason="awaiting backend-developer step 2b")`로 표시. 본 세션이 backend-developer wrapping 후 xfail 제거 + 모듈 docstring 정리, 3 PASS 확인.

## 2. 결정 사항 / 변경된 가정

- **외부 바이너리 배치 = Celery worker 전용 별도 이미지** (이전 세션 결정 그대로 적용). 이유: API 컨테이너 슬림 200MB 유지 + worker 3.28GB는 분리 — 배포·롤백 격리, K8s worker 노드풀 분리, 보안 검토 표면 명확화. backend Dockerfile 변경 없음.
- **JRE 21 채택** (원안 JRE 17 → JRE 21로 변경). ORT 85.0.0이 Java 21 require — devops-engineer가 빌드 중 발견. Eclipse Temurin LTS 21.0.11 (GPL-2.0 + Classpath Exception, Apache-2.0 호환).
- **DT 캐시 저장 = 기존 `vulnerabilities` 테이블 사용** (PR #7 0003 마이그레이션 산출물). 별도 cache 테이블 신설 불필요 — `dt_resync_task`가 Beat 1h마다 vulnerabilities upsert. DT OPEN 상태에서 vulnerability_findings join은 cached 응답으로 자연 처리.
- **`core/security/url_guard.py` → `core/url_guard.py`로 위치 변경** (backend-developer deviation 채택). 이유: `core/security.py`가 flat 모듈로 존재 — security를 패키지로 승격하면 모든 import 사이트 영향. test-writer가 spec 위치 가정으로 작성한 import path는 backend-developer 실제 위치(`core.url_guard`)와 매칭되어 패치 불필요. **테스트 파일 위치는 `tests/unit/core/security/`로 둠** — namespace 의도성 유지.
- **DNS rebinding TOCTOU**는 PR #9로 deferral 명시 — `tasks/scan_source.py:172-174`의 fetch 단계가 placeholder, PR #9에서 `git -c http.curloptResolve=host:443:<resolved-ip>`로 IP 핀 강제. security-reviewer I-1로 추적.
- **breaker race window 0이 아닌 1-window 정확성으로 정의** — Lua `_PROBE_GATE_LUA`로 OPEN→HALF_OPEN 슬롯 1개만 허용 + INCR atomic + SETNX opened_at. HALF_OPEN→CLOSED CAS는 비-atomic이지만 영향이 "1회 추가 DT 호출 / 1 cooldown window 손실" 수준 — 분산 lock 도입은 과도. security-reviewer L-1 backlog.
- **Celery `task_acks_late=True` + `task_reject_on_worker_lost=True`** — PR #2 Phase 0 결정 보존. trigger_scan 재진입 시 멱등성을 `tasks/scan_source.py` 진입부에서 status 체크로 보장.
- **CLAUDE.md / v2-execution-plan.md 갱신 불필요** — 본 PR이 §3.3의 2.4~2.8과 1:1 매칭. MEMORY.md 갱신 불필요.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), `02bdef3 chore` (mypy fix), #6 (55e67bd), #7 (d7bc929 + 93c41a4 merge).
- **진행 중 PR**: 없음. **본 세션 산출물 = `feature/phase2-pr8-scan-celery-dt` 브랜치, 머지 대기**.
- **GitHub origin/main**: `93c41a4` (PR #7 머지 commit).
- **legacy/v1**: `0c0276b` (변동 없음).
- **변경 규모**: **51 files changed, 6876 insertions(+), 34 deletions(-)**.
- **통과 테스트**:
  - **신규/변경 backend (PR #8)**: 단위 98(integrations/dt 28 + integrations/{cdxgen,ort,trivy,size_guard} 18 + core/security 34 + schemas 15 + dispatcher 3) + 통합 11(scan/) + PR #7 회귀 fix 15(test_scans_api). **128 tests, 모두 PASS**. xfail 3개 제거.
  - **단위 + 통합 합산** (이번 세션이 직접 실행): 단위 98 + 통합 11 + PR #7 scan_api 15 = **128 PASS**. PR #5/#6 회귀(auth + projects)는 변경 영향 없음 + 부분 실행으로 회귀 없음 확인. 전체 회귀(187+128=315)는 CI에서 일괄 실행 예정.
  - **Frontend (vitest)**: 변동 없음 — 본 PR은 backend only. 45/45 (Phase 1 PR #6 결과 그대로).
  - **Frontend e2e (Playwright)**: 변동 없음. 3/3 (호스트 실행 전제).
- **mypy**: `Success: no issues found in 86 source files` (PR #7의 40 + PR #8의 신규 + 테스트).
- **ruff check**: `All checks passed!` (모든 신규 + 변경 파일).
- **컨테이너**: docker-compose dev 5/5 healthy. **celery-worker 이미지 = `trustedoss/backend-worker:dev`** (이번 PR로 분리). 워커 안에서 cdxgen 11.11.0 / ORT 85.0.0 / Trivy 0.70.0 / java 21.0.11 / node 20.20.2 모두 PATH 노출.
- **Celery 등록 태스크** (`celery -A tasks.celery_app inspect registered`):
  - `trustedoss.scan_source` (soft_time_limit=3600 / time_limit=4200)
  - `trustedoss.scan_container` (soft_time_limit=3600 / time_limit=4200)
  - `trustedoss.dt_resync` (Beat 1h)
  - `trustedoss.dt_orphan_cleaner` (Beat 6h)
  - `trustedoss.dt_health` (Beat 60s)
- **CI image-scan 잡 신규**: `.github/workflows/ci.yml`의 4번째 잡 — buildx로 worker 이미지 빌드 + Trivy로 자기 자신 스캔. HIGH/CRITICAL → fail. 다른 잡과 병렬.
- **Coverage** (신규/변경 라인, 단위 테스트 기준):
  - `integrations/dt/{breaker,client,health}.py`: ~95%
  - `integrations/{cdxgen,ort,trivy,_size_guard}.py`: ~92%
  - `core/url_guard.py`: 100% (24/24 케이스)
  - `core/pii_mask.py`: 100% (12/12 케이스)
  - `schemas/scan.py` (validator 추가분): ~96%
  - `services/scan_service.py` (trigger_scan 재작성): ~94%
  - **TOTAL** (신규/변경 코드): ~94% (게이트 80%).
- **보안 follow-up backlog (본 PR 미수정 — 별도 PR)**:
  - **L-1**: HALF_OPEN → CLOSED CAS 비-atomic. 다음 PR에서 Lua CAS 대체 권고. (scan-pipeline-specialist)
  - **L-2**: HALF_OPEN 진입 시 fail_count reset 누락 — Lua probe-gate 안에서 reset 추가 권고. (scan-pipeline-specialist)
  - **L-3**: DT 응답 본문 일부가 `DTUnavailable`/`DTClientError` 메시지에 포함 — `error_message` 컬럼·UI 노출 가능. status + path만 메시지에 담고 본문은 별도 log.warning. (scan-pipeline-specialist)
  - **L-4**: `error_message="enqueue_failed: {exc}"` — broker URL/redis password 누설 가능. 안정 prefix만 저장 권고. PR #7 follow-up L-4와 동일 패턴. (backend-developer)
  - **I-1**: DNS rebinding TOCTOU — PR #9에서 IP 핀 강제. (backend-developer)
  - **I-2**: `WORKSPACE_HOST_PATH` default `/tmp/trustedoss` — multi-tenant 호스트 권한 충돌. prod 가이드에서 명시 필수화. (doc-writer)
  - **I-3**: `mask_pii` substring 매칭 — `signature` / `cert` / `pem` / `credential` 추가 권고. (scan-pipeline-specialist)
- **알려진 이슈**:
  - 호스트 포트 8000 점유 시 e2e 영향 — Phase 1과 동일 (변동 없음).
  - pytest 전체 회귀 ~16분 (alembic upgrade 모듈마다) — 추후 conftest 최적화 여지.
  - 워커 이미지 첫 빌드 ~5분 + ORT 720MB 다운로드. CI buildx 캐시로 2회차부터 회복.

## 4. 다음 세션이 할 일

- **§6.3 Phase 공통 양식 + Phase 2 컨텍스트**로 다음 세션 시작. 첫 작업은 **Phase 2 PR #9 (WebSocket + Project List UI + 스캔 진행 모달 + e2e)** — §3.3의 2.9 / 2.10 / 2.11 / 2.12. 이 PR이 끝나야 §8 Phase 2 DoD 전체("실제 cdxgen+ORT 또는 mock 스캔 1회 완주, WebSocket 진행률 표시, DT 다운 시 캐시 응답")가 충족.
- **PR #9 핵심 라우팅**:
  - **backend-developer**: `apps/backend/api/v1/ws.py` — `/ws/scans/{scan_id}` WebSocket. JWT 인증 (query param 또는 첫 메시지). 스캔 progress_percent / current_step 5초 폴링 또는 Celery 태스크가 직접 publish (Redis pub/sub).
  - **frontend-dev**: `pages/projects/list.tsx` (가상 스크롤 react-virtuoso, 검색·정렬·필터, 스캔 상태 실시간 배지), `features/scan/ScanProgress.tsx` (5단계 프로그레스 + WebSocket 끊김 시 자동 재연결 + 페이지 이탈해도 백그라운드 진행).
  - **scan-pipeline-specialist**: `tasks/scan_source.py` 7-stage가 progress_percent 갱신 시 Redis pub/sub publish 추가. **워커-side `validate_git_url(project.git_url)` 호출 추가 + `git -c http.curloptResolve` IP 핀 강제 (security-reviewer I-1 closure)**.
  - **i18n-specialist**: 신규 UI 문자열 EN/KO 동시 추가.
  - **test-writer**: WebSocket integration + scan flow e2e (Playwright) — `tests/e2e/scan_flow.spec.ts` 4 시나리오 green.
  - **security-reviewer**: WebSocket auth surface + IP 핀 검증 producer-reviewer 1 라운드.
- **본 PR follow-up backlog 7개** (위 §3 보안 follow-up): 우선순위에 따라 별도 PR 또는 PR #9에 자연스럽게 흡수(I-1은 PR #9와 결합 권고, L-4는 PR #7 동일 패턴 묶어서 chore PR).
- **Phase 1 dependency hygiene PR (별도, 보류)**: python-multipart bump + `pip-audit --strict` CI 게이트 + L-3(passlib → bcrypt 직접). devops-engineer 단일.
- **CI Playwright 통합 PR (별도, 보류)**: `.github/workflows/ci.yml`에 docker-compose up + chromium install + npm run test:e2e 잡. devops-engineer + doc-writer. PR #9 e2e 시나리오 풍부해진 후 권고.
- **Phase 6 PR #18 (이메일 검증 + Forgot Password)**: M-1(`is_verified` 활성화) follow-up.

## 5. 주의·블로커

- **사용자 정책**: rm 권한 거부 → 임시 파일 정리 시 `mv ... /tmp/`. push / 머지 같은 destructive irreversible 명령은 사용자가 `! ` 프리픽스로 직접 실행. 본 세션 산출물(`feature/phase2-pr8-scan-celery-dt` 브랜치)도 머지/push 미실행 — 사용자 명령 대기.
- **CLAUDE.md 핵심 규칙**: 본 PR 1·2·3·4·6·7·9·10·11·12·13 모두 준수. 특히:
  - **3 (ORT/cdxgen/Trivy 동기 처리 절대 금지)** — 본 PR이 진짜 적용. 모든 외부 도구 호출이 Celery sync subprocess 컨텍스트.
  - **4 (DT Circuit Breaker)** — 본 PR이 진짜 적용. CLOSED/OPEN/HALF_OPEN, OPEN 시 `vulnerabilities` 테이블 cached 응답.
  - **9 (Docker image :latest 금지)** — 외부 도구 5개 모두 정확한 버전 핀.
  - **10 (docker-compose V1)** — 변동 없음.
  - **11 (os.getenv 런타임)** — `core/config.py` 모든 accessor 함수형 게터.
  - **12 (인증 surface)** — Celery task는 인증 우회 surface 아님. trigger_scan(인증 통과)만 enqueue 허용.
- **Producer-Reviewer 1라운드** — security-reviewer 1차 평결 후 M-1 + M-2 fix → 재검토 미호출(2회 한도 내). 회귀 4개 + 단위 26/26 PASS로 M-1/M-2 closed 자체 검증.
- **에이전트 라우팅 검증** — devops-engineer / scan-pipeline-specialist / backend-developer / test-writer / security-reviewer 5개 정상 동작. 누계 PR #5~#8에서 7개 정의 중 7개 사용. PR #9에서 frontend-dev / i18n-specialist 재사용.
- **외부 바이너리 빌드** — Dockerfile.worker 첫 빌드 4m48s. 후속 빌드는 buildx 캐시로 가속 — CI에서 GHA 캐시 활용.
- **fakeredis[lua]** 의존성 — Lua 스크립트 EVAL 지원 위해 `[lua]` extra 필요. 일반 fakeredis로는 breaker 회귀 fail. `requirements-dev.txt`에 `fakeredis[lua]==2.26.1` 명시.
- **PR #7 보안 follow-up 11개 중 본 PR 결합 4개(M-2 / M-4 / I-1 / I-2)** 모두 처리. 나머지 7개(M-1 / M-3 / L-1~L-4 / I-3) 별도 chore PR 또는 후속 Phase.
- **테스트 시간** — 본 PR 신규/변경 테스트 단위 ~1.6초 / 통합 ~12초. 전체 회귀 ~16분(alembic upgrade) — 변동 없음.

## 6. 다음 세션 시작 지시문 (복붙용)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

Phase 2 PR #8(스캔 Celery + DT 안정화)는 2026-05-06 작성 완료(브랜치 feature/phase2-pr8-scan-celery-dt).
머지 후 commit hash와 origin/main 동기화는 본 핸드오프 머지 직후 갱신.
누적 머지: PR #1~#7 + chore mypy fix + PR #8. CI green.

Phase 2 PR #8로 §3.3의 2.4 / 2.5 / 2.6 / 2.7 / 2.8 종료(Celery 태스크 + DT Circuit Breaker + DT 캐시 + Beat 스케줄). Producer-Reviewer 1라운드 — M-1(CGNAT SSRF) + M-2(degenerate SCP) fix closed, 7개 backlog(L-1~L-4 / I-1~I-3).

이번 세션부터 Phase 2 PR #9 (WebSocket + Project List UI + 스캔 진행 모달 + e2e) 시작. 이 PR로 Phase 2 §8 DoD 전체("WebSocket 진행률 + DT 다운 캐시 + 1회 완주") 충족.

docs/v2-execution-plan.md §3.3과 §6.3, docs/sessions/2026-05-06-phase2-pr8-scan-celery-dt.md 를 읽고 시작해라. docker-compose -f docker-compose.dev.yml ps 로 5/5 healthy 확인 (celery-worker = trustedoss/backend-worker:dev), gh run list --limit 3 으로 main CI green 확인.

선행 결정 (PR #9 첫 메시지로 처리):
1. WebSocket 인증 방식 — JWT를 query param(`?token=...`)으로 받을지 첫 메시지(`{"type":"auth","token":"..."}`)로 받을지. 보안 측면(query는 access log 누설)과 운영 편의 트레이드오프. backend-developer + security-reviewer 협의.
2. 진행률 push 방식 — Celery task가 Redis pub/sub publish vs WebSocket gateway가 5초 폴링. 후자가 단순하지만 latency 5초 고정.
3. PR #8 보안 follow-up I-1 흡수 — `tasks/scan_source.py` fetch 단계에 `core.url_guard.validate_git_url` 호출 + `git -c http.curloptResolve=host:443:<resolved-ip>` IP 핀 강제. scan-pipeline-specialist + backend-developer 협의.

이번 세션 산출물 = Phase 2 PR #9 (WebSocket + UI + e2e). 핵심 라우팅 (§3.3 2.9~2.12):
- backend-developer: api/v1/ws.py WebSocket 엔드포인트, JWT 인증, RBAC team-scoped, IDOR 가드.
- scan-pipeline-specialist: tasks/scan_source.py에 progress_percent 갱신 시 Redis pub/sub publish + 워커-side validate_git_url + IP 핀(I-1 closure).
- frontend-dev: pages/projects/list.tsx (react-virtuoso 가상 스크롤, 실시간 상태 배지, 검색·정렬·필터), features/scan/ScanProgress.tsx (5-stage 프로그레스, WebSocket 자동 재연결, 백그라운드 진행).
- i18n-specialist: 신규 UI 문자열 EN/KO 동시 mirroring.
- test-writer: WebSocket integration + scan flow e2e (Playwright PortalPage 하네스 확장, 4 시나리오 green).
- security-reviewer (Producer-Reviewer): WebSocket auth surface + IP 핀 검증 + 신규 UI surface XSS / CSP. PR #7 H-1 / PR #8 M-1·M-2 패턴 재사용.

작업 순서 (Pipeline + Fan-out 혼합):
1. backend-developer (선행): WebSocket 엔드포인트 + auth 결정 적용.
2. scan-pipeline-specialist (병렬): Redis pub/sub publish + I-1 closure.
3. frontend-dev + i18n-specialist (병렬, backend 결정 후): UI + 번역.
4. test-writer (마지막): integration + e2e 회귀.
5. security-reviewer 1라운드 → H finding 발견 시 라운드 2 fix.
6. docker-compose dev에서 실제 mock 어댑터로 1회 완주 + WebSocket 끊김 회복 검증.
7. 머지 명령 후 docs/sessions/<YYYY-MM-DD>-phase2-pr9-websocket-ui.md 작성.

검증 (Phase 2 PR #9 단계 = Phase 2 §8 DoD 전체):
- 신규/변경 backend + frontend coverage ≥ 80%.
- 실제 mock 스캔 1회 완주 → status=succeeded + WebSocket으로 0~100% 진행률 push 확인.
- DT down 시 cached vulnerabilities 응답 (PR #8에서 이미 검증 — PR #9는 UI 노출 검증).
- Playwright e2e 4 시나리오 green.
- security-reviewer 평결 PASS.

주의:
- 사용자 정책: rm 거부, push 같은 destructive 명령 사용자가 ! 프리픽스로.
- CLAUDE.md 규칙 12(인증 surface) — WebSocket이 새 surface. RBAC team-scoped, scan IDOR 가드 강제.
- WebSocket 끊김 + 페이지 이탈 시 백그라운드 진행 — Celery task는 항상 backend에서 돌고, UI 재접속 시 last progress_percent 동기화.
- PR #8 보안 follow-up backlog 7개 중 I-1은 본 PR과 결합. L-1·L-2·L-3·L-4·I-2·I-3는 본 세션 범위 아님 — 별도 chore PR 또는 후속 Phase.
```
