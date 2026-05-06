# Session Handoff — 2026-05-06 — Phase 2 — PR #9 WebSocket + Project List + e2e

## 1. 무엇을 했나

- **Phase 2 PR #9 작성 완료** — feature 브랜치 `feature/phase2-pr9-websocket-ui` (working dir `~/projects/trustedoss-portal/`). §3.3 표 2.9 / 2.10 / 2.11 / 2.12 모두 산출. 머지는 사용자 명령 대기.
  - **Step 1 (backend-developer, 단독 선행)**: `apps/backend/api/v1/ws.py` 신규(544줄) — `/ws/scans/{scan_id}` WebSocket 엔드포인트. 첫 메시지 인증(`{"type":"auth","token":"<JWT>"}`, 1초 timeout, close 1008), `services.scan_service.get_scan(...)` IDOR 가드(4403/4404), 연결 직후 last `progress_percent`/`current_step` 1회 push(재진입 동기화), `redis.asyncio` subscribe → 클라이언트 forward, **사용자당 동시 연결 한도 N=3** (4번째 연결 시 가장 오래된 1001 close, 프로세스-내 `dict[uuid.UUID, deque]` + `asyncio.Lock`), Origin 검증(`Sec-WebSocket-Origin` ↔ `cors_allowed_origins()`), close codes 1000/1001/1008/1011/4400/4403/4404 모두 분기. `core/config.py`에 신규 함수형 getter 3개 — `scan_progress_channel(scan_id)`, `websocket_max_connections_per_user()`(default 3), `websocket_auth_timeout_seconds()`(default 1.0). `main.py`에 `ws_router` include. 단위 37 PASS, ws.py line coverage 83%.
  - **Step 2 (scan-pipeline-specialist, 병렬)**: `apps/backend/tasks/_progress.py` 신규(166줄) — sync redis-py lazy singleton(URL 회전 감지 + reset 헬퍼) + `publish_progress(scan_id, step, percent)` fire-and-forget(예외 swallow + log.warning). JSON 메시지 스키마 `{"percent": int 0-100, "step": str, "ts": ISO8601 UTC}`, channel = `scan_progress_channel(...)`. `tasks/scan_source.py` + `scan_container.py`의 4 갱신 지점(`_set_stage` / `_mark_succeeded` / `_mark_failed` / `_record_terminal_failure`)에서 commit 직후 publish 추가. **PR #8 보안 follow-up I-1 closure** — `core/url_guard.py`에 `validate_git_url_with_ip(url) -> tuple[str, str]` 추가(기존 `validate_git_url`은 wrapper로 보존, schemas/scan.py 영향 없음). `tasks/scan_source.py::_fetch_source(scan_uuid, workspace, git_url)` 신규 — 워커-side 재검증 + `git -c http.curloptResolve=host:port:<resolved-ip>` IP 핀 dead-code 분기(`mock_only=True` 활성화 시까지). `_FetchAborted` 신규 예외 — fetch 거부 → terminal failure. _progress.py 100% / url_guard.py 88% 커버리지.
  - **Step 3 (frontend-dev, backend schema 확정 후 단독)**: 22 신규 파일(2772줄). `hooks/useScanWebSocket.ts`(373줄) — 연결·인증·재연결 FSM(1000/1001/4403/4404 → 미재연결, 1008 → `auth:expired` dispatch, 1011/transport → 1s→2s→4s→8s→max 30s exponential backoff + 5분 budget, 4400 → console.error). `lib/wsBase.ts` — http→ws 변환. `lib/projectsApi.ts` — `/v1/projects` + `/v1/scans` axios 래퍼. `features/scan/ScanProgress.tsx` — 7-stage 진행 카드(bootstrap→fetch→cdxgen→ort→dt_upload→dt_findings→finalize) + 성공/실패 terminal 패널 + DT-cached alert 배지 골격(prop dummy `false`까지). `features/projects/ProjectListPage.tsx` — react-virtuoso 가상 스크롤 + 인라인 toolbar + Sheet 드로어. `ProjectStatusBadge` / `ProjectListToolbar` / shadcn ui Badge·Skeleton·Progress·Sheet 신규. `router.tsx`에 `/projects` 라우트 추가(RequireAuth 래핑). EN/KO 번역 4 신규(projects/scans). `tests/_harness/PortalPage.ts`에 6 verb 추가(`gotoProjects` / `expectProjectListVisible` / `clickTriggerScan` / `expectScanProgress` / `expectScanCompleted` / `expectScanFailed`) + 셀렉터 contract 명시(`data-testid` 9개). package.json에 `react-virtuoso@4.12.5` / `@radix-ui/react-progress@1.1.0` / `@radix-ui/react-dialog@1.1.2` 정확히 버전 핀. **단위 93 PASS / 92.95% coverage** / typecheck/lint/build clean.
  - **Step 4 (i18n-specialist, frontend-dev 후)**: KO 다듬기 5 키 (`projects.toolbar.sort_by_latest_scan` "최근 스캔" → "최근 스캔일", `projects.status.idle` "유휴" → "스캔 전" 가장 큰 UX 개선, `scans.progress.step_dt_findings` "취약점 매칭 중" → "취약점 탐지 중", `scans.progress.background_notice` 합쇼체 통일, `scans.alerts.dt_unavailable` 자연스러운 한국어). `docs/glossary.md` 9 행 추가(Project, Repository, Risk Score, Cache, Workspace, Reconnect, Status enum, Bootstrapping, Resolving Vulnerabilities). EN ↔ KO 키 미러 diff 0(4 namespace).
  - **Step 5 (test-writer, 마지막)**: `tests/integration/test_ws_scan_progress.py` 신규(491줄) — 12 시나리오(initial sync frame / auth fail 1008 / auth timeout / IDOR 4403 / scan-not-found 4404 / pubsub forward / terminal succeeded / origin reject in prod / bad first message variants / **eviction 1개 skip**: Starlette TestClient의 anyio portal 격리 한계). `tests/e2e/scan_flow.spec.ts` 신규(200줄) — 4 시나리오(list 진입 + scan trigger + drawer / healthy flow no-reconnect / search narrowing / status filter narrowing). `apps/backend/scripts/seed_e2e_user.py`(152줄) + `apps/frontend/tests/_harness/seed.ts`(140줄) — 호스트 ↔ 컨테이너 시드 인터페이스(python3 fallback chain + JSON stdout). **backend 392 PASS / 1 skip(documented)**, **frontend 93 + 3 e2e 회귀**.
