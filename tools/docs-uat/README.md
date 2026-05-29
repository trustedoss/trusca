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
  diff query; `admin-guide/disk-and-health.md`: authed `GET /v1/admin/health` +
  `GET /v1/admin/disk`; `admin-guide/users-and-teams.md`: authed
  `GET /v1/admin/users` + `GET /v1/admin/teams`; `admin-guide/vulnerability-data.md`:
  authed `GET /v1/admin/trivy/health`, with the Trivy DB / oras / air-gap
  operator commands waived). Later phases add more docs to its `--doc` list.
- **`docs-uat-nightly-ui`** — schedule + manual dispatch. Same bootstrap plus
  frontend deps + Playwright, then runs the nightly-tier user-guide UI steps
  (`user-guide/components-and-licenses.md` + `dashboard.md` + `scans.md` +
  `auth-and-profile.md`, dispatched through existing PortalPage verbs).
- **`docs-uat-nightly-helm`** — schedule + manual dispatch. Lightweight (Helm 3
  + Node, no compose stack): runs `installation/helm.md`'s nightly chart-validate
  step (`helm lint` + a full `helm template` render with the minimum required
  `--set` values). Per the Phase D decision, the chart is validated statically;
  real `kind` cluster deploys were declined as too heavy / flaky. The doc's
  `helm install` / `helm upgrade` commands need a live cluster + the published
  OCI chart, so they carry `waiver=`, and the post-deploy Verify steps (kubectl)
  are `kind=manual`.

`admin-guide/backup-and-restore.md` is **enrolled** (extract-and-lint enforces
its coverage + KO parity + drift) but has no docs-uat-executed steps: every
command is a production-operator tool (`scripts/backup.sh`/`restore.sh` hardcode
`docker-compose.yml`) or host-scheduler / off-host / encrypt variant, and the
backup→restore round-trip is already executed by `install-uat.yml`. So those
fences carry `waiver=`, and the Verify steps are `kind=manual`.

`admin-guide/api-keys.md` and `admin-guide/oncall-runbook.md` are likewise
**enrolled-only** (drift + KO-parity tracking, no executed steps): api-keys'
runnable verify needs a freshly-minted key (POST `/v1/api-keys`, not yet a
docs-uat capability) and its example curl uses a placeholder host + token; the
oncall runbook is pure incident-diagnosis against the production compose stack.
Both carry `waiver=` on their command fences (api-keys' Verify steps are
`kind=manual`). `admin-guide/github-app.md` is not enrolled at all — it has no
executable fence or Verify step to guard (the GitHub App UI is roadmap).

The four `ci-integration/` docs (`github-actions` / `gitlab-ci` / `jenkins` /
`webhooks`) are **enrolled-only**. They are example CI configs / operator
snippets that assume a running portal + API key + a real CI runner, so they
cannot run deterministically in a self-contained sandbox (the `act` /
`gitlab-ci-local` dry-run path was declined for that reason). The canonical
example per doc is annotated `kind=manual` to anchor drift tracking against its
version-pinned URLs; placeholder bash/sql fences carry `waiver=`.

## Periodic full-coverage audit (manual checklist)

The sampled tiers (gate on PRs, nightly/weekly on schedule) deliberately do not
re-run *every* enrolled step on *every* trigger. Instead of an automated
week-of-year rotation ledger (considered, then declined as over-engineered now
that every doc is enrolled + lint-tracked), do a periodic **manual** full sweep
— quarterly, or before a release:

1. Bring up the dev stack and seed it the same way `docs-uat-nightly` does:
   `cp .env.example .env && docker-compose -f docker-compose.dev.yml run --rm -T backend alembic upgrade head && docker-compose -f docker-compose.dev.yml up -d` then `docker-compose -f docker-compose.dev.yml exec -T backend python -m scripts.seed_demo`.
2. Run every enrolled doc at the nightly tier and confirm all green:
   `for d in $(node -e 'const m=require("./docs-uat/manifest.json");console.log([...new Set(m.docs.map(x=>x.doc))].join("\n"))'); do node tools/docs-uat/run.mjs --tier=nightly --doc="$d"; done`
3. Spot-check that no `tier=manual` step has silently become automatable (e.g. a
   new read-only API now exists for a step that was manual because it needed a
   POST).
4. Validate the Helm chart: `helm lint charts/trustedoss && helm template trustedoss charts/trustedoss --set env.secret.secretKey=$(openssl rand -hex 32) --set postgres.auth.password=x --set ingress.host=example.com >/dev/null`.

`extract-and-lint` already guarantees coverage + KO parity + drift on every PR,
so this sweep is about catching *behavioral* regressions the sampled tiers might
miss between runs — not about coverage bookkeeping.

All are `continue-on-error: true` (non-blocking) until the manifest fidelity
stabilizes for the first public release, then they flip to blocking (design §9
decision 7).
