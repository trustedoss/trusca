# Session Handoff — 2026-05-06 — Phase 1 — PR #6 Auth UI + i18n + E2E Harness

## 1. 무엇을 했나

- **Phase 1 PR #6 머지** — `55e67bd feat: phase 1 pr #6 — auth ui + i18n + e2e harness` (37 files, +3498 / -58). frontend 인증 surface 전체.
  - **1.6 (frontend-dev)**: shadcn primitives 4종(`form/input/label/alert`) + 페이지 3종(`pages/auth/{Login,Register,ForgotPassword}.tsx` + `AuthLayout.tsx`). `react-router-dom@6.30.3` 도입 + `src/router.tsx` + `RequireAuth.tsx` 가드. shadcn Form + zod + react-hook-form 패턴. RFC 7807 detail을 React 텍스트 노드로 렌더(XSS-safe). `lib/authApi.ts` (fetch + ProblemError) — 1.7에서 axios로 교체.
  - **1.7 (frontend-dev)**: `stores/authStore.ts` 정식화 — status machine(idle → bootstrapping → authenticated/anonymous) + `bootstrap()`(/auth/me) + `logout()`. `lib/api.ts` (axios + 인터셉터) — bearer attach + **singleflight refresh rotation** + `_retry` 가드 + refresh 실패 시 `auth:expired` CustomEvent 1회 dispatch. `components/AuthExpiredListener.tsx` (라우터 트리 안에서 listen → `navigate("/login")`) — store는 router-free. dev-only `window.__setAccessToken` / `window.__authStore` 훅(`import.meta.env.DEV` 가드, Vite tree-shaking으로 prod 번들에서 제외). password 클라이언트 floor 8 → 12 정합(NIST 800-63B).
  - **1.8 (i18n-specialist)**: `locales/{en,ko}/auth.json` 40 키 미러(login/register/forgot/errors). `lib/i18n.ts`에 `auth` 네임스페이스 등록. `docs/glossary.md` 신규 — 13 canonical 도메인 용어(Component / Vulnerability / License / Scan / Severity 5단계 / SBOM / CVE / Allowed-Conditional-Forbidden License / Component Approval / Audit Log / Build Gate). KO 표기 v2 risk 토큰(치명/높음/중간/낮음/정보)과 정합.
  - **1.9 (test-writer)**: `tests/_harness/auth.ts` (`AuthHarness` 클래스, 13 verb, 모두 `data-testid` 기반 → locale-agnostic). `tests/e2e/auth.spec.ts` 시나리오 3개. `playwright.config.ts` 신규(testDir=tests/e2e, baseURL=http://localhost:5173, workers:1 — rate-limit 충돌 회피, retries CI 1회).
- **Producer-Reviewer 패턴 — 1회 fix loop**: security-reviewer 1차 평결 **PASS** (Critical/High/Medium 0). Low 2 + Info 4. Low 2건만 본 PR에 fix:
  - **L-1**: Register 후 auto-login이 rate-limit 429에 막히면 사용자가 가입 사실을 모르고 폼에 갇힘. fix — register/auto-login try/catch 분리, auto-login 실패 시 `navigate("/login?registered=1")` + Login에서 `useSearchParams`로 success alert(non-destructive, emerald + CheckCircle2). 신규 i18n 키 `login.registered_success` EN/KO 미러.
  - **L-2**: 동시 다발 401 N건 → catch에서 N번 dispatch. fix — `refreshOnce()` 자체 catch에서 reset + dispatch + throw 1회만, 응답 인터셉터 catch는 propagate만. 신규 단위 테스트 "concurrent 401s with refresh failure → exactly ONE auth:expired event".
- **선행 chore commit**: `02bdef3 chore(backend): fix mypy errors in models/auth.py` — PR #5 머지 후 GitHub Actions가 mypy 4건(`_role_enum` return type, `Mapped[dict]` generic param 2건)으로 fail 상태였음. PR #6 시작 전 main CI green 회복용 별도 commit.

## 2. 결정 사항 / 변경된 가정

- **fetch → axios 일원화** — 1.6은 fetch + 직접 ProblemError를 작성했고 1.7에서 axios 인스턴스로 교체. `lib/authApi.ts`는 axios re-export shim으로 남겨 호출처 호환 유지(`postRegister`/`postLogin`/`fetchMe`/`postLogout`). 단위 테스트는 `vi.mock("@/lib/api")` 패턴으로 변경.
- **Router-free store** — authStore에 `react-router-dom` import를 두지 않는 결정. logout 후 라우팅은 호출자(컴포넌트)가 navigate, refresh 실패 시는 CustomEvent("auth:expired") dispatch + `AuthExpiredListener` 가 라우터 트리 내부에서 listen. 이유: store 단독 단위 테스트에서 라우터 mock을 강요하지 않기 위함 + Phase 2 이후 store가 다른 라우팅 솔루션(예: 모달 라우팅)에 묶이지 않도록.
- **dev-only 윈도우 훅** — Playwright e2e #3(만료 토큰 자동 refresh)이 store에 만료 토큰을 주입하기 위해 `window.__setAccessToken` 필요. `import.meta.env.DEV` 가드로 prod 번들 제외. `__authStore`는 `Object.defineProperty` getter로 read-only.
- **password 12자 정합** — i18n-specialist는 1.8 시점에 8자 메시지로 mirror(기존 zod min(8) 회귀 방지)했고 1.7에서 클라이언트 zod min(12) + i18n 메시지 + 단위 테스트 단언 모두 12자로 정합화. backend는 12자 floor — bypass 시 백엔드가 거부(이중 방어).
- **e2e는 호스트 실행** — `docker-compose -f docker-compose.dev.yml exec frontend npx playwright test`는 컨테이너 내부 baseURL 결정 문제로 backend 통신 안 됨(`localhost:8000`이 자기 자신 가리킴). 호스트에서 `cd apps/frontend && npx playwright test`로 실행해야 함. CI 통합(별도 docker-compose up + chromium install)은 후속.
- **AuthHarness는 PortalPage의 sibling 클래스** — 기존 `tests/_harness/PortalPage.ts`의 throw하는 login/logout placeholder는 그대로 두고(다른 페이지용) AuthHarness를 별도 클래스로 작성. Phase 2 이후 도메인별 하네스 추가 시 같은 패턴 사용.
- **Forgot Password는 stub** — 백엔드 reset 엔드포인트는 PR #5 범위 외. 본 PR 폼은 submit 시 i18n success 메시지만 노출하고 네트워크 호출 0(테스트로 잠금). 정식 구현은 Phase 6 PR #18.
- **CLAUDE.md / v2-execution-plan.md 갱신 불필요** — 본 PR이 §3.2의 1.6~1.9와 1:1 매칭. 표준 §3·§4·§5·§6 모두 준수. MEMORY.md 갱신도 불필요.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), `02bdef3 chore` (mypy fix), **#6 (55e67bd)**.
- **진행 중 PR**: 없음.
- **GitHub origin/main**: `55e67bd`. CI run 25385759947 = success.
- **legacy/v1**: `0c0276b` (v1 freeze, 변동 없음).
- **통과 테스트**:
  - **Backend** (PR #5 + chore): 단위 35 + 통합 19 + alembic 2 = 56. 회귀 0. mypy 0 errors.
  - **Frontend 단위 (vitest)**: 45/45 pass. Coverage — auth pages 97-100%, authStore 95.91%, api.ts 85.29%, problem.ts 100%, ui primitives 86-100%. 게이트(80% lines / 70% branches) 통과.
  - **Frontend e2e (Playwright)**: 3/3 green. 6.2s, single worker. 시나리오 — register→auto-login→home / login fail inline alert / expired access → auto-refresh → retry.
- **CI 상태**: GitHub Actions(`lint`/`typecheck`/`test`) 두 잡(backend + frontend) 모두 green at `55e67bd`.
- **컨테이너 상태**: docker-compose dev 5/5 healthy. backend 4325835 + chore reload(소스 마운트), frontend는 axios + react-router-dom 추가됨(npm install이 컨테이너 안에서 실행).
- **보안 follow-up 등록 항목** (PR #5 합의분 + 본 PR Info 분):
  - PR #5 미해결: M-1 ~ M-5, L-1 ~ L-4, I-1, I-3 (이전 핸드오프 §3 참고). M-1(`is_verified`) Phase 6 PR #18, M-5 dependency hygiene 별도 devops PR.
  - **PR #6 신규 Info(머지됨, 별도 PR 후속)**:
    - **I-1** `i18n.init({ escapeValue: false })` defense-in-depth — `t()` 호출에 사용자 입력 들어가는 경우 lint 룰. Phase 6 i18n-specialist 후속.
    - **I-2** `__setAccessToken`을 `Object.defineProperty(writable:false)` + post-build grep CI 검증으로 강화. devops-engineer 후속.
    - **I-3** `VITE_API_BASE_URL` 미설정/비-https 시 prod build fail-fast. devops-engineer + frontend-dev 후속.
    - **I-4** `<AlertDescription>{apiError}</AlertDescription>` 텍스트 노드 invariant 주석 또는 `<ErrorAlert>` 캡슐화.
- **알려진 이슈**:
  - 호스트 포트 8000을 다른 로컬 프로젝트(BodyForge)가 점유한 사례 있음. e2e 실행 전 `curl -s http://localhost:8000/openapi.json | head -c 80` 으로 backend 응답 확인. JSON 아니면 backend 컨테이너 재기동.
  - Playwright 첫 실행 시 호스트에 `npx playwright install chromium` 필요(README에 추가 권장 — 별도 chore).
  - shadcn primitives(`button.tsx`/`form.tsx`)에 `react-refresh/only-export-components` 경고 2건. shadcn 본래 패턴이라 무시 — eslint config에서 `disable-next-line` 또는 룰 예외는 별도 chore.

## 4. 다음 세션이 할 일

- **§6.4 Phase 2 양식**으로 새 세션 시작. 첫 작업은 Phase 2 **PR #7 (스캔 파이프라인 코어)**. 본 세션 범위 아님.
- **Phase 1 dependency hygiene sweep PR** (별도 — devops-engineer 단일):
  - python-multipart >= 0.0.26, fastapi → starlette ≥ 0.49.1 동반 bump.
  - `pip-audit --strict` 를 `.github/workflows/ci.yml` 에 추가.
  - L-3 (passlib → bcrypt 직접) 함께 처리 권장.
- **CI에 Playwright 통합** (별도 chore — devops-engineer):
  - `.github/workflows/ci.yml` 에 `docker-compose -f docker-compose.dev.yml up -d` + `npx playwright install --with-deps chromium` + `npm run test:e2e` 잡 추가.
  - frontend 빌드 산출물에 `__setAccessToken` / `__authStore` 부재 grep 검증 step.
- **README**에 호스트 e2e 실행 절차 1단락 추가 (별도 chore — doc-writer, 작은 PR).
- **Phase 6 PR #18 (이메일 검증 + Forgot Password 정식)** — `is_verified` 컬럼 활성화, Forgot Password 백엔드 엔드포인트, 프론트 stub 교체. M-1 follow-up과 동시.

## 5. 주의·블로커

- **사용자 정책**: rm 권한 거부 → 파일 삭제 시 `mv ... /tmp/`. push 같은 destructive irreversible 명령은 사용자가 `! ` 프리픽스로 직접 실행(이번 세션에서 두 번 적용: `02bdef3` chore push + `55e67bd` PR #6 push).
- **CLAUDE.md 핵심 규칙 1·2·6·7·9·10·11·12·13** — 본 PR 모두 준수. 특히 11(import.meta.env 캐싱 금지, 함수 호출 시점 평가) frontend에 동일 정신 적용. 12(인증 surface 일관성)는 모든 axios 호출이 단일 인스턴스 + 인터셉터를 통과하도록 강제.
- **Producer-Reviewer 1회 loop** — 본 PR은 1차 PASS 후 Low 2건만 fix. 회귀 검증 only이므로 2회 검토 호출 안 함. 다음 보안 surface(API Key — Phase 5, OAuth — Phase 8, 빌드 게이트 — Phase 5)에서도 같은 패턴 재사용.
- **에이전트 라우팅 검증** — 본 세션에서 frontend-dev / i18n-specialist / test-writer / security-reviewer 4개 에이전트 정상 동작 확인. 9개 정의 중 7개 사용(누계 PR #5 + PR #6). 미사용 2개: scan-pipeline-specialist(Phase 2), doc-writer(Phase 7), devops-engineer(Phase 0/7~8 + 본 후속 chore).
- **e2e 컨테이너 vs 호스트** — 호스트 실행 전제. CI 통합 시 docker-compose up + chromium install이 워크플로우에 들어가야 함. CI 통합 전까지는 PR 머지 게이트가 unit + lint + typecheck + backend pytest로만 구성됨(e2e는 로컬 검증).
- **L-1 시나리오 부작용** — register→auto-login 분리로 사용자 경험은 정상화됐지만, register 직후 backend가 user를 active 상태로 만들기 전 race가 있다면 auto-login 401 가능. 현재 백엔드는 동기 commit이라 race 없음. Phase 2 이후 비동기 후처리(예: welcome email Celery task)가 추가되면 재검토 필요.

## 6. 다음 세션 시작 지시문 (복붙용)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

Phase 1 PR #6(인증 UI + i18n + E2E 하네스)는 2026-05-06 머지 완료(commit 55e67bd).
GitHub origin/main 동기화 완료, CI green. 누적 머지: PR #1~#6 + chore mypy fix.

Phase 1은 PR #6로 종료(§8 Phase 1 DoD: 가입→로그인→로그아웃 EN/KO 동작 + security-reviewer PASS, 모두 충족).

이번 세션부터 Phase 2 PR #7 (스캔 파이프라인 코어) 시작.

docs/v2-execution-plan.md §3.3과 §6.4, docs/sessions/2026-05-06-phase1-pr6-auth-ui-i18n-e2e.md 를 읽고 시작해라.

선행 작업(PR #7 시작 전 권장 — 본 세션이 다룰지 별도 세션으로 분리할지 첫 메시지로 결정):
- Phase 1 dependency hygiene sweep PR (devops-engineer 단독 — python-multipart bump, pip-audit CI 게이트, passlib → bcrypt).
- README에 호스트 e2e 실행 절차 추가(npx playwright install chromium 안내).
- CI에 Playwright e2e 잡 추가(docker-compose up + chromium install).
이 3개는 Phase 2 본 작업과 분리해 작은 chore PR로 묶거나, Phase 2 PR #7과 함께 갈지 첫 메시지에서 명시.

이번 세션 산출물 = Phase 2 PR #7 (스캔 파이프라인 코어 — 정확한 작업 범위는 §3.3 참조). 핵심:
- scan-pipeline-specialist (첫 호출 검증) — Celery 태스크 골격, cdxgen/ORT/Trivy 어댑터 인터페이스, DT health monitor + circuit breaker 골격.
- db-designer — 스캔 도메인 스키마(Project / Scan / Component / Vulnerability / License / SBOM 산출물 포인터).
- backend-developer — 스캔 라이프사이클 API + WebSocket 진행률 채널.
- frontend-dev — Projects 목록 + 스캔 트리거 + 진행률 표시.
- test-writer — 스캔 흐름 단위 + 통합 + e2e 시나리오.
- security-reviewer — DT 연동 + 빌드 게이트 surface.

작업 순서는 §3.3에 따른다. Fan-out과 Pipeline 패턴이 혼재 — DB 스키마는 선행, 그 위에 어댑터/태스크/API가 병렬, UI/E2E가 후행.

검증:
- Phase 2 DoD(§8): 실제 cdxgen+ORT(또는 mock) 스캔 1회 완주, WebSocket 진행률 표시, DT 다운 시 캐시 응답.
- security-reviewer PASS — DT 연동 / 캐시 폴백 / 게이트 결정 로직.

주의:
- 사용자 정책: rm 거부, push 같은 destructive 명령은 사용자가 ! 프리픽스로 직접.
- CLAUDE.md 규칙 3(ORT/cdxgen/Trivy는 Celery 비동기 — 동기 처리 절대 금지) + 4(DT Circuit Breaker, OPEN 상태면 PostgreSQL 캐시 반환).
- 스캔 5~60분 장시간 — 진행률 UX(스켈레톤 + WebSocket) 핵심 규칙 7(기능+완성도 동시).
- Phase 1 follow-up 4개(M-1, M-3, I-1~I-4) 본 세션 범위 아님(우선순위에 따라 별도 PR).
```