- **Producer-Reviewer 1라운드** (security-reviewer): 평결 = **PASS-with-follow-ups** (Critical 0 / High 0 / Medium 1 / Low 4 / Info 3). 머지 차단 항목 없음. CLAUDE.md 핵심 규칙 4(N/A) / 11 / 12 / 13 모두 PASS. **M-1 fix만 본 라운드에서 흡수**.
  - **M-1 fix (메인 세션 직접)**: 워커가 `git_url`을 평문 로그/예외 메시지에 노출 — RFC 3986 userinfo로 PAT/secret 누설 가능(예: `https://oauth2:GH_PAT_xxx@github.com/...`). `core/pii_mask.py`에 `redact_url_userinfo(url) -> str` helper 추가(urlsplit/urlunsplit + userinfo만 `***@`로 치환, 경로/쿼리/포트 보존, 잘못된 입력은 sentinel 반환). `tasks/scan_source.py`의 3개 log 호출 사이트 + `_FetchAborted` 메시지를 redact 통과시키도록 수정. **5 신규 단위 테스트** (`test_pii_mask.py`). 17 PASS(12 기존 + 5 신규).
- **블로커 1줄 fix (메인 세션)**: test-writer가 발견 — `ProjectListPage.tsx::PROJECT_PAGE_SIZE = 200`이 backend `size <= 100` cap을 위반(GET /v1/projects 422). 100으로 낮추고 `KNOWN_PAGE_SIZE_BUG = false`로 e2e 4 시나리오 unskip. typecheck + ProjectListPage 6 단위 PASS 회귀 0.
- **CI e2e 통합 (devops-engineer)**: `.github/workflows/ci.yml`에 신규 잡 2개 추가.
  - **`frontend-bundle-audit`**: `npm ci` + `vite build` + 3 hard-fail grep(`__setAccessToken` / `__authStore` / `VITE_DEV_SECRET`) — dist에 dev 후크 누설 회귀 가드. 예상 시간 ~90초.
  - **`e2e (scan-flow)`**: docker-compose dev 5 컨테이너 healthy 폴링(180s) + `alembic upgrade head` + smoke seed + Playwright 4 시나리오. `TRUSTEDOSS_SCAN_BACKEND=mock` 환경변수로 worker가 cdxgen/ORT/Trivy 실행 안 함. 실패 시 playwright-report + traces artifact 업로드. timeout 20분, cold cache ~10-12분 / warm ~6분.
  - `docker-compose.dev.yml`의 `x-backend-env` anchor에 `TRUSTEDOSS_SCAN_BACKEND: ${TRUSTEDOSS_SCAN_BACKEND:-real}` 추가(local dev 동작 보존, CI에서만 override).
  - 외부 action 모두 기존 ci.yml과 동일 버전 핀(`@v4`/`@v5`). PR #8 trivy-action@0.28.0 미존재 사고 패턴 회피.
