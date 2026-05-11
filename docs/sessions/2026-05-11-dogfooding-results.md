# Dogfooding Results — 2026-05-11

> **⚠️ Simulated dry-run, not human-timed.**
>
> This pass is the **Option 2** outcome from the session plan
> `2026-05-11-dogfooding-first-30min.md`: a code+doc walk performed by
> Claude against the v2.0.0 repo, **not** an actual install on a fresh
> droplet. Wall-clock numbers are intentionally **omitted** — the value of
> wall-clock data is that a human pauses, mis-reads, or scrolls; that
> signal is unreachable from a programmatic walk.
>
> What this pass **can** find:
> - **D (docs)**: doc text that contradicts the code or itself.
> - **S (system)**: APIs/files the doc references that do not exist.
>
> What this pass **cannot** find (deferred to human dogfooding, Option 1):
> - **P (prerequisite)**: DNS / firewall / TLS / sudo / network surprises.
> - **U (UI)**: discoverability of buttons, empty-state ergonomics,
>   one-time-reveal warnings, browser TLS-lock impressions.
> - **C (cognitive)**: operator-vs-developer persona confusions, where
>   the doc *says* the right thing but a non-expert misreads it.
>
> Each entry below cites the file + line and (when relevant) the
> contradicting authoritative source (git tag, route, schema, or other
> doc paragraph). PRs that fix the D entries are tracked in the
> "Priority backlog" table at the bottom.

---

## Method

For each of the three tasks (α admin, β developer, γ CI integration):

1. Read every doc the persona would land on, in the order they would
   land on it.
2. For every actionable line (a command, an endpoint, a UI claim, a
   referenced env var), verify it against the source of truth:
   - `scripts/install.sh` / `apps/backend/scripts/create_super_admin.py`
     for installer claims.
   - `apps/backend/api/v1/` route registration for endpoint paths.
   - `apps/backend/schemas/` Pydantic models for payload shapes.
   - `apps/backend/models/` SQLAlchemy enums for state machines.
   - `actions/scan/action.yml` for the composite action.
   - `git tag` for version references.
3. Record a finding only when the doc and the source disagree (D), or
   when the doc references something that does not exist (S).

No friction is recorded for items where the doc is correct — even if the
sequence could have been smoother.

---

## Task α — First Admin

**Persona path (per session plan, lines 31–38):**

```
0  install.sh → 5  first login → 10 create team
15 add teammate → 20 /admin/dt CLOSED → 30 manual backup
```

**Findings:**

### α-1 — `/admin/dt` checklist contradicts Step 3 of the same doc — **D, P0**

- File: `docs-site/docs/installation/docker-compose.md`
- Lines 104–106 (Step 3 — Sign in and verify):

  > "Visit `/admin/health` — every component should be **green**:
  > backend, postgres, redis, worker, beat. The `dt` row will be
  > **OPEN** (Dependency-Track not yet wired in) — that is normal at
  > this stage."

