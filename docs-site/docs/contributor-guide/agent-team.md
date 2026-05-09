---
id: agent-team
title: Agent team
description: The nine specialist agents that build TrustedOSS Portal, the Producer-Reviewer pattern, and when to trigger security review.
sidebar_label: Agent team
sidebar_position: 4
---

# Agent team

TrustedOSS Portal is built by an orchestrated team of nine specialist agents. Each owns a domain and is invoked by the harness when the work crosses into that domain. This page is contributor-facing: it tells you which agent does what, when to trigger which one, and the **mandatory security-review checkpoints**.

:::note Audience
Contributors who use the agent harness to ship work, and reviewers who want to know which checkpoints are non-negotiable.
:::

## The nine agents

| Agent | Owns | Typical phases |
|---|---|---|
| **`backend-developer`** | FastAPI endpoints, Pydantic schemas, business logic in `apps/backend/services/`. | Phase 1 ~ 5 |
| **`db-designer`** | PostgreSQL schema, Alembic forward-only migrations, indexes, constraints. | Phase 0 ~ 1 |
| **`scan-pipeline-specialist`** | Celery tasks, the cdxgen / ORT / Trivy / Dependency-Track integrations, the DT circuit breaker. | Phase 2 |
| **`frontend-dev`** | React 18 + shadcn/ui components, TanStack Query hooks, Zustand stores, route wiring. | Phase 2 ~ 6 |
| **`i18n-specialist`** | `react-i18next` setup, EN / KO translations, the `i18next-parser` drift gate, locale toggle. | Phase 6 |
| **`devops-engineer`** | Docker Compose dev / prod, GitHub Actions, the Helm chart, install / upgrade / backup / restore scripts. | Phase 0, 7 ~ 8 |
| **`test-writer`** | pytest unit + integration, the Playwright `PortalPage` harness, adversarial-input matrices. | every phase |
| **`doc-writer`** | Docusaurus pages, EN / KO docs, the API reference, this contributor guide. | Phase 7 |
| **`security-reviewer`** | OWASP Top 10 review, dependency CVE triage, audit-log verification, post-implementation security signoff. | Phase 8 (and as needed) |

## How they collaborate — patterns

The orchestrator routes work between agents using four patterns:

### Fan-out / fan-in

Independent strands of a phase run in parallel:

```
                   ┌── backend-developer (endpoint)
Phase 4 admin  ──┬─┤
                   ├── frontend-dev (UI)
                   ├── test-writer (harness + tests)
                   └── doc-writer (admin guide)
```

The orchestrator merges only when **every strand is green**.

### Producer-Reviewer

A producer agent drafts; a reviewer agent challenges. Used for security-critical paths — see [Mandatory security-review checkpoints](#mandatory-security-review-checkpoints) below.

### Pipeline

Phases with ordering constraints are pipelined: Phase 0 (foundation) → Phase 1 (auth) → Phase 2 (scans) → … . A downstream agent does not start until its upstream dependency is merged.

### Expert pool

The orchestrator routes to whichever agent matches the domain. A migration goes to `db-designer`; a Helm change goes to `devops-engineer`. Contributors who write code directly should mentally pick the agent and follow its conventions.

## Mandatory security-review checkpoints

`security-reviewer` is **not** an optional courtesy review. The following code paths trigger Producer-Reviewer mandatorily — a PR touching them does not merge without `security-reviewer` signoff:

1. **Authentication and session** — `apps/backend/auth/`, JWT issuance, refresh-token rotation, session cookie policy, password hashing config, rate-limit config on auth routes.
2. **API key management** — `apps/backend/services/api_key_service.py`, hashing, prefix lookup, scope semantics, revocation propagation, audit emission.
3. **DT (Dependency-Track) API calls** — outbound requests to DT, the circuit breaker, the cached-vulnerability fallback, orphan project cleanup.
4. **OAuth flow** — `apps/backend/auth/oauth/`, identity matching by `(provider, provider_user_id)`, signed-state CSRF token, the seven-error-code mapping, `redirect_after` pass-through.
5. **CI build gate** — `apps/backend/services/policy_gate.py`, the `gate=pass|fail` decision, exit-code-1 contract on the action / template / Jenkinsfile.
6. **Backup / restore destructive flow** — `apps/backend/tasks/backup.py`, the `/admin/backup` Upload+Restore endpoint, the `X-Confirm-Restore` precondition, the typing-gate, super-admin enforcement.

The reviewer checks (at minimum): RFC 7807 conformance, structlog PII masking, adversarial-input parametrize coverage, audit-log emission, OpenAPI schema additions, rate-limit settings, input validation at the edge.

## How to trigger security-review

If your PR touches any of the six checkpoints above:

1. In the PR description, add a `## Security checkpoints` section listing the rule numbers above.
2. Mention `@security-reviewer` (the agent identifier) and / or apply the `security-review-required` label.
3. Address the reviewer's comments before requesting normal code review.

If you are unsure whether a path is security-critical, **request the review anyway**. False positives are cheap; missed reviews ship vulnerabilities.

## Reading agent definitions

Agent definitions live outside this repository in `revfactory/harness`. Each definition specifies role, allowed tools, domain guidelines, output format, and a mock task. The harness orchestrator reads these on every invocation. As a contributor, you do not configure agents directly — you write the PR and the orchestrator routes.

## See also

- [Getting started](./getting-started.md) — set up your environment.
- [Coding standards](./coding-standards.md) — the conventions every agent enforces.
- [Testing guide](./testing-guide.md) — what `test-writer` expects to see in the diff.