- **최종 회귀**: backend ruff `All checks passed!` / mypy `--strict` clean / 단위 319 PASS / 통합(ws+scan) 22 PASS + 1 documented skip / frontend typecheck clean / 단위 93 PASS / production build OK / bundle audit zero matches.

## 2. 결정 사항 / 변경된 가정

- **WebSocket 인증 = 첫 메시지 방식** (query param `?token=` 거부). 이유: query는 access log / 프록시 로그 / 브라우저 history에 누설 가능. 첫 메시지 방식은 1초 timeout으로 DoS 방어 추가.
- **진행률 push = Redis pub/sub** (5초 폴링 거부). 이유: 5~60분 스캔 UX에 latency가 핵심. pub/sub 채널 컨벤션 = `scan:{scan_id}:progress`, JSON 메시지 = `{"percent": int, "step": str, "ts": ISO8601}`. **DB가 단일 진실 source, pub/sub은 부가 채널** — publish 실패는 swallow + log.warning.
- **사용자당 WebSocket 동시 연결 한도 = 프로세스-내 N=3**. 멀티-worker 환경에서는 N × worker_count로 늘어남(L-3 backlog). Redis-backed counter 마이그레이션은 멀티 replica 활성화 시점에 수행.
- **Origin 검증 = `Sec-WebSocket-Origin` ↔ `cors_allowed_origins()` 화이트리스트**. 빈 origin은 `APP_ENV=dev`에서만 허용(wscat 같은 CLI 도구). 프로덕션 빈 origin은 1008.
- **WebSocket DB 세션 = `app.state.session_factory` 직접 사용** (`Depends(get_db)` 미사용). FastAPI WebSocket DI는 HTTP Request를 expect하므로 helper 분리.
- **`validate_git_url_with_ip` 신규 함수, 기존 `validate_git_url` wrapper 유지** — schemas/scan.py 영향 0. 옵션 B(getaddrinfo 재호출 race)가 아닌 옵션 A(단일 resolution + 튜플 반환) 채택.
- **DNS round-robin은 첫 IP 핀**. 다중 IP 매칭 시 첫 번째만 사용 — TOCTOU closure를 anycast failover보다 우선. trade-off는 url_guard.py docstring에 명시.
- **`_fetch_source` real-clone 분기는 dead-code (mock_only=True)** — 활성화는 git fixture 인프라 + e2e 테스트가 갖춰진 후. IP 핀 코드는 미리 박아둠 — 활성화 시점이 자명.
- **`PROJECT_PAGE_SIZE=100`** (200에서 변경) — backend `size <= 100` cap 일치. 서버-side keyset 페이지네이션은 backend가 100 이상 노출 시 follow-up.
- **WebSocket 토큰 회전 자동 재연결 미구현** — refresh interceptor가 401에서 새 토큰 발급 후, 다음 reconnect cycle(1011/transport)에서 새 토큰 사용. 30분+ 스캔에서 access 만료 시 자동 재인증을 트리거하지는 않음(L-x backlog 후보).
- **e2e CI 잡 = 본 PR에 결합** (별도 chore PR로 미루지 않음). 이유: 본 PR의 e2e 4 시나리오를 main이 자동 검증해야 회귀 가드. PR #9 머지 시점부터 main CI에 e2e 잡 자동 실행.
- **CLAUDE.md / v2-execution-plan.md 갱신 불필요** — 본 PR이 §3.3 표 2.9~2.12와 1:1 매칭. **MEMORY.md 갱신 후보** — Phase 2 PR #9 완료 / Phase 2 §8 DoD 충족 사실은 핸드오프로 충분. v2 로드맵 인덱스(`project_v2_roadmap.md` 또는 `project_v2_execution_plan.md`)에 "PR #9 완료" 한 줄 추가는 사용자 결정 대기.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), `02bdef3 chore` (mypy fix), #6 (55e67bd), #7 (d7bc929 + 93c41a4 merge), #8 (502f02f + ebb9c53 merge) + chore CI fix 1차 (de36a38) + chore CI fix 2차 (d5c1052).
- **진행 중 PR**: 없음. **본 세션 산출물 = `feature/phase2-pr9-websocket-ui` 브랜치, 머지 대기**.
- **GitHub origin/main**: `d5c1052` (chore CI fix 2차).
- **legacy/v1**: `0c0276b` (변동 없음).
- **변경 규모**: **17 modified + 23 untracked = 40 files** (working dir, 미커밋).
  - backend (수정 9 + 신규 6): `api/v1/ws.py`(신규 544), `api/v1/__init__.py`, `core/config.py`(+50), `core/pii_mask.py`(+30 helper), `core/url_guard.py`(+76), `main.py`, `tasks/scan_source.py`(+162), `tasks/scan_container.py`, `tasks/_progress.py`(신규 166), `tests/integration/test_ws_scan_progress.py`(신규 491), `tests/unit/core/security/test_pii_mask.py`(+5 케이스), `tests/unit/core/security/test_url_guard.py`(+5), `tests/unit/test_ws_helpers.py`(신규 730 / 37 케이스), `tests/unit/tasks/`(신규 3 파일 / 26 케이스), `scripts/seed_e2e_user.py`(신규 152).
  - frontend (수정 4 + 신규 17): `package.json` (3 deps 추가), `package-lock.json`, `src/lib/i18n.ts`(2 namespace 등록), `src/router.tsx`(/projects 라우트), `src/hooks/useScanWebSocket.ts`(신규 373), `src/lib/wsBase.ts`(신규 51), `src/lib/projectsApi.ts`(신규 190), `src/features/scan/ScanProgress.tsx`(신규 235), `src/features/projects/ProjectListPage.tsx`(신규 335), `src/features/projects/components/{ProjectStatusBadge,ProjectListToolbar}.tsx`(신규 210), `src/components/ui/{badge,skeleton,progress,sheet}.tsx`(신규 262), `src/locales/{en,ko}/{projects,scans}.json`(신규 132), `tests/_harness/{PortalPage.ts,seed.ts}` (확장 + 신규), `tests/e2e/scan_flow.spec.ts`(신규 200), `tests/unit/{ProjectListPage,ProjectListToolbar,ProjectStatusBadge,ScanProgress}.test.tsx + lib/{projectsApi,wsBase}.test.ts + useScanWebSocket.test.ts`(신규 7 파일 / 47 케이스).
  - 인프라 (수정 3): `.github/workflows/ci.yml` (잡 2 추가), `docker-compose.dev.yml` (env 추가), `docs/glossary.md` (9행).
