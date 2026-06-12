# Contributing to TRUSCA

Thank you for your interest in contributing! TRUSCA is an Apache-2.0 licensed, self-hosted SCA portal, and we welcome contributions from the community — code, documentation, translations, bug reports, and design feedback.

This document describes how to set up the project locally, the conventions we follow, and what we expect in a pull request.

> **AI-assisted development.** This project is developed with AI-assisted tooling (Claude Code) for scaffolding, refactoring, and review. Design decisions, code review, and accountability for every merged change remain human-owned by the maintainers listed in [`MAINTAINERS.md`](MAINTAINERS.md). Pull requests from contributors using similar tooling are welcome — please disclose in the PR description and treat the AI as a collaborator, not the author.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Workflow](#development-workflow)
4. [Coding Standards](#coding-standards)
5. [Testing & Coverage Gates](#testing--coverage-gates)
6. [Harness-First Principle](#harness-first-principle)
7. [Pull Request Process](#pull-request-process)
8. [Commit Messages](#commit-messages)
9. [Internationalization (i18n)](#internationalization-i18n)
10. [Documentation](#documentation)
11. [Security Issues](#security-issues)
12. [License & DCO](#license--dco)

---

## Code of Conduct

This project adheres to the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). By participating, you agree to uphold its terms. Report unacceptable behavior to **conduct@trustedoss.io**.

---

## Getting Started

### Prerequisites

- **Docker** + **Docker Compose V1** (the hyphenated `docker-compose` command — V2 / `docker compose` is not supported in our development environment)
- **Python 3.12** (backend)
- **Node.js 20** (frontend)
- **Git**

### Bootstrap the dev stack

```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca
cp .env.example .env  # adjust as needed
docker-compose -f docker-compose.dev.yml up -d
```

After ~30 seconds, all five containers (`postgres`, `redis`, `backend`, `celery-worker`, `frontend`) should be `healthy`. The frontend is served on `http://localhost:5173`, the backend API on `http://localhost:8000`.

### Running tests locally

```bash
# Backend
cd apps/backend
pytest tests/unit tests/integration --cov

# Frontend
cd apps/frontend
npm run test -- --coverage
```

---

## Development Workflow

### Branch model

- `main` — protected, deployable. Direct pushes are disabled; everything goes through pull requests.
- `feature/<short-topic>` — your working branch. Keep it small and focused.

### Picking work

- Browse open issues labeled `good first issue` or `help wanted` in the [issue tracker](https://github.com/trustedoss/trusca/issues).
- For larger features, open a discussion or feature-request issue first so we can align on scope before you write code.

### Keeping in sync

```bash
git fetch origin
git rebase origin/main
```

Rebase, don't merge — we keep `main` linear.

---

## Coding Standards

We treat the codebase as a global commercial product, not a personal project. Be tasteful.

### Backend (Python / FastAPI)

- **Style:** [`ruff`](https://github.com/astral-sh/ruff) (lint + format). Run `ruff check . && ruff format .` before committing.
- **Types:** [`mypy`](https://www.mypy-lang.org/) strict mode. Public functions must be fully annotated.
- **Async first:** prefer `async def` for I/O-bound endpoints, services, and integrations. SQLAlchemy 2.0 async sessions are the default.
- **Errors:** all 4xx / 5xx responses use [RFC 7807 Problem Details](https://www.rfc-editor.org/rfc/rfc7807) (`application/problem+json`). Required fields: `type`, `title`, `status`, `detail`, `instance`. Domain extensions are `snake_case`.
- **Logging:** [`structlog`](https://www.structlog.org/) JSON lines, one event per line. `request_id`, `user_id`, `team_id`, and `task_id` are propagated automatically. Never log secrets, tokens, or full email addresses — use the `mask_pii` helper.
- **Configuration:** call `os.getenv()` at runtime, not module load. Never cache env vars in module-level constants.
- **Database:** PostgreSQL only — no SQLite, no in-memory. Schema changes require a new Alembic migration.
- **Migrations:** forward-only. `downgrade()` is `pass` or `raise NotImplementedError`. Schema and data migrations are separate revisions. Breaking changes follow expand → migrate-data → contract.

### Frontend (TypeScript / React 18)

- **Style:** [`eslint`](https://eslint.org/) flat config + [`prettier`](https://prettier.io/) (run `npm run lint && npm run format`).
- **Types:** strict TypeScript. `any` requires a justification comment.
- **Components:** prefer [`shadcn/ui`](https://ui.shadcn.com/) primitives. Custom UI must use Tailwind design tokens (see `src/index.css`) — never hardcode colors or sizes.
- **State:** server state lives in [TanStack Query](https://tanstack.com/query); client UI state lives in [Zustand](https://zustand-demo.pmnd.rs/). Don't mix.
- **i18n:** every user-visible string goes through `t()`. No hardcoded English in JSX.

### Docker / DevOps

- **Image tags:** never `:latest`. Pin to a minor + patch version (e.g. `node:20.18.1-alpine`, `postgres:17.2-alpine`).
- **Compose:** use `docker-compose` (V1, hyphenated). `docker compose` (V2) is not supported.
- **Secrets:** never commit. Use `.env.example` for the schema; real values go in `.env` (git-ignored) or GitHub Actions secrets.

---

## Testing & Coverage Gates

We block PRs that lower test coverage. The thresholds are enforced in CI:

| Scope | Tool | Threshold |
|---|---|---|
| Backend lines | `pytest --cov` | **≥ 80%** (`fail_under=80` in `pyproject.toml`) |
| Frontend lines | `vitest --coverage` | **≥ 80%** lines / 70% branches (`vite.config.ts`) |
| E2E core scenarios | Playwright (harness pattern) | always green |

A change that lowers coverage below the floor will fail CI. Add tests for the lines you write.

### What to test

- **Unit:** pure functions, schemas, parsers, RBAC predicates.
- **Integration:** anything that touches PostgreSQL, Redis, Celery, or an external integration. Use real services (via `docker-compose`), not mocks.
- **E2E:** user-visible flows. Login, scan execution, report download, admin actions.

> Mocks for external paid APIs (e.g., GitHub App, GCP) are acceptable. Mocks for our own database / queue are not.

---

## Harness-First Principle

> Write the test harness before the feature, not after.

Every new screen or domain area must ship with its **harness** — a class or module that exposes the domain in test-friendly verbs (`auth.login()`, `scan.expectInProgress()`, `project.openVulnerabilitiesTab()`). The feature implementation comes second.

Why:
- Refactors stay cheap. UI restyles don't break tests because tests speak domain language, not selectors.
- Tests document behavior. Reading the harness tells you what the feature is supposed to do.
- Reviewers can read tests first to understand the change.

If you add a feature with no harness, the PR is incomplete. See `apps/frontend/tests/_harness/PortalPage.ts` and `apps/backend/tests/_harness/` (forthcoming) for examples.

---

## Pull Request Process

1. **Fork & branch** — `git checkout -b feature/my-change`.
2. **Implement** — follow the coding standards above. Keep PRs small (< 500 lines diff is the sweet spot; > 1000 lines should usually be split).
3. **Self-review** — run lint, typecheck, tests locally. Fix all warnings, not just errors.
4. **Open the PR** — fill out the [pull request template](.github/pull_request_template.md) completely. Empty checklists block review.
5. **CI must pass** — all three jobs (lint, typecheck, test) on both backend and frontend matrices. We do not merge red.
6. **Review** — at least one maintainer approval. Security-sensitive changes (auth, API keys, Trivy / external scanner integrations, OAuth, build gate) require additional review by a maintainer with the `security` role.
7. **Merge** — maintainers merge via "Squash and merge" to keep `main` linear. The squash message uses the PR title — write good titles.

### What gets a PR rejected

- Coverage drop below 80%
- New strings without i18n keys (or KO translations missing)
- New endpoint without OpenAPI documentation
- New feature without an updated Docusaurus page
- `docker compose` (V2) usage, `:latest` tags, or module-level `os.getenv()` caching
- Mocking the database in tests
- Backwards-compat shims that have no current consumer

### CLA

We do **not** require a Contributor License Agreement. Your contribution is licensed under Apache-2.0 by the act of submitting it (see [License & DCO](#license--dco)).

---

## Commit Messages

We follow a relaxed [Conventional Commits](https://www.conventionalcommits.org/) style:

```
<type>(<scope>): <short summary>

<body — what and why, not how>

<footer — refs, breaking changes>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`, `build`, `perf`, `style`.

Examples:
```
feat(auth): add refresh token rotation with reuse detection
fix(dt): retry on 502 with exponential backoff
docs(install): document upgrade path for v2.0.1
```

Squash-merged PRs inherit the PR title — make it conventional.

---

## Internationalization (i18n)

Every user-visible string must exist in both **English** (`apps/frontend/src/locales/en/*.json`) and **Korean** (`apps/frontend/src/locales/ko/*.json`). PRs that add only English will be asked to add Korean before merge.

Translation conventions:
- Keys are flat and dot-namespaced: `auth.login.submit`.
- Korean translations follow [`docs/glossary.md`](docs/glossary.md) (forthcoming) for domain terms.
- Use ICU plural / select syntax for variable counts.

CI runs `i18next-parser --fail-on-update` to catch missing keys.

---

## Documentation

Every user-facing feature ships with a Docusaurus page in `docs-site/docs/`. Backend API changes update the OpenAPI schema (FastAPI auto-generates this) and are reflected in the hosted API Reference at `/reference/api`.

The public roadmap and release history live in [`ROADMAP.md`](ROADMAP.md) and [`CHANGELOG.md`](CHANGELOG.md). Larger proposals go through a GitHub issue / discussion before a PR — see [`GOVERNANCE.md`](GOVERNANCE.md).

---

## Security Issues

**Do not open public issues for security vulnerabilities.** See [`SECURITY.md`](SECURITY.md) for the responsible disclosure process and our response SLA.

---

## License & DCO

By contributing to this project, you certify that:

1. The contribution is your original work, or you have the right to submit it.
2. You license your contribution under the [Apache License 2.0](LICENSE).
3. You understand that the project and your contribution are public.

This is the [Developer Certificate of Origin (DCO) 1.1](https://developercertificate.org/) in spirit. We do not require sign-offs in commits, but the same understanding applies.

---

## Related documents

- [`GOVERNANCE.md`](GOVERNANCE.md) — how decisions are made and how to become a maintainer
- [`MAINTAINERS.md`](MAINTAINERS.md) — who maintains which area
- [`SUPPORT.md`](SUPPORT.md) — where to ask questions and report problems
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — community standards
- [`SECURITY.md`](SECURITY.md) — vulnerability disclosure

---

Thanks again for contributing — every PR, issue, translation, and design suggestion makes the project better.

— The TRUSCA maintainers
