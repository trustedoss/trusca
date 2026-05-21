# Dogfooding Results — 2026-05-21 (real local environment)

> **Real environment pass**, run by Claude against a live local stack on the
> maintainer's Apple Silicon MacBook (Colima). Unlike the simulated dry-run
> (`2026-05-11-dogfooding-results.md`, doc-vs-code only), this pass actually
> drives the running API/DB so it can surface **S** (system/runtime) and
> **behaviour** defects the static walk could not — plus re-checks whether the
> 7 D-findings from the simulated pass still reproduce.
>
> Method: API black-box (`/auth/login` → bearer token → `/v1/...`) + cross-check
> each call against the authoritative guide (`docs-site/docs/user-guide/*`,
> `admin-guide/*`). Three iterative rounds; each round feeds (1) guide fixes and
> (2) code fixes.

---

## Environment

- **Type**: local dev stack (`docker-compose.dev.yml`)
- **Host**: MacBook, Apple M3 Pro, 18 GiB physical
- **Container runtime**: Colima 0.10.1, VM **12 GiB / 4 CPU / aarch64** (raised from 8 GiB this session for scan headroom)
- **Services**: backend / frontend / postgres 17 / redis 7 / celery-worker / celery-beat — all healthy
- **Backend base**: `http://localhost:8000` — auth at `/auth/*`, app API at `/v1/*`
- **Frontend**: `http://localhost:5173`
- **Seed**: `python -m scripts.seed_demo` → demo-org, 3 teams, 5 users, 5 projects, CVEs, notifications
- **Accounts** (all share the seed super-admin password printed at seed time):
  - `admin@demo.trustedoss.dev` (super_admin)
  - `frontend-admin@ / backend-admin@ / security-admin@demo.trustedoss.dev` (team_admin)
  - `dev@demo.trustedoss.dev` (developer)

## Method

- Drive the API as each persona would, in the guide's stated order.
- Verify every actionable claim (endpoint path, payload shape, state machine,
  UI/route reference) against the running system + the source of truth.
- Record a finding when the system disagrees with the guide (**D**), references
  something that does not exist (**S**), or the runtime misbehaves (**B**, bug).
- Category legend: **D** docs, **S** missing-system-ref, **B** runtime bug,
  **U** UX/discoverability, **C** cognitive.

---

## Findings (cumulative across rounds)

### Round 0 — environment bring-up (pre-test infra)

| # | Cat | Where | Finding | Status |
|---|-----|-------|---------|--------|
| E1 | B | `apps/backend/Dockerfile.worker:382-393` | `.NET SDK` installed via amd64-only Microsoft apt repo → `E: Unable to locate package dotnet-sdk-8.0` on arm64; worker image unbuildable on Apple Silicon. | **Fixed** — switched to official `dotnet-install.sh` (arch-aware). |
| E2 | B | `apps/backend/scripts/seed_demo.py:159-166` | `seed_demo` docstring claims "Idempotent" but fails on a **single** run: `_CVE_BANK` has 10 entries, `_COMPONENT_BANK` had 5, so `cve_idx % 5` wraps and inserts a duplicate `(scan_id, component_version_id, dependency_path)` (e.g. `./lodash` at cve_idx 0 and 5) → `UniqueViolationError`. The `--dry-run` unit smoke test never hits the DB, so this was invisible to CI. | **Fixed** — expanded `_COMPONENT_BANK` to 10 so each CVE maps to a distinct component. |

### Round 1 — developer (β) API paths + VEX state machine

All core read paths returned HTTP 200 against `portal-web`: project detail,
`/vulnerabilities`, `/components`, `/licenses`, `/obligations`, `/sbom`
(CycloneDX-JSON **and** SPDX-JSON), `/notifications`. No 5xx, no schema errors.

