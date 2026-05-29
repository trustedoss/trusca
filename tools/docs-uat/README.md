# docs-uat — the docs *are* the tests

docs-uat is the procedural-correctness lane for our documentation: it proves
that **following a doc step-by-step actually works**. The source of truth is
the doc itself — you annotate the existing markdown with hidden
`<!-- docs-uat: ... -->` comments, and the tooling extracts those into an
execution manifest, runs the sampled tiers, and fails CI when the doc drifts
from reality.

> Design rationale + roadmap: `docs/docs-verify-investigation-2026-05-29.md`.
> Phase A (this slice) covers **Quickstart** end-to-end. Phases B–D enroll
> install / admin / user-guide / CI docs.

## How it fits together

```
docs-site/docs/**.md  ──extract.mjs──▶  docs-uat/manifest.json
   (annotated)                              │
                                            ├─ extract.mjs --lint   (coverage / schema / KO parity)
                                            └─ run.mjs --tier --doc (dispatch shell/api/sql/ui)
                                                                         └─ ui → Playwright
                                                                              (playwright.docs-uat.config.ts)
```

- **`extract.mjs`** — parses annotations, writes the manifest, and (`--lint`)
  enforces id uniqueness, per-kind schema, coverage, and KO structure parity.
- **`run.mjs`** — rebuilds the manifest fresh, filters to one `--doc`/`--tier`,
  and dispatches each step. ui steps are handed to Playwright in one batch.
- **`apps/frontend/playwright.docs-uat.config.ts` + `tests/docs-uat/docs-uat.spec.ts`**
  — a generic spec that maps each `harness=verb(args)` annotation onto the
  **existing** PortalPage / AuthHarness verbs (we reuse verbs, never reimplement).

The manifest (`docs-uat/manifest.json`) is a build artifact — it is
gitignored and regenerated on every run, so it can never drift from the docs.

## Annotation grammar

A docs-uat annotation is an HTML comment placed immediately before the thing
it tests. `.md` files are CommonMark in Docusaurus 3, so the comment is
invisible to readers.

**Before a fenced code block** (`shell` / `sql` / `api` http blocks):

```markdown
<!-- docs-uat: id=qs-up kind=shell ctx=host expect=exit:0 tier=gate -->
​```bash
docker-compose -f docker-compose.dev.yml up -d
​```
```

**Before a prose line** (a `## Verify it worked` step, a UI claim, or a
testable assertion like "services are healthy"):

```markdown
<!-- docs-uat: id=qs-login kind=ui harness=login(admin@demo.trustedoss.dev,DemoTest2026!) tier=gate -->
Open **http://localhost:5173** and sign in:
```

### Fields

Tokens are whitespace-separated `key=value`; **values carry no spaces**.

| field | required | values | meaning |
|---|---|---|---|
| `id` | ✅ | kebab-case, globally unique | stable identifier (report + rotation key) |
| `kind` | ✅ | `shell` `api` `ui` `sql` `lint` `manual` | dispatch target |
| `tier` | ✅ | `gate` `nightly` `weekly` `manual` | sampling tier (gate = PR, runs in < 10 min) |
| `ctx` | shell/sql | `host` `backend` `worker` `postgres` `kind` | where it runs |
| `expect` | per kind | `exit:N` · `status:N` · `match:/re/` · `rows:>N`/`rows:>=N`/`rows:N` · `ok` (sql: query runs cleanly) | assertion |
| `url` | api | `/path` or absolute | endpoint hit by the api kind (the doc may show a `${VAR}` host placeholder; the runner uses `DOCS_UAT_API_BASE`) |
| `auth` | api | `admin` | inject a super-admin bearer (one cached login via `DOCS_UAT_ADMIN_EMAIL`/`_PASSWORD`, default demo super-admin) |
| `retry` | optional | `NxMs` e.g. `40x6s` | attempts × interval — api polling, or a shell step racing a warming-up service (e.g. `alembic upgrade head` right after `up`) |
| `harness` | ui | `verb` or `verb(arg1,arg2)` | maps to a registered PortalPage/AuthHarness verb |
| `fixture` | optional | e.g. `seed_demo` | documents the pre-state the step assumes |
| `waiver` | optional | `<reason>` (no spaces) | explicitly excludes the block from execution — keeps it counted, never silently dropped |