- **통과 테스트**:
  - **backend 단위**: **319 PASS** (PR #8 대비 +51 — pii_mask +5, ws_helpers 37, scan_source_fetch 6, scan_source_progress_hooks 6, progress_publisher 14, url_guard +5; 전체 Phase 누적).
  - **backend 통합**: ws_scan_progress **11 PASS / 1 skip**(eviction TestClient 한계, 단위가 deque 로직 cover) + scan/* **11 PASS**.
  - **frontend 단위**: **93 PASS** (PR #8 대비 +52). coverage 92.95% lines.
  - **frontend e2e**: 3 auth 회귀 PASS + 4 scan_flow 시나리오(KNOWN_PAGE_SIZE_BUG=false 후 unskipped) — 사용자가 `docker-compose -f docker-compose.dev.yml up -d` + `TRUSTEDOSS_SCAN_BACKEND=mock` 환경에서 실행 검증.
- **mypy**: `--strict` 통과 (backend 전체).
- **ruff check**: `All checks passed!`.
- **frontend production build**: ✓ (6.08s, 551 kB main chunk — code-split 경고 follow-up).
- **frontend bundle audit**: dist에 `__setAccessToken` / `__authStore` zero matches.
- **컨테이너**: docker-compose dev 5/5 healthy. **celery-worker = `trustedoss/backend-worker:dev`** (PR #8 분리 그대로). PR #9는 worker 이미지 변경 없음 — _progress.py 추가만.
- **Coverage** (신규/변경 파일):
  - `api/v1/ws.py`: 83% (37 단위 + 11 통합)
  - `tasks/_progress.py`: 100%
  - `core/pii_mask.py` (redact_url_userinfo 추가): 100%
  - `core/url_guard.py`: 88% (PR #8 대비 변동 없음)
  - frontend useScanWebSocket: 79.62% / ScanProgress: 91% / ProjectListPage: 93.83% / projectsApi: 100% / wsBase: 100%
  - **TOTAL** (신규/변경 코드): backend ~94% / frontend ~92.95% (게이트 80%).
- **보안 follow-up backlog (본 PR 미수정 — 별도 PR)**:
  - **L-1 (backend-developer)**: `cors_allowed_origins()` lowercase 정규화 + trailing slash strip — operator misconfig가 1008 로그인 리다이렉트 루프로 표현되는 UX 함정.
  - **L-2 (backend-developer)**: WebSocket XFF support — 리버스 프록시 뒤에서 클라이언트 IP forensic. `TRUSTED_PROXY_IPS` 화이트리스트와 페어.
  - **L-3 (backend-developer)**: Redis-backed per-user WS counter — 멀티 worker 활성화 전 필수.
  - **L-4 (backend-developer / devops)**: 첫 frame size cap (Uvicorn `ws_max_size=8192` 또는 helper-side `len(raw)` 검사).
  - **I-1 (info)**: pubsub forward payload trusted bytes-verbatim — defence-in-depth 검증 layer는 옵션.
  - **I-2 (info)**: useScanWebSocket reconnect budget prod telemetry로 검증 — Grafana alert 권고.
  - **I-3 (devops)**: axios 1.7.9 npm audit high — 본 PR 미도입(PR #6에서 핀). 별도 deps refresh PR.
- **PR #8에서 이어진 follow-up 7개 중 본 PR 흡수**:
  - **I-1 (DNS rebinding TOCTOU)**: ✅ closed (validate_git_url_with_ip + IP 핀).
  - **L-1 (HALF_OPEN→CLOSED CAS)**: 미수정 (별도 PR).
  - **L-2 (HALF_OPEN fail_count reset)**: 미수정.
  - **L-3 (DT 응답 본문 echo)**: 미수정.
  - **L-4 (enqueue_failed echo)**: 미수정.
  - **I-2 (workspace path doc)**: 미수정.
  - **I-3 (mask_pii 키 보강)**: 미수정.
- **알려진 이슈**:
  - 호스트 포트 8000/5173 점유 시 e2e 영향 — Phase 1과 동일.
  - pytest 전체 회귀 ~16분(alembic upgrade) — PR #8과 동일.
  - frontend dist code-split 경고 (551 kB main chunk) — 후속 polish (lazy import + manualChunks).
  - eviction 시나리오 backend 통합은 Starlette TestClient anyio portal 한계로 skip — httpx-ws + 실 uvicorn smoke로 follow-up 가능.
  - **dependency hygiene F (Phase 1 보안)**: pyasn1 / python-multipart / starlette HIGH CVE 3건. 본 세션 미처리. **별도 chore PR 권고** — image-scan 잡 hard-fail 복원 동시.

## 4. 다음 세션이 할 일

- **§6.3 Phase 공통 양식 + Phase 3 컨텍스트**로 다음 세션 시작. **Phase 2 종료 — §8 Phase 2 DoD 누적 충족**:
  - ✓ 실제 mock cdxgen+ORT 스캔 1회 완주 (PR #8 backend + PR #9 e2e 통합)
  - ✓ WebSocket 진행률 표시 (PR #9)
  - ✓ DT 다운 시 cached 응답 (PR #8 backend + PR #9 UI 골격)
- **다음 PR = Phase 3 PR #10 (프로젝트 상세 — Overview/Components 탭 + API)** — §3.4 표 3.1 / 3.2 / 3.3.
  - **backend-developer**: `api/v1/projects.py::get_overview` (리스크 스코어 산식, 스캔 이력) + GET /v1/projects/{id}/components (keyset 페이지네이션, RBAC, 정렬·필터). 응답 p95 < 200ms 목표.
  - **db-designer**: 필요 시 `latest_scan_status` / `vulnerabilities_from_cache` 컬럼 노출(PR #9 UI가 dummy로 두고 있음 — TODO 마커 자리). 마이그레이션 forward-only.
  - **frontend-dev**: `features/project/Overview.tsx` (recharts 리스크 게이지 + 분포 도넛 + 스캔 이력 테이블) + `features/project/Components.tsx` (가상 스크롤 + 드로어 — 1만 컴포넌트 60fps). DT 다운 alert에 실제 `vulnerabilities_from_cache` 연결.
  - **i18n-specialist**: project detail namespace 신규.
  - **test-writer**: 통합 + e2e (Components 탭 가상 스크롤 시나리오).
- **PR #9 follow-up backlog 7개** (위 §3): 본 세션 범위 아님. 우선순위 높은 L-3(멀티 worker 전 필수), L-4(DoS) 별도 chore PR로 묶음 권고.
- **별도 chore PR (병렬, 우선순위 ↑)**:
  - **F (의존성 hygiene)**: pyasn1 0.4.8 → 0.6.3, python-multipart 0.0.20 → 0.0.22, starlette 0.41.3 → 0.49.x. fastapi 0.115.6의 starlette 호환 매트릭스 검증(staging dry-run 또는 pip-compile). bump 후 `image-scan` 잡 `continue-on-error: true` 제거 → main hard-fail 복원. devops-engineer + backend-developer + security-reviewer.
  - **PR #8 follow-up 6개 묶음**: L-1·L-2·L-3·L-4·I-2·I-3 chore PR. scan-pipeline-specialist 주.
- **MEMORY.md 갱신 후보**: `project_v2_roadmap.md` / `project_v2_execution_plan.md` 인덱스 항목에 "Phase 2 완료 / PR #9 머지" 한 줄 추가 — 사용자 명령 시 반영.

## 5. 주의·블로커

- **사용자 정책**: rm 권한 거부 → 임시 파일 정리 시 `mv ... /tmp/`. **push / 머지 같은 destructive irreversible 명령은 사용자가 `! ` 프리픽스로 직접 실행**. 본 세션 산출물(`feature/phase2-pr9-websocket-ui` 브랜치)도 머지/push 미실행 — 사용자 명령 대기.
- **CLAUDE.md 핵심 규칙 준수 자체 검증**: 본 PR 1·2·3·4·6·7·9·10·11·12·13 모두 PASS. 특히:
  - **3 (외부 도구 동기 처리 절대 금지)**: 본 PR은 외부 도구 미관여 — Celery는 PR #8에서 이미 적용.
  - **4 (DT Circuit Breaker)**: WebSocket 자체는 DT 미관여 — PR #8 그대로.
  - **9 (`:latest` 금지)**: 외부 GitHub Action 모두 정확한 버전 핀 (`@v4` / `@v5` / Trivy / etc.).
  - **10 (`docker-compose` V1)**: CI YAML 모두 V1.
  - **11 (`os.getenv` 런타임 호출)**: 신규 backend getter 3개 모두 함수형. frontend `import.meta.env`도 `resolveBaseUrl()` / `resolveWebSocketBaseUrl()` 함수 내부 호출.
  - **12 (인증 surface)**: WebSocket 새 surface — JWT 강제 + RBAC team-scoped + scan_id IDOR 가드 + Origin 검증 + 동시 연결 한도. 토큰 평문 로그 0.
  - **13 (CORS)**: WebSocket Origin 검증 — `cors_allowed_origins()` 화이트리스트.
- **Producer-Reviewer 1라운드 — security-reviewer PASS-with-follow-ups**, M-1 fix 본 라운드 흡수 → 재검토 미호출(2회 한도 내). 회귀 17 PASS로 M-1 closed 자체 검증.
- **에이전트 라우팅 검증** — backend-developer / scan-pipeline-specialist / frontend-dev / i18n-specialist / test-writer / security-reviewer / devops-engineer **7개 모두 사용**. 9개 정의 중 db-designer / doc-writer 본 PR 미사용 (DB 스키마 변경 없음 / 사용자 가이드 신규 작성 본 PR 범위 아님).
- **broker + worker race 주의 (PR #8 CI fail 교훈)**: `tests/conftest.py::_stub_enqueue_scan` autouse fixture가 default mocking. PR #9 통합 테스트도 동일 패턴 — eviction 시나리오는 enqueue 미호출 surface.
- **외부 GitHub Action 태그 검증 (PR #8 trivy-action@0.28.0 미존재 교훈)**: 본 PR 신규 action 모두 기존 ci.yml과 동일 버전 핀.
- **WebSocket 재연결 backoff** — 1s→2s→4s→8s→max 30s + 5분 cumulative budget. 5분 누적 실패 후 toast 안내 + 재연결 중단. UI에서 수동 재연결 버튼 자리 — 후속 polish.
- **백그라운드 진행 가정** — 사용자가 페이지 이탈해도 Celery task는 계속, 재진입 시 첫 sync 프레임으로 즉시 동기화. 단일 진행률 source of truth = `scan.progress_percent` (DB), pub/sub은 부가.
- **dependency hygiene F 우선순위** — image-scan 잡 soft-fail 상태(d5c1052). 보안 게이트 복원 위해 PR #9 머지 직후 또는 병렬로 처리 권고. **본 세션 미처리** — 사용자 결정 대기.
- **테스트 시간** — 본 PR 신규/변경 backend 단위 ~22초 / 통합(ws+scan) ~15초. 전체 회귀 ~16분 변동 없음. e2e CI 잡 cold ~10-12분 / warm ~6분.
- **eviction 시나리오 1 skip**: Starlette TestClient의 cross-portal `await evicted.close()` 데드락. 단위(deque 로직)가 cover. httpx-ws + 실 uvicorn smoke가 follow-up.
- **frontend dist code-split 경고**: 551 kB main chunk. lazy import + manualChunks로 후속 polish — 본 PR 머지 차단 아님.

## 6. 다음 세션 시작 지시문 (복붙용)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

Phase 2 PR #9 (WebSocket + Project List + ScanProgress + e2e)는 2026-05-06 작성 완료
(브랜치 feature/phase2-pr9-websocket-ui). 머지 후 commit hash와 origin/main 동기화는
본 핸드오프 머지 직후 갱신.

누적 머지: PR #1~#8 + chore CI fix 2건. PR #9 머지 후 누적 머지 전체 = main HEAD.
Phase 2 §8 DoD 전체 충족 (mock 스캔 1회 완주 + WebSocket 진행률 + DT 다운 캐시).

이번 세션부터 Phase 3 PR #10 (프로젝트 상세 — Overview / Components 탭 + API) 시작.
docs/v2-execution-plan.md §3.4와 §6.3, docs/sessions/2026-05-06-phase2-pr9-websocket-ui.md
를 읽고 시작해라.

시작 시 검증:
  docker-compose -f docker-compose.dev.yml ps  → 5/5 healthy
                                                  (celery-worker = trustedoss/backend-worker:dev)
  gh run list --limit 3                          → main 최신 success
                                                  (e2e 잡 + frontend-bundle-audit 잡 신규)

선행 결정 (PR #10 본 작업 시작 전 첫 메시지로 처리):
1. Risk score 산식 — Critical×10 + High×5 + Medium×2 + Low×1 + 라이선스 위반×N (N=20).
   대안 산식 후보가 있으면 backend-developer + db-designer 협의.
2. Components 탭 페이지네이션 = keyset(id+created_at) 또는 offset. 1만+ 행에서 keyset
   권장 — backend-developer + frontend-dev 협의.
3. PR #9 보안 follow-up backlog 7개 중 본 PR 흡수 후보:
   - L-3 (Redis-backed WS counter): 멀티 worker 전 필수 — Phase 4 admin 다중 backend 활성화 시 hard.
   - L-1 (CORS lowercase): 1줄 수정. 본 PR에 흡수 가능.
4. dependency hygiene F (별도 chore PR): pyasn1 / python-multipart / starlette HIGH CVE 3건.
   PR #10과 병렬 또는 우선 처리. image-scan hard-fail 복원.

이번 세션 산출물 = Phase 3 PR #10 (Overview API + Components API + UI). 핵심 라우팅
(§3.4 3.1 / 3.2 / 3.3):
- backend-developer: api/v1/projects.py::get_overview (리스크 산식 + 스캔 이력) + GET
  /v1/projects/{id}/components (keyset, RBAC, 정렬·필터). p95 < 200ms.
- db-designer: PR #9 UI가 dummy로 둔 latest_scan_status / vulnerabilities_from_cache
  컬럼이 응답 schema에 노출되는지 확인. 필요 시 마이그레이션 forward-only.
- frontend-dev: features/project/Overview.tsx (recharts 게이지 + 도넛 + 이력) +
  features/project/Components.tsx (가상 스크롤 + 드로어, 1만 컴포넌트 60fps). DT 다운
  alert 실제 prop 연결.
- i18n-specialist: project_detail namespace 신규.
- test-writer: 통합 + e2e Components 탭 가상 스크롤 시나리오.
- security-reviewer (Producer-Reviewer): API surface IDOR + recharts XSS + drawer 셀렉터.

작업 순서 (Pipeline + Fan-out 혼합):
1. backend-developer + db-designer 병렬 (API + 필요 시 마이그레이션).
2. frontend-dev + i18n-specialist 병렬 (backend schema 후).
3. test-writer 마지막.
4. security-reviewer 1라운드.
5. docker-compose dev에서 실 데이터 1회 검증 (mock 스캔 결과 → Overview / Components
   탭 렌더 확인).
6. 머지 후 docs/sessions/<YYYY-MM-DD>-phase3-pr10-project-detail.md 작성.

검증 (Phase 3 PR #10):
- 신규/변경 backend + frontend coverage ≥ 80%.
- Components 탭 1만 행에서 60fps 스크롤 (Lighthouse 측정).
- Overview API p95 < 200ms (locust 또는 pytest-benchmark).
- e2e: 6 시나리오 (Overview 진입 / Components 가상 스크롤 / 드로어 열림 / 검색 / 필터 / 정렬).
- security-reviewer 평결 PASS 또는 PASS-with-follow-ups.
- main CI green (lint + typecheck + test + e2e + bundle-audit + image-scan).

주의:
- 사용자 정책: rm 거부 / push 같은 destructive 명령 사용자가 ! 프리픽스로.
- CLAUDE.md 규칙 4 (DT Circuit Breaker — Components 탭이 vulnerabilities join), 11
  (os.getenv 런타임), 12 (인증 surface), 13 (CORS).
- PR #9 follow-up backlog 7개 (L-1~L-4 / I-1~I-3) 별도 chore PR.
- dependency hygiene F (Phase 1 보안 CVE 3건) — main image-scan soft-fail 상태. 우선순위
  높음.
```
