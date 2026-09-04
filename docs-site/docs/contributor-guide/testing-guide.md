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

The other surprise is `git diff --name-only origin/main`, which is how most
people check a rebase for stray files. It answers "what differs", and that
mixes your change with everything `main` gained since you branched, so a file
somebody else merged reads as one you deleted. Compare against the merge base,
or rebase first and then look. When an unfamiliar name does appear,
`git log HEAD..origin/main` says whether `main` moved or you did. Getting this
wrong is not only confusing: unfamiliar names on every check teach you to skim
the list, and the real accident is in that list.

If you need a placeholder migration so `alembic upgrade head` resolves while
the revision before yours is still on someone else's branch, put `_local_stub`
in its filename and add that exact path to `.git/info/exclude`. Not a glob.
That file lives in the common git directory and applies to every worktree, so
`0081_*.py` would hide a colleague's real `0081_finding_assignment.py` from
their `git status` and let them open a PR without its migration. Delete the
stub the moment the real revision lands on `main`, before you rebase: two
files declaring the same `revision` string stop alembic outright. Delete its
line from `.git/info/exclude` at the same time. A stale line there is not
harmless: it is shared, it names a path nobody can see any more, and the next
person has to work out what it was protecting.

### A database a placeholder touched is spent

Applying a placeholder migration stamps its revision as done without running
any of its DDL. When the real revision later lands, alembic sees the id in
`alembic_version` and skips it, and no amount of `alembic upgrade head`
recovers: the database reports the newest revision while missing the columns
that revision added. Nothing looks wrong until one test fails on a missing
column, which reads exactly like a defect in the code you are holding. That
happened here, and the wrong hunt had already started before the schema was
compared against the recorded revision.

So keep one throwaway database for work that needs a placeholder and drop it
afterwards. The stamping cannot be avoided, since resolving the chain is the
whole point of the stub; reuse can.

### Check for absence with a query you have seen return something

A query that finds nothing and a query that asks the wrong question give the
same answer. Confirming the poisoning above, a check queried
`findings.assignee_user_id`; the table is `vulnerability_findings`, so every
database answered zero. The databases really were poisoned, so the conclusion
survived and there was nothing to notice, which is what makes this one hard
to catch: the answer was right and the reasoning was empty.

Point the same query at something you know exists first. Without that
positive control, a zero means nothing at all.
### Say in the assertion what it is protecting

A red test means one of two things: you broke something the test was put there
to hold, or the test describes a world that no longer exists. They look
identical, and only one of them is fixed by making the test pass. Wrapping it
in whatever it now needs is always the shorter path, so the ambiguity resolves
toward erasing the guard.

The way out is that the assertion says what it is for. A body-content test
went red across twenty-one cases with "No QueryClient set"; the obvious move
was to wrap it in a provider. Its own docstring said the body is deliberately
surface-agnostic and that a hard dependency on the drawer would surface here
first. That sentence turned a mechanical fix into a design change: the editor
became a slot the surface fills.

Write the reason where the failure will be read, not in a commit message.

### A screen that is half right is harder than one that is all wrong

Nothing fails, no error appears, and part of the interface updates correctly.
A wrong cache-invalidation key does this: the mutation succeeds, the drawer
refreshes from its own write, and only the table stays stale. Attention goes
to the half that looks wrong, which is the table, while the cause sits in the
half that looks right.

When only part of a view is stale, suspect what connects them before
suspecting the stale part. The correct half is not evidence that its path is
sound; often it is correct for a reason that bypasses the broken step
entirely.

### A neighbour that passes is not permission {#neighbour-passing}

Writing something the way the file next door writes it, and then finding that
your version fails a check its version passes, is a normal afternoon here. The
instinct is to conclude that you got the detail wrong. Sometimes you did.
Often the neighbour passes for a reason that does not extend to you, and there
are at least three:

- **It is a recorded exception.** `token:lint` and its siblings fail only on
  newly added violations and carry a budget of existing ones, so the
  `text-slate-600` in every health panel is debt somebody wrote down, not the
  convention. A new panel written the same way fails while its neighbours
  pass.
- **It was justified in that spot and is not in yours.** A `nosemgrep` exists
  in three places in this repository, and one directory holds twenty-three
  cases of the same shape written without it. Adding a fourth suppression
  there would have taught the next reader that the rule is optional.
- **The check cannot see it.** An i18n key in a neighbouring component passed
  extraction while an identical-looking one failed, because the neighbour's
  key is a template literal: the static analyser cannot read it and files it
  as dynamic. Its position was never validated at all.