- Lines 140–141 (Step 4's "End-to-end first-success checklist"):

  > "Go to `/admin/dt` — DT row may be OPEN for up to 60 seconds on
  > first boot; wait for it to flip to **CLOSED**."

- Authoritative source: `scripts/install.sh` brings up only
  `docker-compose.yml` (no `docker-compose.dt.yml` overlay), so on the
  default path `DT_API_KEY` is unset and the breaker stays OPEN
  indefinitely. The checklist's "wait for it to flip to CLOSED" is
  unreachable for a stock install — the admin will wait, then look for
  a misconfiguration that does not exist.

- **Impact for Task α**: this is the 20-minute step in the persona
  path. A first-time admin who follows the checklist as written sits
  on OPEN forever; in the wall-clock log this would surface as a
  multi-minute "what did I miss" loop.

- **Fix**: rewrite the checklist line to mirror Step 3's truth
  ("OPEN is expected unless you brought up the DT overlay"), or split
  the checklist into "without DT overlay" and "with DT overlay" two
  bullets.

### α-2 — Dangling `team_id` UUID instruction in onboarding — **D, P2**

- File: `docs-site/docs/admin-guide/users-and-teams.md`
- Lines 69–76 (Onboarding teammates):

  > "Admin creates the team at `/admin/teams → New team` and **notes
  > the team UUID** (visible in the URL or via `GET /v1/admin/teams`)."

- Authoritative source: the documented add-member path that follows
  is `/admin/users → <user> → Memberships → Add to team` — a UI
  picker keyed by team name, not by UUID. The teammate self-registers
  by email; the admin matches them by email. The UUID is never used.

- **Impact for Task α**: minor noise. A diligent admin copies the UUID
  somewhere, then never pastes it; harmless but distracting.

- **Fix**: drop the "notes the team UUID" parenthetical, or move it to
  a separate paragraph titled "Scripted mass onboarding" alongside the
  existing `POST /v1/admin/teams/{team_id}/members` recipe.

---

## Task β — First Developer

**Persona path (per session plan, lines 47–54):**

```
0  /register → 3  team add + re-login → 5  /projects/new
10 Scan + progress drawer → 20 Components/Vulns/Licenses/SBOM
25 VEX Mark not affected → 30 SBOM CycloneDX download
```

**Findings:**

### β-1 — `POST /v1/scans/source` endpoint does not exist — **D + S, P0**

- File: `docs-site/docs/user-guide/scans.md`
- Lines 38–44 (Warning: Branch selection at v2.0.0):

  > "...use the API: `POST /v1/scans/source {project_id, branch:
  > \"develop\"}`."

- Authoritative source:
  - `apps/backend/api/v1/projects.py:362–367` registers
    `POST /{project_id}/scans` (the only POST scan-trigger route in
    the codebase).
  - `apps/backend/api/v1/scans.py` has only `GET /scans`,
    `GET /scans/{scan_id}`, `GET /projects/{project_id}/scans` —
    no `POST /scans/source`.
  - `apps/backend/schemas/scan.py:260–273` defines `ScanCreate` with
    `kind: ScanKind = "source"` and `metadata: dict` — there is **no
    `branch` field**. Branch override at v2.0.0 must go through
    Project Settings → `default_branch` (already correctly stated
    elsewhere in the same doc).

- **Impact for Task β**: a developer wanting to scan a feature branch
  follows the warning and gets a 404 plus a schema-rejection
  (`extra="forbid"`). The doc's own "From the API" section three
  lines below uses the correct endpoint — the contradiction is
  internal to the same page.

- **Fix**: change the warning to:

  > "...temporarily change `default_branch` in **Project Settings**
  > — there is no branch-override field in either the UI or the
  > API at v2.0.0."

  (Drop the fake endpoint entirely.)

### β-2 — Vulnerability drawer button list does not match VEX states — **D, P1**

- File: `docs-site/docs/user-guide/vulnerabilities.md`
- Line 72 (The drawer — finding detail → Analysis):

  > "VEX status action buttons (one per allowed transition: Confirm,
  > Mark exploitable, Mark not affected, Mark in triage, Mark
  > resolved, Mark false positive, Mark not applicable)."

- Authoritative source:
  `apps/backend/schemas/vulnerability_detail.py:40–48` defines:

  ```python
  VulnFindingStatus = Literal[
      "new", "analyzing", "exploitable",
      "not_affected", "false_positive",
      "suppressed", "fixed",
  ]
  ```

- Mismatches:
  - **"Confirm"** — no corresponding state. CycloneDX VEX has no
    `confirmed` state; the closest is `exploitable`.
  - **"Mark not applicable"** — no corresponding state. CycloneDX VEX
    has no `not_applicable` state; the closest is `not_affected`,
    which is already listed.
  - **Missing "Mark suppressed"** — the `suppressed` state has no
    button listed, even though the doc's own table at lines 35–43
    enumerates it explicitly with build-gate behavior.

- **Impact for Task β**: the persona path (line 25-min step) is
  "Mark not affected — justification" — that single button **does**
  exist, so the persona completes the milestone. But a developer
  exploring the drawer will look for "Suppress" and not find it
  under that label.

- **Fix**: rewrite the button list to match the 7 states, e.g.:

  > "...one per allowed transition: Mark in triage (`analyzing`),
  > Mark exploitable, Mark not affected, Mark false positive, Mark
  > suppressed, Mark fixed. The `new` state has no inbound button —
  > it is the initial state."

### β-3 — Dangling `team_id` lookup instruction — **D, P2**

- File: `docs-site/docs/user-guide/projects.md`
- Lines 68–71:

  > "To find your `team_id`, ask the team-admin or super-admin to
  > share it (they see it under `/admin/teams` → row drawer).
  > Developers do not have direct access to the `/v1/admin/teams`
  > listing at v2.0.0; a `GET /v1/users/me/memberships` shortcut is
  > on the roadmap."

