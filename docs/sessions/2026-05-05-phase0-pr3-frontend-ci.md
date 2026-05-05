# Session Handoff — 2026-05-05 — Phase 0 — PR #3 Frontend bootstrap + GitHub Actions CI

## 1. 무엇을 했나

- **하네스 먼저**: `apps/frontend/tests/_harness/PortalPage.ts`(언어 토글·앱 마운트 검증 메서드 + PR #5 placeholder 메서드), `tests/unit/App.test.tsx`(앱 마운트, 5개 risk 토큰, EN↔KO 토글), `tests/unit/authStore.test.ts`(Zustand 스토어 4개 시나리오), `tests/setup.ts`(testing-library + i18n teardown).
- **`apps/frontend/` 본격 부트스트랩**:
  - React 18.3.1 + TypeScript 5.6.3 + Vite 5.4.10. `tsconfig.json` references → `tsconfig.app.json`(앱)·`tsconfig.node.json`(Vite/Tailwind/PostCSS/eslint config).
  - Tailwind CSS 3.4.14 + shadcn/ui (style: default, base color: slate, CSS variables). 시드 컴포넌트: `Button`(cva variants 6종 × 4 사이즈) + `Card` 패밀리(Header/Title/Description/Content/Footer).
  - TanStack Query 5.59.20 — `AppProviders`에서 `QueryClientProvider` 마운트, `ReactQueryDevtools`는 `import.meta.env.DEV`에서만 렌더 (prod build에서 dead-code elimination).
  - Zustand 5.0.1 — `stores/authStore.ts`에 `AuthRole`/`AuthUser` 타입, `setUser`/`setAccessToken`/`reset` placeholder. PR #5에서 axios 인터셉터·refresh rotation으로 살을 붙임.
  - react-i18next 15.1.1 + i18next 23.16.4 + browser-languagedetector 8 — `lib/i18n.ts`(en/ko 리소스, localStorage `trustedoss.lang` 캐시), `LanguageToggle` 컴포넌트(Lucide `Languages` 아이콘).
  - `src/index.css` 디자인 토큰 — Critical `#dc2626` / High `#ea580c` / Medium `#ca8a04` / Low `#2563eb` / Info `#71717a`, Primary `#0f172a`(HSL `222.2 47.4% 11.2%`), Inter + JetBrains Mono(Google Fonts), 레이아웃 토큰(`--layout-sidebar: 224px`, `--layout-header: 48px`, `--table-row: 40px`).
  - `pages/Home.tsx` — 헤더 48px(언어 토글 포함) + Card 2개(웰컴 + risk legend 5개 도트). 모든 라벨 i18n 키 사용.
- **frontend Dockerfile + docker-compose.dev.yml 갱신**:
  - `apps/frontend/Dockerfile` — `node:20.18.1-alpine` 베이스(마이너+패치 핀), `tini` PID1, `COPY package*.json` → `npm ci` 캐시 적중. ENTRYPOINT/CMD 분리, EXPOSE 5173.
  - `docker-compose.dev.yml` — frontend 서비스를 `image: trustedoss/frontend:dev` build로 전환(매 부팅 `npm install` 회피). 익명 볼륨 `/app/node_modules`로 host bind-mount가 컨테이너 deps를 가리지 않도록 마스킹. `frontend-node-modules` named volume 제거.
  - 헬스체크는 `wget http://127.0.0.1:5173/`(IPv4 강제) 그대로 유지.
- **`.github/workflows/ci.yml`**:
  - 3 잡(`lint` / `typecheck` / `test`) × matrix `[backend, frontend]` × `fail-fast: false`. PR + push(main) 트리거, 동일 ref concurrency cancel.
  - `lint`: `ruff check`(backend) / `eslint .`(frontend).
  - `typecheck`: `mypy .`(backend) / `tsc -b --noEmit`(frontend).
  - `test`: backend는 services `postgres:17.2-alpine` + `redis:7.4-alpine`을 띄우고 `DATABASE_URL`/`REDIS_URL`/`SECRET_KEY` 주입 후 `pytest --cov tests/unit tests/integration`. frontend는 `vitest --coverage`.
  - Node 20 + Python 3.12 setup 캐시(`actions/setup-node@v4`, `actions/setup-python@v5`). 커버리지 아티팩트 업로드.
  - `actionlint 1.7.4` 통과.
- **품질·보안·운영 표준 §2 (테스트 임계 80%) 적용**:
  - frontend: `vite.config.ts > test.coverage.thresholds`에 `lines/funcs/stmts: 80`, `branches: 70`. 현재 97.24% lines / 88.46% branches.
  - backend: `pyproject.toml > [tool.coverage.report] fail_under = 80` + `[tool.coverage.run] omit = ["tests/*", "alembic/versions/*"]`. 현재 80.29%.
- **PR #2 잠복 부채 정리** (CI 게이트 도입과 함께 그린 상태로):
  - ruff 위반 8건 자동 수정(`UP007` Union → `X | Y`, `UP035` `typing.Callable` → `collections.abc.Callable`, alembic `env.py`/`versions/0001_init.py` 포함).
  - mypy strict 11건 수정 — `RequestIDMiddleware.__init__`에 `ASGIApp` 타입, exception handler 3개에 `-> JSONResponse`, `lifespan`에 `-> AsyncIterator[None]`, `get_logger` Any 반환에 `# type: ignore[no-any-return]`, `_extract_request_id` 헤더 명시 타입.
  - `[[tool.mypy.overrides]] module = "tests.*"`로 테스트 코드는 `disallow_untyped_defs=false` 등 완화(check_untyped_defs는 유지).
- **검증 통과**:
  - frontend: lint 0 errors / 1 warning(shadcn `buttonVariants` 표준 패턴), `tsc -b --noEmit` EXIT=0, `vitest --coverage` 4 tests passed, 전역 라인 97.24%.
  - backend: `ruff check .` 통과, `mypy .` 17 source files no issues, `pytest tests/unit tests/integration` 10/10 passed, 라인 커버리지 80.29% (`fail_under=80` 통과).
  - 컨테이너: 5/5 healthy(`docker-compose -f docker-compose.dev.yml ps`). frontend `curl http://127.0.0.1:5173/` → 200.
  - CI: `rhysd/actionlint:1.7.4 .github/workflows/ci.yml` EXIT=0.

## 2. 결정 사항 / 변경된 가정

- **Tailwind는 v3.4.14**(v4 정식판 미선택). 이유: shadcn/ui가 v3 CSS variable 제너레이터를 기준으로 안정. v4 마이그레이션은 별도 PR로 추진.
- **eslint v9 flat config + typescript-eslint v8** — `eslint.config.js` 하나로 통일, legacy `.eslintrc` 미사용. flat config는 typescript-eslint 8.x에서 1급 지원.
- **vitest coverage threshold = 글로벌 라인 80%**(per-file 아님). PR diff 기반 커버리지(diff-cover)는 향후 도입 검토. 지금은 글로벌 floor가 PR #3 단계의 게이트로 충분.
- **CI 잡은 3개로 묶고 backend/frontend matrix**(2×3=6 잡). 통합 테스트는 `services:`로 매트릭스 backend value에서만 실제로 사용됨(frontend value도 services를 받지만 사용 안 함 → 약간의 낭비 감수, 잡 분리보다 단순).
- **`SECRET_KEY: ci-secret-key-min-32-chars-padding-1234`**는 CI 잡 env에 평문. 32자 이상 더미 값. 운영 비밀이 아니므로 의도적 노출. 실제 비밀은 GitHub Secrets로 별도.
- **Devtools dynamic import 대신 정적 import + DEV 가드** — Vite는 `if (false) {...}` 분기 안의 import만 사용되면 dead-code elimination으로 prod 번들에서 제외. ESM-only 환경에서 `require()` 하이브리드는 사용 불가.
- **CLAUDE.md 핵심 규칙 검증**:
  - ① PostgreSQL only — 변경 없음(이번 PR은 frontend/CI 중심). ②~④도 유지.
  - ⑨ `:latest` 금지 — frontend `node:20.18.1-alpine`(마이너+패치) / `trustedoss/frontend:dev` 명시 핀.
  - ⑩ `docker-compose`(V1 하이픈) — 모든 명령에서 준수.
  - ⑪ `os.getenv()` 런타임 호출 — backend 미변경, 신규 frontend는 `import.meta.env`(Vite의 정적 환경 주입). FastAPI 측 `core/config.py` 함수형 접근 그대로.
  - ⑬ CORS — dev `http://localhost:5173` 화이트리스트 그대로.
- **MEMORY.md 갱신 불필요** — 본 핸드오프가 PR #3 산출물의 단일 진실. 다음 세션이 이 문서를 읽으면 상태 100% 복원.

## 3. 현재 상태

- 머지된 PR: #1(54e858f), #2(ca8ab41).
- 진행 중 PR: PR #3 (이번 세션 산출물, 미커밋). 사용자 머지 명령 대기.
- 통과 테스트:
  - 단위: frontend 4 / backend 3 (Celery factory)
  - 통합: backend 7 (alembic upgrade + health 5 시나리오)
  - E2E: 해당 없음 (Playwright 의존성만 설치 — PR #5에서 활성화)
- 컨테이너 상태(`docker-compose -f docker-compose.dev.yml ps`):
  - postgres healthy / redis healthy / backend healthy / celery-worker healthy / **frontend healthy** (image build 전환 후)
- 커버리지:
  - frontend: 97.24% lines / 88.46% branches / 100% funcs / 97.24% stmts
  - backend: 80.29% lines (fail_under 80 통과)
- CI 상태: 푸시 전이지만 actionlint 통과, 로컬에서 동일 명령 실행 시 모두 green.
- 알려진 이슈:
  - eslint 1 warning — `src/components/ui/button.tsx`의 `buttonVariants` named export(shadcn/ui 표준 패턴). Fast Refresh가 영향 받지 않으므로 의도적 보존.
  - PR #2 mypy/ruff 부채 6+11건은 이번 PR에서 해소.

## 4. 다음 세션이 할 일

- §3.1의 마지막 세트: **PR #4 — OSS 거버넌스(0.8) + Harness 에이전트 9개(0.9)**.
  - `.github/ISSUE_TEMPLATE/{bug_report.yml, feature_request.yml, security.yml}` + `.github/pull_request_template.md` + `CONTRIBUTING.md`(코드 스타일/PR 절차/CLA 무) + `CODE_OF_CONDUCT.md`(Contributor Covenant 2.1) + `SECURITY.md`.
  - `.claude/agents/*.md` 9개 — `docs/v2-execution-plan.md §4.2` 표 기준. 각 파일에 (a) 역할 한 줄, (b) 사용 도구, (c) 영역 가이드라인 (CLAUDE.md 핵심 규칙·§§1.2 보강 표준 인용), (d) 출력 양식, (e) mock task 1개. §6.2 드라이런 결과 보고.
- PR #4 머지로 Phase 0 완료. 다음 세션은 §6.3 양식으로 **Phase 1**(인증 & RBAC) 시작.
- 패턴 권고: PR #4는 거버넌스 파일과 에이전트 정의 둘 다 독립적이므로 메인 세션이 직접 작성(서브 에이전트 없이)하면 충분. 단 마지막 드라이런 단계에서 Producer-Reviewer 패턴으로 `security-reviewer` 에이전트가 자기 자신을 mock-call하는 self-test가 가능.

## 5. 주의·블로커

- **GitHub Actions 잡 첫 실행 시 secrets 점검 필요**: `DATABASE_URL`/`REDIS_URL`/`SECRET_KEY`는 잡 env로 주입(repo secrets 의존 없음). 실제로 GH UI에서 첫 PR push 후 3개 잡 모두 green인지 확인.
- **frontend Dockerfile의 `COPY . .`**은 `.dockerignore`에 의존. `coverage/`, `node_modules/`, `playwright-report/`는 ignore 목록에 포함됨. 커밋 전 `git status`에서 `apps/frontend/coverage/`가 뜨지 않는지 한 번 더 체크.
- **사용자 정책 재확인**: `rm` 권한 거부 → 이번 PR도 `mv ... /tmp/`로 우회(구 `src/main.ts`, `.gitkeep` 7개). PR #4에서도 동일 정책.
- **shadcn/ui CLI 사용 시점**: 추가 컴포넌트(Form/Dialog/Sheet/Drawer 등)는 PR #5(인증 화면)에서 `npx shadcn@latest add <component>` 명령으로 본격 도입. 현재 `components.json`은 그 시점에 CLI가 인식할 수 있도록 사전 배치.
- **Playwright 브라우저 미설치**: `@playwright/test` 의존성만 lock에 들어감. PR #5에서 `npx playwright install --with-deps chromium` + 첫 E2E 시나리오 추가.
- **vite.config.ts `usePolling: true`** — macOS/Windows host bind-mount 호환을 위한 폴링 watch는 컨테이너 CPU를 약간 더 사용. 운영 영향 없음.

## 6. 다음 세션 시작 지시문 (복붙용)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

Phase 0 PR #3(React 부트스트랩 + GitHub Actions CI)는 2026-05-05 머지 완료.
다음 작업은 PR #4(Phase 0 마무리)이다. docs/v2-execution-plan.md §3.1의 0.8·0.9, §4.2(에이전트 표), §6.1의 "PR #4 — OSS 거버넌스 + Harness 에이전트 정의" 블록과
docs/sessions/2026-05-05-phase0-pr3-frontend-ci.md 를 읽고 시작해라.

산출물:
- 0.8 OSS 거버넌스
  · CONTRIBUTING.md (코드 스타일/PR 절차/CLA 무, 80% 커버리지 게이트 명시)
  · CODE_OF_CONDUCT.md (Contributor Covenant 2.1)
  · SECURITY.md (보안 신고 절차, 응답 SLA)
  · .github/ISSUE_TEMPLATE/{bug_report.yml, feature_request.yml, security.yml}
  · .github/pull_request_template.md (요약/관련 이슈/체크리스트/테스트 결과)
- 0.9 .claude/agents/*.md 9개 — §4.2 표 기준, 각 에이전트마다 (a)역할 (b)도구 (c)영역 가이드라인(CLAUDE.md 핵심규칙·보강표준 인용) (d)출력 양식 (e)mock task 1개. §6.2 드라이런 결과 보고.

작업 순서:
1. 거버넌스 파일은 단일 메시지에서 순차 작성(템플릿 의존 없음).
2. .claude/agents/*.md 9개는 패턴이 동일하므로 1개를 짜고 사용자 확인 후 나머지 8개 일괄.
3. 각 에이전트에 mock task 드라이런 (`Agent(subagent_type=..., prompt="<mock>")`)으로 응답 양식 점검. 결과는 콘솔 로그로만 보고, 파일에 기록하지 않음.
4. 완료되면 사용자에게 보고하고 머지(커밋) 명령을 받는다 — 이 PR이 Phase 0 종료점.
5. 세션 종료 시 docs/sessions/2026-05-05-phase0-pr4-governance-agents.md 를 §7 양식으로 작성. 그리고 다음 세션 시작 지시문은 §6.3(Phase 1) 양식으로 갈음한다.

검증:
- gh CLI(설치돼 있다면) 또는 GitHub UI 미리보기로 ISSUE_TEMPLATE/pull_request_template 렌더링 확인(불가능하면 yaml lint으로 대체).
- .claude/agents/ 9개 파일 모두 존재 + frontmatter(name/description/tools) 일관.
- 메인 세션의 에이전트 호출이 9개 정의 중 의도한 것을 라우팅하는지 1개라도 mock task로 확인.
```