The three look identical from outside, which is the point of listing them: a
reader who knows only about baselines will check the baseline, find nothing,
and conclude the neighbour is the convention. Establish which one you are
looking at before copying.

A fourth case is not about the neighbour passing at all. What you take from it
can be right and wrong at once, and taking it as one piece hides the seam. The
assignment carry-forward tests needed two fixtures from the file next door, a
session and a database gate; importing a fixture that a test also names as a
parameter is a redefinition, so both were written locally instead. The session
fixture had no shared version and the gate did, and copying them together
reproduced the private gate that ER66 exists to remove. Nothing pointed at the
gate, because it arrived attached to something that was fine. When you bring
over more than one thing, ask of each separately whether it belongs to you.

### Running locally: give each run its own Postgres and its own Redis

The suite talks to a real Postgres and a real Redis, and neither is isolated
for you. Two runs pointed at the same database or the same Redis index will
interfere, and the two interfere very differently.

Sharing a **database** announces itself: alembic refuses, or a test reads a
row it did not write. That is why "a database per branch" became the habit
here. Give the run its own:

```bash
createdb trusca_mybranch          # or docker exec ... psql -c 'CREATE DATABASE ...'
export DATABASE_URL=postgresql+asyncpg://trustedoss:trustedoss@localhost:5432/trusca_mybranch
```

Sharing a **Redis index** is silent, which is why no habit grew around it.
Celery's broker lives there, so a worker started by the other run will happily
consume a message this one published. The task never arrives, the test waits
out its timeout, and the failure looks exactly like a defect in the code you
are holding. Give the run an index nobody else is on:

```bash
export REDIS_URL=redis://localhost:6379/7   # any index that is empty
```

`conftest.py` checks this at session start and stops the run if the index
already holds keys, printing the count and a sample. It does not clear
anything: on a shared index that would break whoever else is mid-run. If the
keys are your own leftovers, clear them; if they are not, move.

CI needs none of this. Each job gets its own Postgres and Redis container, so
the fixed URLs in the workflow are already private to that job.

### When the database is missing, the venue decides {#db-required}

Tests that need a schema call `tests._db_required`, which separates two
situations that used to be treated alike.

**No database** is an environment fact. On a laptop with no Postgres running,
or pointed at a database that does not exist, those tests skip - that is what
makes the suite runnable without setting anything up. `TRUSCA_TESTS_REQUIRE_DB`
governs this one: CI sets it, because there a database is promised and its
absence is a fault.

**A database that answers but will not migrate** is a fault, and it is the same
fault on CI and on your laptop. That fails everywhere, flag or no flag.
Skipping it locally would mean whoever broke a migration hears about it from CI
instead of from the run they just did.

Which of the two you are in is decided by opening a connection, not by reading
the migration's error text: driver wording changes between versions and is not
something to match on.

The reason is worth knowing, because the old behaviour looked harmless. Every
module skipped when `alembic upgrade head` failed, so a pull request that broke
a migration made all the tests that needed the schema skip, and the job that
exists to catch a broken migration exited 0. Measured on a probe branch with a
deliberately broken head migration: the integration leg skipped 1824 tests and
the unit leg 989, and what turned either leg red was a handful of modules that
had no gate at all - not the gates working.

Two details of the helper follow from the same measurement:

- **The migration runs once per process and the outcome is remembered, failure
  included.** Each module used to run its own, so when one broke, the first
  module reported the real error and every later one reported
  `relation "..." already exists` from re-running a chain that had stopped
  partway. Retrying cannot succeed, and it replaces the true diagnosis with a
  false one.
- **A module that uses the database must gate on it.** One that opens a session
  and migrates nothing passes only while some earlier module happens to have
  built the schema, and fails on its own. `test_database_gates_are_shared.py`
  fails if any module does this, and fails if a new module writes its own copy
  of the gate instead of calling the helper.

To see what CI sees, set the flag:

```bash
TRUSCA_TESTS_REQUIRE_DB=1 pytest tests/integration
```

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

`tools/mutate/mutate.py` is one safe way to do that, and is not required. It
edits one file and refuses when the anchor is missing, when it is ambiguous,
or when the replacement leaves the file unchanged, exiting non-zero so a `&&`
chain stops before the tests run. The last refusal is the one worth having: an
anchor can match while the edit changes nothing, and the green run that follows
reads as evidence the assertion is weak. `--restore` puts the file back from a
backup rather than through `git checkout`, which would also discard
uncommitted work in that file.

The ways an assertion stops being able to fail repeat, and six have shown up
often enough to name:

- **The manipulation never reached the target**, so nothing changed and the
  test passed on an unchanged system. See rule 8.
- **The code that writes and the code that reads are the same**, so any
  implementation agrees with itself.
