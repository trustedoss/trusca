---
id: testing-guide
title: Testing guide
description: pytest layout, the Playwright PortalPage harness, adversarial-input parametrize, and the 80% coverage merge gate.
sidebar_label: Testing guide
sidebar_position: 3
---

# Testing guide

Tests are first-class. The PR merge gate is **≥ 80 % line coverage on changed code**; the Playwright E2E suite does not run on pull requests (see [Coverage gate](#coverage-gate) below). This page walks the layout, the harness pattern, and the adversarial-input rules that catch the bugs static analysis cannot.

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

### Working in a git worktree

Two things surprise people here.

Repository tooling under `tools/` resolves the repository root differently
depending on the tool. `tools/em-dash/lint.mjs` asks git, so it follows where
you run it. `tools/ko-style/lint.mjs` derives the root from its own location,
so calling the main checkout's copy by absolute path checks the main tree even
from inside a worktree and even when you pass it worktree paths. Run the
worktree's own copy. To confirm a lint is reading your files, put a violation
in one of them and check it is caught at the right line, then remove it: a
file count only differs when your branch adds a file.

If you need a placeholder migration so `alembic upgrade head` resolves while
the revision before yours is still on someone else's branch, put `_local_stub`
in its filename and add that exact path to `.git/info/exclude`. Not a glob.
That file lives in the common git directory and applies to every worktree, so
`0081_*.py` would hide a colleague's real `0081_finding_assignment.py` from
their `git status` and let them open a PR without its migration. Delete the
stub the moment the real revision lands on `main`, before you rebase: two
files declaring the same `revision` string stop alembic outright.

### Coverage

```bash
pytest --cov=. --cov-report=term-missing --cov-report=xml
```

Aim for ≥ 80 % line coverage on **changed lines**. CI runs the two suites as separate jobs (`test (backend-unit)`, `test (backend-integration)`), and `coverage-gate (backend)` combines their data and judges both numbers: the whole tree against `fail_under = 80`, and the lines your branch changed against the same 80 % via `diff-cover`. Either one under threshold fails the job.

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

## Hardening rules — what the 2026-06 validation campaign taught us

An external verification team executed 1,360 guide-derived cases against the
live portal and surfaced 70 unique defects that our unit / functional / e2e
suites — all green — had missed. The post-mortem traced them to a handful of
structural blind spots; each rule below closes one and names the defect class
that proved it. These rules are binding for new PRs (they mirror CLAUDE.md §2).
Rules 6 to 8 came later, from the 2026-09 adoption-readiness review.

### 1. Security assertions are permission × state matrices

We had an "other team → 404" test and a "terminal → 409" test — but never
their cross product, and a real leak lived exactly at that intersection (a
non-member probing another team's *finished* scan got a 409 that confirmed it
existed). The permission denial (404 existence-hide / 403) must always fire
before any state-derived 409. New 409 surfaces add a case to
`apps/backend/tests/integration/test_existence_hide_state_matrix.py`.

The rule has a second half at the route level. `tests/contracts/permission-matrix.json`
declares the gate every route carries, and
`apps/backend/tests/unit/test_permission_baseline.py` asserts it in both
directions: a route with no row fails (a surface shipped without anyone
classifying it), and a row with no route fails (the matrix went stale and
stopped being an oracle). Adding a route means adding its row. Changing a gate
means changing the fixture, which is what puts the change in front of a
reviewer instead of leaving it spread across the API modules. Assert both
allow and deny for each role: a denial-only test stays green when a gate is
widened, and an unknown role is denied everywhere, so a missing role reads as
"secure" while the product is broken.

### 2. Duplicated vocabularies require a contract test

When the same closed vocabulary lives in two places — a DB enum and a
dispatcher catalog, an emitter and an advertised list, a backend enum and a
frontend mirror constant — per-module tests stay green while the pair drifts
(the notification-kind drift sat dormant until the approval trigger was
wired). Import both sides and assert set equality:
`apps/backend/tests/unit/test_catalog_contracts.py` is the pattern.

### 3. Persistence-boundary tests use recorded real tool output

Hand-built minimal fixtures are too clean. A real container image carries
several CVEs per package as the *norm*, and the container-scan persist bug
lived exactly in that density — our one-CVE-per-package fixtures could never
reach it. Record real tool output (`tests/fixtures/trivy/`) and derive
expected counts from the fixture so re-recording never breaks assertions.

### 4. The docs are an oracle

34 of the 70 findings were guide–implementation mismatches — invisible to
code-derived tests by construction, because the code is self-consistently
wrong. Every documented promise (a status code, a CLI command, a config key)
gets a docs-uat assertion or a guard test as part of the feature's DoD.

Config keys have that guard already:
`apps/backend/tests/unit/test_config_key_contract.py` holds the keys the code
reads, `.env.example`, and the reference page to one list. A new key needs an
entry in the template or the page, and a key that stops being read has to lose
its entry, so an operator is never offered a setting that does nothing. Two
shapes are invisible to the check and are declared in that file instead: a key
assembled at runtime, and a key forwarded to a child process rather than read.

### 5. Lifecycle sequences are a test category

Single-operation tests passed while revoke → re-register was a permanent 409
(the unique constraint counted revoked rows). Create → revoke → re-create,
archive → restore → use: test the sequence, not just each verb.

### 6. Code that writes owns a test that runs it

Two maintenance tasks reported success while writing nothing. They mutated
rows in memory, put the mutated count in their summary, and never called
`session.commit()`, so `sync_session_scope` closed and dropped the work (the
helper does not auto-commit, and its docstring says so). Neither task had a
test that executed it, and a test that only imports a task, or asserts on a
helper the task calls, cannot see this.

This rule first said "background task", and the narrower wording let the same
defect through again somewhere else. The user-anonymisation service flushed
and never committed, and `get_db` does not commit either, so all four of its
routes answered with a populated object and persisted nothing: opening a
request returned 201 with a real id for a row that was gone a moment later,
the two-person approval flow was inert end to end, the operator command
always refused for want of an approved request, and the admin panel built to
show overdue erasures reported "nothing outstanding" permanently. Every test
it had called the service functions directly and committed the session
itself, so every one of them passed.

The defect does not live in tasks. It lives in a write that is never
committed, and a route, a service, a script or a task can all host one. Any
code that writes gets at least one test that drives it the way production
does and then asks again, in a separate read, whether the write survived.

### 7. Break what a new assertion guards, and watch it fail

A passing test does not tell you what it holds. Five assertions in the
adoption-readiness review held nothing: a limit check that passed because the
same number appeared in the docs with a different meaning; a call check that
passed on the name appearing in a comment; a blocking check that passed
because deleting the block still failed the scan for a different reason; a
naming check that passed with the email still inside the name; an
invalidation check that only ever read the last token a loop captured. Watch
for containment checks, for assertions that ask whether something failed but
not why, and for a value captured in a loop. Write the assertion, then break
the thing it guards and confirm the test goes red.

The ways an assertion stops being able to fail repeat, and six have shown up
often enough to name:

- **The manipulation never reached the target**, so nothing changed and the
  test passed on an unchanged system. See rule 8.
- **The code that writes and the code that reads are the same**, so any
  implementation agrees with itself.
- **A tool found nothing**, and the result alone cannot say whether there was
  nothing to find or whether the tool was not looking there. A repository lint
  run from the wrong tree reports zero findings for a file it never opened.
- **A fixture never populated the field**, so a guard over that field is blind
  and reports success for every input.
- **Isolation removed a condition rather than removing noise.** Running one
  file alone, using a minimal fixture, mocking a dependency and testing a
  function directly all change the conditions instead of clearing them. A
  celery registration check failed when its file ran alone and passed in the
  full suite, and the single-file failure was read as the clean result; the
  imports performed by other test modules were part of what the assertion
  depended on. Before drawing a conclusion from an isolated run, name what
  the isolation took away.
- **A comparison was read past what it can answer.** Running the same test on
  your branch and on `main` says whether your change caused the result and
  nothing about what did: whatever both sides share, the invocation included,
  is invisible to it. The same celery check failed identically on both
  branches, which established "not mine" and was then read as "not the way I
  ran it" as well. Any A/B has this limit, whether the pair is two branches,
  two environments or two configurations.

### 8. When a mutation survives for no clear reason, check it landed, then look for a second layer

First confirm the mutation actually took effect. A mutation that never
applied hands you a green run as evidence, and the conclusion you draw from
it is the opposite of the truth. Two cases in the adoption-readiness review:
an added compose block that became a duplicate key the parser resolved to
the other one, and an UPDATE that changed nothing because the target row
already held the value being written. Both read as "the guard does not
catch this" when the guard was fine.

Two layers that can produce the same outcome hide each other: delete either
and the result is unchanged, so neither is verified. The reverse also
happens, where an outer check keeps the inner one unreachable, so what looks
like two defences is one and relaxing the outer silently removes the inner.
Three cases in the adoption-readiness review: a checksum verification the
manifest pre-check kept unreachable; a sign-in throttle where the router's
early rejection and the counter's guard covered for each other; an audit
trigger the GRANT layer had kept from ever being exercised. Assert separately
that each layer is reached and that it works alone.

### Two regression nets, on purpose

`tests/verify-specs/` vendors the verification team's deterministic spec
modules (see its `PROVENANCE.md`) and runs them nightly
(`verify-specs-nightly.yml`) against a freshly seeded stack. That nightly is
our internal regression net — it does **not** replace the verification team's
independent Tier-3 re-verification, whose value is precisely that the oracle
is not ours.

## Design gates — colour and pixels

Two rules that used to live only in review comments are now enforced.

**Design tokens.** `npm run token:lint` fails on a raw hex or a Tailwind
palette class (`bg-amber-50`, `text-emerald-700`) anywhere under
`apps/frontend/src/`. Use a token: the shadcn semantic set, `risk-*` for
finding severity, or `status-*` for entity and operation state — see the
[design system reference](../reference/design-system.md).

Pre-existing debt is frozen per file in `scripts/token-lint-baseline.json`
and the gate is a ratchet: new bypasses fail, files that grow fail, and
files that shrink also fail, asking for the lowered baseline to be
committed. That last direction is the point — a budget you paid down but
did not record is a budget someone else can spend.

A consequence worth stating, because it catches people copying from the file
next door: a raw colour class you can see in `apps/frontend/src` is not
evidence that raw colour classes are allowed. It may be an entry in the
baseline. The health panels all write `text-slate-600` for a muted badge, and
a new panel written the same way fails the gate while its neighbours pass.
Check the baseline before following a neighbour; reading it costs less than a
round trip through CI.

```bash
npm run token:lint          # check
npm run token:lint:update   # after paying debt down, commit the result
```

**Visual baselines.** `ui-gates.yml` runs on every PR touching the
frontend and blocks on pixel drift. Which screens it guards is decided in
`tests/visual/coverage-manifest.ts`, where every screen the router mounts is
either represented (with a baseline) or exempt (with a reason);
`visualCoverage.test.ts` fails if a screen is missing from that register.
The set is intentionally one-per-layout-template rather than one-per-route —
each baseline is a maintenance liability, and a wall of flaky diffs teaches
reviewers to skim past red.

After an intentional UI change, refresh the baselines from CI rather than
locally — macOS font hinting diverges from the linux runner by 5–20 % on
text-heavy frames:

```bash
gh workflow run ui-gates.yml --ref <branch> -f update_baselines=true
# then download the `visual-baselines` artifact and commit the PNGs
```

There is deliberately no skip label. Anything volatile enough to need one
(relative timestamps, the dev-server devtools launcher) is masked or hidden
in the spec instead.

**Accessibility.** The same workflow runs axe-core (WCAG 2.1 A/AA) over the
same screens. `color-contrast` is the reason it needs a real browser: axe
cannot evaluate it in jsdom, which is why the older `badgeContrast.test.tsx`
computes ratios by hand for one component.

It is a ratchet like the token lint, not a demand for zero — the app had
never been scanned, and a gate that cannot be satisfied is one that gets
switched off. Violations are frozen per screen and rule in
`tests/a11y/a11y-baseline.json`, and the numbers only go down. Every run
publishes what it observed (counts plus the offending selectors) as the
`a11y-observed` artifact, so a failure tells you where to look instead of
leaving you to re-derive it.

Refresh it the same way as the visual baselines — the `update_baselines`
dispatch covers both, since pixels and rule counts drift for the same
reasons.

Both gates walk one screen register (`tests/_harness/screenIds.ts`), so
they cannot disagree about what is covered.

## Coverage gate, concretely {#coverage-gate}

The merge gate is enforced in `.github/workflows/ci.yml`:

- **Unit + integration combined:** ≥ 80 % line coverage overall, and ≥ 80 % on the **lines the pull request changed**. The two suites run as separate jobs; `coverage-gate (backend)` combines them before judging, because neither leg's number means anything alone.
- **E2E (Playwright):** NOT part of the pull-request gate. The suite runs on the nightly schedule and on a manual `workflow_dispatch`, so opening a PR does not exercise it. Core scenarios live in `apps/frontend/tests/e2e/_core/` and are added with the relevant feature; if your change touches a user-visible flow, run them yourself or ask a maintainer to dispatch the workflow.
- **Design tokens:** `token:lint` ratchet, above.
- **Visual + accessibility:** `ui-gates.yml`, above.

The combined `coverage.xml` is attached to the run as the `backend-coverage` artifact, and the `coverage-gate` log lists the missing lines. Reproduce a diff-coverage failure locally with `diff-cover coverage.xml --compare-branch=origin/main` from `apps/backend`.

## See also

- [Getting started](./getting-started.md) — bring up the dev stack first.
- [Coding standards](./coding-standards.md) — the rules tests verify.