### Coverage rule

In an **enrolled** doc (one with ≥1 annotation), every `bash`/`sh`/`http`/`sql`
fence **and** every `## Verify it worked` step must be annotated or carry a
`waiver=`. Anything uncovered fails `extract.mjs --lint`. Un-enrolled docs are
ignored until a later Phase annotates them.

### `ctx=host` execution convention

The doc command is sometimes not literally runnable in CI ("the doc command ≠
the CI exec context"). `run.mjs` applies exactly two adaptations for
`ctx=host` shell steps, and nothing else:

1. The command runs from the **repo root** (so the doc's `docker-compose -f
   docker-compose.dev.yml ...` resolves).
2. `docker-compose ... exec` gets `-T` injected (no TTY on CI runners).

When a command genuinely can't run as written — e.g. Quickstart's `git clone`
of this very repo while CI is already inside the checked-out tree — mark it
`waiver=<reason>` rather than rewriting the doc into something a human
wouldn't type. (Quickstart uses `waiver=ci-uses-checkout-tree`.)

## Running it locally

You need the dev stack reachable (`docker-compose -f docker-compose.dev.yml up`
+ `seed_demo`) for the gate, or just lint the annotations statically:

```bash
# Static — manifest + coverage / schema / KO parity lint (no stack needed)
node tools/docs-uat/extract.mjs --lint

# Plan a run without executing (prints the ordered step list)
node tools/docs-uat/run.mjs --tier=gate --doc=quickstart.md --dry-run

# Full gate against a running dev stack (brings it up / seeds / drives UI / tears down)
node tools/docs-uat/run.mjs --tier=gate --doc=quickstart.md
```

ui steps shell out to the frontend's Playwright; install it once with
`cd apps/frontend && npm ci && npx playwright install --with-deps chromium`.

## Adding a new ui verb

The spec only *binds* annotations to verbs — it adds no assertion logic. To
teach docs-uat a new verb, register it in `VERBS` in
`apps/frontend/tests/docs-uat/docs-uat.spec.ts`. For a brand-new screen, add
the verb to `PortalPage` first (harness-first rule), then register the binding.

## CI

`.github/workflows/docs-uat.yml`:

- **`extract-and-lint`** — static, fast, runs on every PR touching docs or
  the tooling. Catches drift (uncovered blocks, broken KO parity).
- **`quickstart-gate`** — brings the dev stack up via the documented commands,
  runs the gate-tier steps end-to-end. (PR + dispatch.)
- **`docs-uat-nightly`** — schedule + manual dispatch (not on PRs). Bootstraps
  the dev stack, seeds the demo data, and runs the nightly-tier admin-guide
  api/sql assertions (`admin-guide/audit-log.md`: authed audit API + a jsonb
  diff query). Later phases add more docs to its `--doc` list.
- **`docs-uat-nightly-ui`** — schedule + manual dispatch. Same bootstrap plus
  frontend deps + Playwright, then runs the nightly-tier user-guide UI steps
  (`user-guide/components-and-licenses.md`: the seeded project's components +
  forbidden-license assertions, dispatched through existing PortalPage verbs).

`admin-guide/backup-and-restore.md` is **enrolled** (extract-and-lint enforces
its coverage + KO parity + drift) but has no docs-uat-executed steps: every
command is a production-operator tool (`scripts/backup.sh`/`restore.sh` hardcode
`docker-compose.yml`) or host-scheduler / off-host / encrypt variant, and the
backup→restore round-trip is already executed by `install-uat.yml`. So those
fences carry `waiver=`, and the Verify steps are `kind=manual`.

All are `continue-on-error: true` (non-blocking) until the manifest fidelity
stabilizes for the first public release, then they flip to blocking (design §9
decision 7).