- **A tool found nothing**, and the result alone cannot say whether there was
  nothing to find or whether the tool was not looking there. A repository lint
  run from the wrong tree reports zero findings for a file it never opened.
  *The pattern only finds the notation you imagined.* `gh` and
  `EXPLAIN` return JSON, and a name like `lint (backend)` or a plan node
  nested under `SubPlan` does not match a pattern built without expecting
  parentheses or nesting, so parentheses, brackets and dots inside a value
  mean something else to a regex than they do to you. And counting things in
  source has the same problem: `grep` found three of the five five-element
  tuples in a test file, the two it missed written in shapes the pattern did
  not anticipate, one of them inside a second helper. A pattern finds the
  notation you imagined; a parse finds what is there. Parse the structure or
  compare exact strings; this repository already asserts over ASTs in several
  places, so it is not a new tool to reach for.

  *A parse can be read as text again.* A census searched `ast.dump()` output
  for `"alembic"` in double quotes and reported zero, since dump writes
  apostrophes. Read the nodes.

  *What you erase in order to compare is what counts as the same.* That
  decision, not the comparison after it, produces the answer. Erasing string
  literals to compare fixtures merged eight unrelated ones, the strings being
  the payload; erasing decorators would have merged different lifetimes.
  *A wider definition needs a fresh sample.* Counting a second way of
  reaching the database swept in eighty-three modules that never open a
  connection, so re-read what a census catches after every change to what it
  counts. That one was wrong six times, five of them right after it grew.

  *A specific wrong answer is more dangerous than an empty one.* Nobody trusts a
  tool that returns nothing, but "13 of 17 checks never ran" carries a count
  and a list, and the detail is what makes it credible: it looks like
  arithmetic, and it is arithmetic on a broken premise. Counting is the common
  way to arrive at one, because a count discards the relationships it counted
  over: rendering a chart with a certificate mounted produced four mount
  paths, which was the expected number, while one of the four had no volume
  behind it and would have been refused by Kubernetes. When a new check
  produces a plausible answer, feed it one input you know it should match and
  confirm it does. If the answer is a number, check what the number is
  attached to.

  *The counterpart: a tool that says what it does not do.* `tools/static-checks`
  names the CI steps it deliberately omits and why, because a local runner that
  silently skips a check and reports success is worse than one admitting the
  gap. Its contract test against the workflow is what caught a later change
  adding a CI step without registering it - not review.

  *Testing the checker has its own version of this.* The input you plant has to
  be something the checker declares it catches, or "it did not fire" means
  both "the checker is not running" and "that was never a violation". A
  clumsy Korean sentence planted to prove a style lint was reading a new file
  went uncaught because no rule in its catalogue covered that shape; a real
  rule from the catalogue was caught immediately.
- **A rule was induced from a single example**, so that example's accidents are
  inside it. An exemption list with one entry asserted that exempt modules have
  no database gate, true of that one by coincidence; the next three keep their
  gates deliberately and it failed for all three. A second instance is what
  separates the essential from the incidental, in a rule as much as in data.
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

The mutation is subject to everything on the list above, including the string
matching it is often testing. Checking that a documentation contract noticed a
variable disappearing from a page, the "removal" renamed `GIT_SSL_CAPATH` to
`GIT_SSL_CAPATH_REMOVED_FROM_DOCS`, which still contains the original as a
substring, so the containment check passed and read as a weak contract. Assert
the state the mutation was supposed to produce, not that the text changed:
here, that the old name is absent.

Did it land is worth asking before a bulk change too. Declare how many files an
edit must touch and stop with nothing written on a mismatch, because a pattern
matching nothing reports "nothing to do", which reads as "not applicable". Over
193 modules that stopped three runs, all three the operator's arithmetic rather
than the tool - twice `wc -l` on a list with no trailing newline, which counts
newlines and is short by one - and never the transformation, which says which
input was weaker. Count independently of the tool, or the two always agree and
nothing is checked, and hold the same bar whatever the size: one file edited by
hand is not safer than a hundred edited by a transformation already run against
the whole tree.

A declared number can also disagree with you. A prediction that a batch would
shrink by one came back unchanged, the model of which list the file belonged to
being wrong; unwritten, the correct result would have read as confirmation. And
two identical failures mean the thing being changed is not the variable: three
repairs to a docstring each moved the insertion point and each failed the same
way, because the inserted fragment carried a stray terminator.

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

A raw colour class you can see in `apps/frontend/src` is not evidence that
raw colour classes are allowed: it may be a baseline entry. Check the
baseline before following a neighbour. See
[A neighbour that passes is not permission](#neighbour-passing).

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