| # | Cat | Where | Finding | Status |
|---|-----|-------|---------|--------|
| R1-1 | D | `docs-site/docs/user-guide/vulnerabilities.md:72` | The VEX button list read as "six non-initial states" buttons, implying any verdict is reachable from any state. The real matrix (`vulnerability_service.py:148`) routes **every** terminal verdict through `analyzing`: `new → {analyzing, suppressed}` only. So β's "Mark not affected" on a fresh finding 422s (`cannot transition 'new' → 'not_affected'`, `allowed_to: [analyzing, suppressed]`). Doc also said "Only developer or higher" but moving **into** `suppressed` needs `team_admin`. | **Fixed** — rewrote the bullet as a per-state transition list (new / analyzing / terminal→reopen) and corrected the suppressed permission. Verified live: `new→analyzing` 200, `analyzing→not_affected` 200, `not_affected→fixed` 422. |
| R1-2 | B/U | `apps/backend/scripts/seed_demo.py` | `/projects/{id}/obligations` returns `{items:[],total:0}` for every demo project even though demo licenses include forbidden GPL-3.0 / conditional LGPL-2.1. `seed_demo` seeds `License`/`LicenseFinding` rows but never seeds `Obligation` rows, and obligations are a separate table keyed by `license_id` — so the Obligations tab and NOTICE-file feature are invisible in the demo. | **Round 2 code fix** — add demo `Obligation` rows for the conditional/forbidden licenses. |
| R1-3 | OK | `seed_demo.py:510-543` | `/notifications` is empty for `admin@demo` — by design: the 3 seeded notifications target the **developer** (`user_id=developer_id`). Not a bug; verify under `dev@demo` in Round 2. | Observed. |
| R1-4 | note | `apps/backend/api/v1/auth.py:65` | Auth endpoints live at `/auth/*` (no `/v1`), app API at `/v1/*`. `/v1/auth/login` 404s; `/auth/login` is correct. Intentional (auth is version-independent) but worth a one-line note in the API-facing docs. | Observed. |

### Round 2 — obligations code fix + admin (α) paths + developer notifications

| # | Cat | Where | Finding | Status |
|---|-----|-------|---------|--------|
| R1-2 | B/U | `apps/backend/scripts/seed_demo.py` | (carried from R1) demo Obligations tab + NOTICE file were always empty. | **Fixed + verified** — added `_OBLIGATION_BANK` (7 duties across MIT / Apache-2.0 / BSD-3 / LGPL-2.1 / GPL-3.0) and an idempotent `(license, kind)` seed loop. After backfilling the live catalog: `/obligations` total **0 → 7** (notice 3, copyleft 1, patent 1, source_disclosure 2); `/notice` now renders a real NOTICE file (`HTTP 200`, 1862 B, per-license obligations). `seed_demo --dry-run` still green. |
| R2-1 | U/env | `/v1/admin/disk` | `total_bytes` / `free_bytes` come back `null` on Colima (virtiofs mount exposes no statvfs totals), so the admin disk gauge would render blank. Real Linux deployments report correctly; local-mac-only. | Observed (env-specific, P3). |
| — | OK | `/v1/admin/{health,dt/status,users,teams,backup}` | All 200. DT breaker correctly `open` (fail_count 20, DT not bundled) — matches the install default. | Verified. |
| R1-3 | OK | `/v1/notifications` (as `dev@demo`) | 3 notifications (Scan completed / Forbidden license / New critical CVE), 2 unread — the seed targets the developer, confirming the empty admin inbox was by design, not a bug. | Verified. |

### Round 3 — project create + real scan (worker e2e) + CI (γ)

