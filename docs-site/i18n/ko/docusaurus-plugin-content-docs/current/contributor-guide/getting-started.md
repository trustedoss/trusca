---
id: getting-started
title: 시작하기
description: 모노레포를 클론하고 docker-compose로 dev 스택을 띄운 다음 TrustedOSS Portal에 첫 PR을 보냅니다.
sidebar_label: 시작하기
sidebar_position: 1
---

# 시작하기

TrustedOSS Portal 컨트리뷰터 트랙에 오신 것을 환영합니다. 이 페이지는 깨끗한 머신에서 첫 머지된 PR까지의 과정을 안내합니다.

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
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal

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

Vite는 `http://localhost:5173`에서 서비스하며 `/api`를 백엔드로 프록시합니다.

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

## 함께 보기

- [코딩 표준](./coding-standards.md) — TypeScript strict, Pydantic v2, Alembic forward-only, RFC 7807, structlog.
- [테스트 가이드](./testing-guide.md) — pytest 레이아웃, Playwright `PortalPage` 하네스, 적대적 입력 매트릭스.
- [에이전트 팀](./agent-team.md) — security-reviewer를 언제 어떻게 동원할지.