- Authoritative source: the API create example three blocks above
  (lines 53–62) does **not** include `team_id` — the body is just
  `{name, description, git_url}`. The `team_id` is derived
  server-side from the actor's active team (`projects.md:25`).

- **Impact for Task β**: a developer reading top-to-bottom will copy
  the curl example, succeed, then read the paragraph below and
  wonder if they did it wrong. Mild C-category cognition risk that
  shows up as D-category text noise.

- **Fix**: drop the paragraph, or relocate it under a section about
  *future* multi-team scoping (since the field is reserved but not
  required at v2.0.0).

---

## Task γ — First CI integration

**Persona path (per session plan, lines 62–68):**

```
0  /integrations API key → 5  .github/workflows/sca.yml
15 PR push → workflow run → 25 portal /scans queue
30 PR comment via TRUSTEDOSS_GITHUB_TOKEN
```

**Findings:**

### γ-1 — `allowed_actions` taxonomy does not exist — **D + S, P0**

- File: `docs-site/docs/ci-integration/github-actions.md`
- Line 59 (Setup → Generate an API key):

  > "In the portal: **Project Settings → CI/CD → API keys → New API
  > key**. Allowed actions: `scan:trigger`, `scan:read`,
  > `report:download`. See [API keys](../admin-guide/api-keys.md)."

- Line 224 (Troubleshooting → `403 Forbidden`):

  > "The API key is valid but does not have the required action
  > allowed. Re-issue the key with `scan:trigger`, `scan:read`,
  > `report:download`."

- Authoritative source: `grep -rn "allowed_actions\|scan:trigger\|
  scan:read\|report:download" apps/backend/` returns **zero
  matches**. The sibling doc
  `docs-site/docs/admin-guide/api-keys.md:64` is explicit:

  > "The key inherits the **role of the issuing user** at request
  > time — **there is no separate "effective role" or "allowed
  > actions" list at v2.0.0**."

  And api-keys.md:196 lists it under Roadmap (v2.x):

  > "Per-key role override (`effective_role`) and a granular
  > `allowed_actions` taxonomy (`scan:trigger`, `scan:read`,
  > `report:download`, `webhook:receive`, `*`). Today the key
  > inherits the issuing user's role and the full RBAC surface."

- **Impact for Task γ**: a CI engineer following github-actions.md
  step 1 will look for an "Allowed actions" multi-select in the
  Create API key dialog, fail to find it, and either (a) waste time
  searching, (b) DM a teammate, or (c) abandon. Also, the
  github-actions.md path "Project Settings → CI/CD → API keys" does
  not match the actual location, which is `/integrations` (per
  api-keys.md:17 and integrations.md:20).

- **Fix**: github-actions.md "Generate an API key" should say:

  > "In the portal: **/integrations → API keys → New API key**.
  > Pick scope `project` and bind it to the project you want CI to
  > scan. See [API keys](../admin-guide/api-keys.md) for the scope
  > model."

  Troubleshooting `403`: rewrite to point at scope, not allowed
  actions:

  > "The API key's scope does not cover the project. Re-issue the
  > key with scope `project` (preferred) or `team`, and verify the
  > project belongs to the scope-bound team."

### γ-2 — Action `uses:` reference is repo-internal — **D, P3 / informational**

- File: `docs-site/docs/ci-integration/github-actions.md`
- Lines 17–19:

  > "Use the in-repo composite action at `actions/scan/action.yml`
  > directly via `uses: trustedoss/trustedoss-portal/actions/
  > scan@v2.0.0` (referenced from this monorepo). A standalone
  > Marketplace publication is on the roadmap."

- Authoritative source: `git tag` confirms `v2.0.0` exists ✓ and
  `actions/scan/action.yml` exists ✓. The composite-action
  reference is **valid** for a user whose CI repo is *separate*
  from the portal monorepo. No fix needed; recorded only because a
  reader who is *in the portal repo* may misread "in-repo" as
  "you must vendor the action into your own repo."

- **Fix**: optional — add one sentence: "Your CI repository does
  not need to be a fork of this monorepo; the `uses:` line above
  works from any GitHub-hosted runner that can reach
  `github.com/trustedoss`." (Mild C-prevention, not blocking.)

---

## Friction summary

