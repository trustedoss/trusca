---
id: getting-started
title: Getting started
description: Clone the monorepo, bring up the dev stack with docker-compose, and ship your first PR to TrustedOSS Portal.
sidebar_label: Getting started
sidebar_position: 1
---

# Getting started

Welcome to the TrustedOSS Portal contributor track. This page takes you from a clean machine to your first merged PR.

:::note Audience
Developers comfortable with Python (FastAPI / Pydantic), TypeScript (React 18 / Vite), Docker, and Git. No prior knowledge of Software Composition Analysis is required — the codebase is well factored.
:::

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| `docker-compose` | **V1, hyphenated** | The dev stack is composed against V1; V2 (`docker compose`) is not supported. |
| Node.js | ≥ 20 LTS | Frontend (Vite) and Docusaurus. |
| Python | 3.12 | Backend, Celery worker, Alembic. |
| Go SDK | ≥ 1.21 | Required by `cdxgen` for Go-module scans during local end-to-end runs. |
| `git` | ≥ 2.40 | Branch / PR workflow. |
| `gh` (GitHub CLI) | ≥ 2.40 | PR creation from the shell. |

You can develop without Go if you do not run scans locally — `cdxgen` only needs it on the worker.

## Clone and branch

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal

# branch naming: feature/* for new features, chore/* for maintenance,
# fix/* for bug fixes, docs/* for documentation-only changes.
git checkout -b feature/short-imperative-summary
```

We rebase rather than merge. Keep your branch close to `main`:

```bash
git fetch origin
git rebase origin/main
```

## Bring up the dev stack

A single command starts PostgreSQL 17, Redis 7, the Celery worker, the FastAPI backend (with `--reload`), and the Vite dev server with HMR:

```bash
docker-compose -f docker-compose.dev.yml up -d
```

First start pulls images and warms the cache; expect ~3 minutes. Subsequent starts take ~10 seconds.

Tail the logs:

```bash
docker-compose -f docker-compose.dev.yml logs -f backend worker
```

The portal is now reachable at:

- **Frontend (Vite):** http://localhost:5173
- **Backend (FastAPI):** http://localhost:8000 (OpenAPI at `/docs`)
- **PostgreSQL:** `localhost:5432`, user / password / db = `trustedoss`

### Local backend (without docker-compose)

If you prefer running the backend on the host (faster iteration, easier debugger attach), keep PostgreSQL + Redis in docker-compose and run the FastAPI app locally:

```bash
cd apps/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Apply migrations
alembic upgrade head

# Run the API
uvicorn main:app --reload --port 8000
```

Run the Celery worker in a second shell:

```bash
celery -A tasks.app worker --loglevel=info
```

### Local frontend

```bash
cd apps/frontend
npm install
npm run dev
```

Vite serves on http://localhost:5173 and proxies `/v1/*`, `/auth/*`, and `/ws/*` to the backend (defaulting to the docker-compose service `backend:8000`). When running Vite on the host instead of inside docker-compose, override the proxy target:

```bash
VITE_PROXY_BACKEND=http://localhost:8000 \
VITE_PROXY_WS=ws://localhost:8000 \
  npm run dev
```

The Playwright harness exercises the same proxy when no `BACKEND_BASE_URL` / `VITE_API_BASE_URL` env override is set.

## Reset the dev stack

Postgres data corruption, a stale celery-worker image (`aiosmtplib` ModuleNotFoundError), or a full disk all share one fix — recreate the named volumes and rebuild the worker.

```bash
make dev-rebuild-worker          # only the worker (keeps DB data)
make dev-reset                   # interactive: drops volumes, recreates stack
make dev-reset-rebuild           # destroys volumes, rebuilds worker, seeds e2e user
```

`scripts/dev-reset.sh` is project-scoped — it only prunes volumes labelled `com.docker.compose.project=trustedoss-portal`, leaving other Compose projects on the host untouched. Pass `--no-prompt` to skip the destructive-action confirmation in CI.

## Run the tests

```bash
# Backend unit + integration
cd apps/backend && pytest -q

# Frontend unit
cd apps/frontend && npm test

# E2E (Playwright) — backend + frontend must be up
cd apps/frontend && npm run test:e2e
```

The PR merge gate requires **≥ 80 % line coverage on changed code** and **all E2E core scenarios green**.

## Your first PR

```bash
git add -p                      # stage selectively
git commit -m "feat: short imperative summary"
git push -u origin HEAD

gh pr create --fill --web       # opens the PR draft in your browser
```

The CI workflow runs lint, typecheck, unit tests, integration tests, and a Playwright smoke. Address any red checks; once green, request review from a code-owner of the touched paths.

We **squash-merge** to keep `main` linear and the changelog readable. Your PR title becomes the squash commit subject — write it in imperative mood, ≤ 72 characters.

## See also

- [Coding standards](./coding-standards.md) — TypeScript strict, Pydantic v2, Alembic forward-only, RFC 7807, structlog.
- [Testing guide](./testing-guide.md) — pytest layout, Playwright `PortalPage` harness, adversarial input matrices.
- [Agent team](./agent-team.md) — when and how to enlist the security reviewer.