| # | Cat | Where | Finding | Status |
|---|-----|-------|---------|--------|
| R3-1 | D | `docs-site/docs/user-guide/projects.md:60-80` | The API create section disagreed with the live schema, which only an actual call surfaces: `team_id` POST without it → **422** (`missing: body.team_id`), yet the doc said "team_id is **not** required — the server derives it". `slug` is also required but was never mentioned, and "Only name, description, git_url are accepted" was wrong (`ProjectCreate` required = `team_id, name, slug`; optional `description, git_url, default_branch, visibility`). The β-3 "fix" (PR #73) over-corrected the doc away from the code. | **Fixed** — example now includes `team_id` + `slug`; rewrote the field list (required vs optional) and the team_id-discovery note (UI selector / `GET /v1/admin/teams`; `me/memberships` on roadmap). |
| R3-2 | design/UX | `apps/backend/tasks/scan_source.py:251-289` | A real scan of a public repo failed at **70 % (`dt_upload`)**: `DT error: ... GET /api/v1/project/lookup: Name or service not known` — the dev stack ships no Dependency-Track. The breaker is **Redis-backed and shared** (not a process-isolation bug); a fresh/half-open probe hits DT directly and the scan terminal-fails. This is the **known pre-Phase-6 design** (scan_source.py:13-14 — deferred outbox will replay OPEN-at-upload scans). **However**: cdxgen+ORT data is still persisted — the failed scan's project shows **156 components / 3 licenses cached** — so the breaker's "license/SBOM survive DT downtime" value holds, but `status=failed` makes it read as "no data". | **Not patched (design).** Logged as launch backlog: (a) Phase-6 deferred-outbox so DT-down scans finish as `succeeded (DT pending)`; (b) friendlier `error_message` than raw DNS; (c) doc note that the dev stack has no DT so scans stop at 70 %. |
| γ-1 | D | `docs-site/docs/ci-integration/github-actions.md:73` | Main `allowed_actions` taxonomy defect is fixed (line 59 now uses `/integrations` + scope model). Residual: line 73 still says project UUID comes from "**Project Settings → CI/CD**". Minor; left for a CI-doc pass. | Mostly fixed; minor residue noted. |
| — | OK | real scan worker path | cdxgen + ORT stages ran on the worker after the arm64 rebuild (156 components extracted from `is-odd`'s tree, 3 licenses classified) — the image fix (E1) is validated end-to-end up to the DT boundary. | Verified. |

---

## Re-check of the 7 simulated-pass findings

The simulated pass (2026-05-11) found 7 D-items; PR #73 ("dogfooding round 1 — 6
D-category fixes") merged most. This pass verifies whether each still reproduces
against the live system.

| Sim # | One-line | Reproduces? |
|-------|----------|-------------|
| α-1 | docker-compose.md DT OPEN/CLOSED contradiction | **No** — fixed. docker-compose.md now says the `dt` row is OPEN by default and only flips to CLOSED if `DT_API_KEY` is set. Live `/v1/admin/dt/status` returns `open`, matching the doc. |
| α-2 | users-and-teams.md "note the team UUID" | **No** — fixed. The UUID is now scoped to the scripted `POST /v1/admin/teams/{team_id}/members` path; the UI flow matches by email. |
| β-1 | scans.md fake `POST /v1/scans/source` | **No** — fixed (PR #73). scans.md now says "Neither the UI nor the API exposes a branch-override at v2.0.0; change `default_branch` in Project Settings". |
| β-2 | vulnerabilities.md drawer button list vs 7 VEX states | **No** — fixed (PR #73), and further sharpened this pass (R1-1) to show per-state transitions. |
| β-3 | projects.md dangling `team_id` lookup | **No** — fixed (PR #73). projects.md now says "team_id is not required in the create body — the server derives it". |
| γ-1 | github-actions.md fake `allowed_actions` taxonomy | **Mostly** — main defect fixed (scope model, `/integrations`); residual "Project Settings → CI/CD" for the project UUID on line 73. |
| γ-2 | github-actions.md "in-repo composite action" wording | **No change needed** — was P3/informational in the sim pass; wording is valid for an external CI repo. |

---

## Priority backlog (from this pass)

| Priority | Item | Cat | Disposition |
|----------|------|-----|-------------|
| **P0** | E1 — worker image unbuildable on arm64 (dotnet) | B | **Fixed** this pass (`dotnet-install.sh`). |
| **P0** | E2 — `seed_demo` fails on a single run (CVE/component bank mismatch) | B | **Fixed** this pass (bank → 10). |
| **P1** | R3-1 — projects.md create schema (team_id/slug required) | D | **Fixed** this pass. |
| **P1** | R1-1 — vulnerabilities.md VEX transitions are state-dependent | D | **Fixed** this pass. |
| **P1** | R1-2 — demo Obligations/NOTICE empty | B/U | **Fixed** this pass (`_OBLIGATION_BANK`). |
| **P1** | R3-2 — DT-down scans terminal-fail at 70 %; data cached but `status=failed` | design | **Backlog** — Phase-6 deferred outbox + friendlier error + dev-no-DT doc note. |
| P3 | γ-1 residue — line 73 "Project Settings → CI/CD" for project UUID | D | Backlog (CI-doc pass). |
| P3 | R2-1 — `/admin/disk` null totals on Colima | env | No action (host-specific). |

## Re-check verdict

6 of the 7 simulated-pass D-findings (α-1, α-2, β-1, β-2, β-3, γ-1-main) are
confirmed fixed and do **not** reproduce; γ-2 was always informational. The real
pass found what the static walk structurally could not: two P0 build/seed bugs
(E1, E2), a schema-vs-doc contradiction only a live 422 reveals (R3-1), and the
DT-down scan-failure behaviour (R3-2).

## Handoff

- **Code fixed + verified live this pass**: `Dockerfile.worker` (arm64 dotnet),
  `seed_demo.py` (component bank + obligations). Both re-run clean
  (`--dry-run` green; obligations 0→7; NOTICE renders; worker scans to the DT
  boundary).
- **Docs fixed**: `vulnerabilities.md` (VEX state machine), `projects.md`
  (create schema). Plus `sca-self.yml` nightly schedule disabled (ops).
- **Carried to launch backlog**: R3-2 (DT-down scan UX / Phase-6 outbox),
  γ-1 line-73 residue.
- **Next**: bundle the above into one PR (user merges). Demo SaaS bundle 2
  (multi-tenant) is unblocked once this lands.