| # | Task | Category | Priority | Where | One-line |
|---|------|----------|----------|-------|----------|
| α-1 | α | D | **P0** | docker-compose.md:140–141 | Checklist tells admin to wait for DT CLOSED, but install.sh leaves DT OPEN. |
| α-2 | α | D | P2 | users-and-teams.md:69–76 | "Note the team UUID" — UUID is never used in the documented flow. |
| β-1 | β | D + S | **P0** | scans.md:38–44 | `POST /v1/scans/source` does not exist; `branch` is not in `ScanCreate`. |
| β-2 | β | D | P1 | vulnerabilities.md:72 | Drawer button list ("Confirm", "not applicable") does not match the 7 VEX states. |
| β-3 | β | D | P2 | projects.md:68–71 | Dangling `team_id` lookup paragraph for a field the create body does not require. |
| γ-1 | γ | D + S | **P0** | github-actions.md:59, 224 | `scan:trigger`/`scan:read`/`report:download` allowed-actions taxonomy does not exist; UI path "Project Settings → CI/CD" is wrong (it is `/integrations`). |
| γ-2 | γ | D | P3 | github-actions.md:17–19 | "in-repo composite action" wording — valid but mildly misleading for first-time readers in their own repo. |

---

## Priority backlog

| Priority | Task | Category | Estimated friction prevented |
|----------|------|----------|------------------------------|
| **P0** | α-1: docker-compose.md DT checklist | D | 5–10 min on the 20-min admin milestone. |
| **P0** | β-1: scans.md fake API endpoint | D + S | Hard 404 + schema rejection; trust loss. |
| **P0** | γ-1: github-actions.md allowed_actions | D + S | Hard "checkbox not found"; ~10 min plus a teammate DM. |
| **P1** | β-2: vulnerabilities.md drawer buttons | D | Reader confusion finding the Suppress button; slight slowdown on triage. |
| **P2** | α-2: users-and-teams.md UUID note | D | <1 min noise per onboarding. |
| **P2** | β-3: projects.md team_id paragraph | D | <1 min noise per first project. |
| **P3** | γ-2: github-actions.md "in-repo" wording | D (informational) | Optional polish. |

---

## What this pass did NOT cover

The session plan (`2026-05-11-dogfooding-first-30min.md`) asks for
wall-clock measurement, persona-aware cognition tracking, and
real-environment prerequisite friction. Those require a human seated
at a fresh droplet with a clock running. Specifically:

- **P (prerequisite)**: DNS A-record propagation, `:80`/`:443`
  firewall, `sudo` prompt count, `docker-compose` V1 install on
  Ubuntu 22.04 (Compose plugin nudge), `INSTALL_TLS_EMAIL` if the
  operator does not know an `admin@<domain>` convention.
- **U (UI)**: discoverability of `/admin/backup`'s "Run manual
  backup now" button (one-click vs two-click vs hidden in
  toolbar?), one-time reveal modal in `/integrations` (does the
  warning text register?), `/admin/dt`'s status card readability
  on a fresh install.
- **C (cognitive)**: the difference between "the doc says OPEN is
  normal" (correct) and an admin's instinct to fix anything red.
  Where docs say "wait" but a human's instinct says "act."
- **S beyond docs**: backend exceptions, empty UI responses, JS
  console errors on a brand-new install. None of these surface
  in a static walk.

These are the items the Option 1 dogfooding session (real droplet,
human stopwatch, persona tone) should target. The D-fixes from this
pass should land **first** so the human dogfooding measures
genuine residual friction instead of friction we already know about.

---

## Next steps

1. Apply the seven D-fixes above as a single docs PR (or two — the
   three P0s in one, the rest in a follow-up).
2. After merge, run the Option 1 dogfooding pass (real fresh
   environment + wall-clock + persona tone) and write
   `2026-05-12-dogfooding-results-real.md` next to this file.
3. Use the wall-clock results from that real pass to set product
   backlog priority for the U/C/S categories we could not measure
   here.

---

## Handoff

- Pass type: **simulated dry-run** (Option 2 of the session plan).
- Wall-clock data: **none** (intentionally).
- Friction surfaced: 7 entries (3 × P0, 1 × P1, 2 × P2, 1 × P3).
- Source-of-truth check coverage: 100% of every action/endpoint/env
  the three task personas would touch in the 90-minute combined path.
- All findings are doc-side (D / D+S). No product code change is
  proposed by this pass.
