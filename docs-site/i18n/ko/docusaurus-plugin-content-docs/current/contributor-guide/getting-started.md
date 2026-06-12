---
id: getting-started
title: 시작하기
description: 모노레포를 클론하고 docker-compose로 dev 스택을 띄운 다음 TRUSCA에 첫 PR을 보냅니다.
sidebar_label: 시작하기
sidebar_position: 1
---

# 시작하기

TRUSCA 컨트리뷰터 트랙에 오신 것을 환영합니다. 이 페이지는 깨끗한 머신에서 첫 머지된 PR까지의 과정을 안내합니다.

:::note 대상 독자
Python(FastAPI / Pydantic), TypeScript(React 18 / Vite), Docker, Git에 익숙한 개발자. SCA(Software Composition Analysis)에 대한 사전 지식은 필요 없습니다 — 코드베이스가 잘 분리되어 있습니다.
:::

## 사전 요구 사항

| 도구 | 버전 | 이유 |
|---|---|---|
| `docker-compose` | **V1, 하이픈 형식** | dev 스택은 V1 기준으로 구성되어 있습니다. V2(`docker compose`)는 지원되지 않습니다. |
| Node.js | ≥ 20 LTS | Frontend(Vite)와 Docusaurus. |
| Python | 3.12 | Backend, Celery worker, Alembic. |
| Go SDK | ≥ 1.21 | 로컬 종단 간 실행 시 `cdxgen`이 Go 모듈 스캔에 필요. |
| `git` | ≥ 2.40 | Branch / PR 워크플로. |
| `gh` (GitHub CLI) | ≥ 2.40 | 셸에서 PR 생성. |

스캔을 로컬에서 돌리지 않는다면 Go 없이도 개발 가능합니다 — `cdxgen`은 worker에서만 필요합니다.

## 클론과 브랜치

```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca

# 브랜치 명명: 새 기능은 feature/*, 유지보수는 chore/*,
# 버그 픽스는 fix/*, 문서 전용 변경은 docs/*.
git checkout -b feature/short-imperative-summary
```

merge가 아닌 rebase를 사용합니다. 브랜치를 `main`에 가깝게 유지하세요.

```bash
git fetch origin
git rebase origin/main
```

## dev 스택 띄우기

단일 명령으로 PostgreSQL 17, Redis 7, Celery worker, FastAPI 백엔드(`--reload`), HMR 가능한 Vite dev 서버가 시작됩니다.

```bash
docker-compose -f docker-compose.dev.yml up -d
```

최초 시작은 이미지를 받고 캐시를 데우느라 ~3분 정도 걸립니다. 이후 시작은 ~10초.

로그 추적:

```bash
docker-compose -f docker-compose.dev.yml logs -f backend worker
```

이제 포털에 접속할 수 있습니다.

- **Frontend (Vite):** http://localhost:5173
- **Backend (FastAPI):** http://localhost:8000 (OpenAPI는 `/docs`)
- **PostgreSQL:** `localhost:5432`, user / password / db = `trustedoss`

### 로컬 backend(docker-compose 없이)

호스트에서 백엔드를 직접 실행하는 게 더 빠른 반복과 디버거 부착에 유리합니다. PostgreSQL + Redis는 docker-compose에 두고 FastAPI 앱만 로컬로 실행하세요.

```bash
cd apps/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 마이그레이션 적용
alembic upgrade head

# API 실행
uvicorn main:app --reload --port 8000
```

별도 셸에서 Celery worker 실행:

```bash
celery -A tasks.app worker --loglevel=info
```

### 로컬 frontend

```bash
cd apps/frontend
npm install
npm run dev
```

Vite는 `http://localhost:5173`에서 서비스하며 `/v1/*`, `/auth/*`, `/ws/*` 를 백엔드(기본값: docker-compose 서비스 `backend:8000`)로 프록시합니다. docker-compose 안이 아닌 호스트에서 Vite를 직접 실행할 때는 프록시 대상을 덮어쓰세요.

```bash
VITE_PROXY_BACKEND=http://localhost:8000 \
VITE_PROXY_WS=ws://localhost:8000 \
  npm run dev
```

Playwright 하네스도 `BACKEND_BASE_URL` / `VITE_API_BASE_URL` env 가 비어 있으면 동일한 프록시를 통해 백엔드에 접근합니다.

## dev 스택 리셋

Postgres 데이터 손상, 오래된 celery-worker 이미지(`aiosmtplib` ModuleNotFoundError), 디스크 가득참 — 모두 동일한 처방으로 해결합니다. named volume을 다시 만들고 worker 이미지를 재빌드합니다.

