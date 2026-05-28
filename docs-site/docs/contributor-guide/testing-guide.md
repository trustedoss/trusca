---
id: testing-guide
title: Testing guide
description: pytest layout, the Playwright PortalPage harness, adversarial-input parametrize, and the 80% coverage merge gate.
sidebar_label: Testing guide
sidebar_position: 3
---

# Testing guide

Tests are first-class. The PR merge gate is **≥ 80 % line coverage on changed code** and **all E2E core scenarios green**. This page walks the layout, the harness pattern, and the adversarial-input rules that catch the bugs static analysis cannot.

:::note Audience
All contributors. Apply on every PR that touches `apps/backend/` or `apps/frontend/`.
:::

## Backend — pytest

Tests live in `apps/backend/tests/` and split into three tiers:

```
apps/backend/tests/
├── unit/             # pure-function tests, no DB, no network
├── integration/      # FastAPI TestClient + Postgres (testcontainers)
└── e2e/              # backend-only black-box flows; not the Playwright suite
```

`conftest.py` at each level exposes the right fixtures. The top-level `conftest.py` provides the cross-tier helpers (factories, time freezing).

### Run a focused set

```bash
cd apps/backend

# Whole suite
pytest -q

# Single tier
pytest -q tests/unit

# By keyword
pytest -q -k "api_key and revoke"

# Single test, with prints
pytest -s tests/integration/test_api_key_endpoints.py::test_revoke_immediate
```

### Coverage

```bash
pytest --cov=. --cov-report=term-missing --cov-report=xml
```

Aim for ≥ 80 % line coverage on **changed lines**. The CI `coverage diff` job reports the per-file delta; hovering coverage at 79 % blocks merge.

### Layout rule of thumb

- **Unit:** the function under test takes no database, no HTTP, no Celery. Mock at the boundary.
- **Integration:** the route is exercised end-to-end via FastAPI TestClient, with a real PostgreSQL via `pytest-testcontainers`. **No mocking of SQLAlchemy.**
- **E2E (backend):** drives the API as a black box using HTTPX, with the worker actually running in another fixture. Used sparingly — Playwright is the primary E2E.

## Frontend — Playwright with the `PortalPage` harness

`apps/frontend/tests/_harness/PortalPage.ts` defines a domain-language Page Object. **Test code never calls `page.click(...)` directly.**

### Why the harness

Tests phrased in domain verbs survive UI churn. The same scenario reads:

```ts
// ❌ brittle — breaks when the modal markup changes
await page.click("button:has-text('New API key')");
await page.fill("input[name='label']", "ci-runner");
await page.click("button:has-text('Create')");

// ✅ stable — speaks the product's language
await portal.createApiKey({ label: "ci-runner", scope: "team", expiryDays: 90 });
```

### Add a verb to the harness

When you add a new screen or a new flow, **add a verb to `PortalPage` first**, then write the scenario:

```ts
// apps/frontend/tests/_harness/PortalPage.ts
async createApiKey(opts: { label: string; scope: ApiKeyScope; expiryDays: number }) {
  await this.page.getByRole("button", { name: "New API key" }).click();
  await this.page.getByLabel("Label").fill(opts.label);
  await this.page.getByLabel("Scope").selectOption(opts.scope);
  await this.page.getByLabel("Expiry").selectOption(`${opts.expiryDays}d`);
  await this.page.getByRole("button", { name: "Create" }).click();
  return this.captureKeyFromOneTimeRevealModal();
}
```

The harness has ~17 verbs today; a contributor reading `PortalPage.ts` should be able to retell the product's user journey.

### Run

```bash
cd apps/frontend
npm run test:e2e          # all scenarios
npm run test:e2e -- --grep "api keys"   # filtered
npm run test:e2e:headed   # visible browser, useful when debugging
```

The dev stack must be up (`docker-compose -f docker-compose.dev.yml up -d`) before E2E runs.

## Adversarial input — parametrize is mandatory

Any code that parses **untrusted input** must be exercised against a parametrized matrix of adversarial cases. The portal has been bitten by this before — chore PR #7's recursive `normalize_spdx_id` was 88 % covered and still admitted a DoS via separator-only tokens.

### Surfaces in scope

- Registry metadata parsers (`packages/`, `npm`, `pypi`, `cargo`, `go.mod`).
- Webhook URL / payload parsers (GitHub, GitLab, Slack, Teams).
- SPDX / CycloneDX expression normalisers.
- OAuth `state` and `code` parsers.
- Anywhere user content is interpolated into a regex, a path, or a shell.

### The matrix

For each surface, parametrize over **at minimum** these adversarial inputs:

| Class | Examples |
|---|---|
| Separator-only tokens | `"AND"`, `"OR"`, `"WITH"`, `"OR OR OR"`, `" "` |
| Scheme abuse | `"javascript:alert(1)"`, `"file:///etc/passwd"`, `"data:text/html,..."` |
| Oversized | 1 MiB string, 65 535 nested parens, 10 000-char URL |
| Control bytes | CRLF (`"\r\n"`), null byte (`"\x00"`), BOM (`"﻿"`) |
| Unicode tricks | RTL override (`"‮"`), homoglyph (`"аpple"` Cyrillic), zero-width (`"​"`) |
| Empty / whitespace | `""`, `"   "`, `"\t\n"` |

Use `pytest.mark.parametrize` and label each case so failure messages are diagnostic:

```python
@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param("MIT AND Apache-2.0", ["MIT", "Apache-2.0"], id="happy-path"),
        pytest.param("AND", [], id="separator-only-token"),
        pytest.param("javascript:alert(1)", [], id="scheme-abuse"),
        pytest.param("(" * 10_000 + "MIT" + ")" * 10_000, ["MIT"], id="deep-nesting"),
        pytest.param("MIT\r\nApache-2.0", ["MIT", "Apache-2.0"], id="crlf-injection"),
        pytest.param("MIT\x00Apache-2.0", ["MIT"], id="null-byte"),
    ],
)
def test_normalize_spdx_id(raw: str, expected: list[str]) -> None:
    assert normalize_spdx_id(raw) == expected
```

Adversarial parametrize is not a substitute for fuzzing — it complements it. We rely on parametrize for regression-pinning the cases we already know about.

## Coverage gate — concrete

The merge gate is enforced in `.github/workflows/ci.yml`:

- **Unit + integration combined:** ≥ 80 % line coverage on **changed lines**.
- **E2E (Playwright):** core scenarios in `apps/frontend/tests/e2e/_core/` must all pass. New core scenarios are added with the relevant feature.

CI publishes the coverage report as a PR comment; hovering at 79.x % blocks merge until you add tests.

## See also

- [Getting started](./getting-started.md) — bring up the dev stack first.
- [Coding standards](./coding-standards.md) — the rules tests verify.
- [Agent team](./agent-team.md) — when to enlist `test-writer` and `security-reviewer`.