```bash
make dev-rebuild-worker          # worker 만 재빌드 (DB 데이터 보존)
make dev-reset                   # 대화형: 볼륨 삭제 후 스택 재생성
make dev-reset-rebuild           # 볼륨 삭제 + worker 재빌드 + e2e 사용자 시드
```

`scripts/dev-reset.sh`는 프로젝트 범위입니다 — `com.docker.compose.project=trustedoss-portal` 라벨이 붙은 볼륨만 prune 하므로 동일 호스트의 다른 Compose 프로젝트는 영향받지 않습니다. CI에서는 `--no-prompt` 로 확인 프롬프트를 건너뛸 수 있습니다.

## 테스트 실행

```bash
# Backend 단위 + 통합
cd apps/backend && pytest -q

# Frontend 단위
cd apps/frontend && npm test

# E2E (Playwright) — backend + frontend 가 떠 있어야 함
cd apps/frontend && npm run test:e2e
```

PR 머지 게이트는 **변경된 코드의 line coverage ≥ 80 %**와 **모든 E2E 핵심 시나리오 green**입니다.

## 첫 PR

```bash
git add -p                      # 선택적 스테이징
git commit -m "feat: short imperative summary"
git push -u origin HEAD

gh pr create --fill --web       # 브라우저에서 PR 초안이 열립니다
```

CI 워크플로는 lint, typecheck, 단위 테스트, 통합 테스트, Playwright 스모크를 실행합니다. 빨간 체크가 있으면 해결한 뒤 변경된 경로의 코드 오너에게 리뷰를 요청하세요.

`main`을 선형으로 유지하고 changelog 가독성을 위해 **squash-merge**를 사용합니다. PR 제목이 squash 커밋 제목이 됩니다 — 명령형으로, 72자 이내로 작성하세요.

## 가이드 스크린샷 재생성

사용자·관리자·컨트리뷰터 가이드는 `docs-site/static/img/screenshots/` 아래의 PNG 캡처를 동반합니다. 이 캡처는 Playwright spec(`apps/frontend/tests/screenshots/capture.spec.ts`)이 갓 시드한 super-admin 으로 SPA 를 드라이브하여 생성합니다.

다음 시점에 캡처를 재생성하세요:

- 가이드 스크린샷이 묘사하는 UI 요소가 추가·변경된 경우,
- 새 가이드 단락에 이미지가 필요한 경우, 또는
- 디자인 시스템(타이포그래피·간격·컬러)이 기존 캡처를 무효화할 만큼 변한 경우.

```bash
# 1. dev stack 기동 — 캡처는 실제 backend / frontend 를 호출합니다.
make dev-up

# 2. 캡처. 시드는 spec 내부에서
#    `seedE2eUser(--super-admin --with-scan --component-count 50 …)`
#    을 통해 자동 실행됩니다.
make screenshots-capture
```

Makefile 타겟은 `playwright.screenshots.config.ts`(e2e 설정과 분리)를 호출하므로 일반 `npm run test:e2e` 매트릭스가 캡처를 우발적으로 트리거하지 않습니다.

PNG 는 `docs-site/static/img/screenshots/<page-slug>-<section-slug>.png` 로 직접 떨어집니다. Markdown 참조는 절대 경로 `/img/screenshots/<file>.png` 를 사용하므로 EN·KO 가 하나의 자산을 공유합니다 — 캡처를 i18n 디렉토리에 **복사하지 마세요**.

새 캡처 추가 절차:

1. 기존 하네스(`AdminBackupHarness`, `AuthHarness`, …)를 활용해 SPA 를 새 `test()` 블록 안에서 드라이브하세요. 직접 `page.click()` 은 금지 — verb 가 부족하면 하네스부터 확장합니다.
2. 보존하고 싶은 시점에 `captureScreenshot(page, "<slug>")` 호출.
3. 같은 PR 안에서 `![대체 텍스트](/img/screenshots/<slug>.png)` 를 EN 마크다운과 KO 미러 양쪽에 삽입.
4. KO 는 alt text 만 번역 — PNG 는 공유 자산입니다.

시각 회귀 테스트(Percy / Chromatic / 픽셀 diff)는 의도적으로 본 시리즈 OUT 입니다 — 캡처는 1회성 매뉴얼 자산이며 UI 가 움직일 때만 재생성합니다.

## 함께 보기

- [코딩 표준](./coding-standards.md) — TypeScript strict, Pydantic v2, Alembic forward-only, RFC 7807, structlog.
- [테스트 가이드](./testing-guide.md) — pytest 레이아웃, Playwright `PortalPage` 하네스, 적대적 입력 매트릭스.
- [에이전트 팀](./agent-team.md) — security-reviewer를 언제 어떻게 동원할지.
