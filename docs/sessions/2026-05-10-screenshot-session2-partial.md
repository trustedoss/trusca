# Session — Screenshot capture Session 2 (partial / WIP)

> Source prompt: `docs/sessions/_next-session-prompt-guide-screenshots.md` §"세션 2 — EN 일괄 캡처".
> Date: 2026-05-10
> PR: [#54 (Draft)](https://github.com/trustedoss/trustedoss-portal/pull/54) (`chore/screenshot-en-guides`)
> Latest commit: `cf5bd06` (rebased onto `87b7678` = PR #53 squash merge)
> Status: WIP — 14/22 captures landed, 8 captures blocked on a refresh-token rotation interaction with `storageState`. Markdown reference insertion held until all PNGs are present.

## What landed

**Pipeline restructure** (Session 2's actual contribution):

| Artefact | Purpose |
|---|---|
| `apps/frontend/tests/screenshots/global-setup.ts` | Single seed + login per capture invocation. Persists `cookies + localStorage` to `.storage-state.json`. Exposes `SHARED_PROJECT_NAMES` for the spec files. |
| `apps/frontend/playwright.screenshots.config.ts` | Adopts `use.storageState` → every spec test starts already authenticated. |
| `apps/frontend/tests/screenshots/.gitignore` | Runtime artefacts (`.storage-state.json`, `.seed.json`) are not tracked. |
| `apps/frontend/tests/screenshots/_helpers.ts` | Trimmed to `captureScreenshot` + `hideDevOnlyChrome`. Seeding/login moved into globalSetup. |
| `apps/frontend/tests/screenshots/capture.spec.ts` | (admin/backup PoC) — login removed, simplified to navigation + capture only. |
| `apps/frontend/tests/screenshots/capture_user_guide.spec.ts` | One `describe.serial` per docs page (10 pages). |
| `apps/frontend/tests/_harness/ApprovalsHarness.ts` | New — `/approvals` page domain verbs (mount, row count, empty, drawer). |
| `apps/frontend/tests/_harness/ScansQueueHarness.ts` | New — `/scans` global queue page mount + row count. |
| `apps/frontend/tests/_harness/PortalPage.ts` | + `selectSbomTab`, `expectSbomTabReady`. |

**Captures landed** — 14 PNG, all 1440×900:

| Page | Slug |
|---|---|
| admin/backup (from PR #53) | `admin-backup-list`, `admin-backup-trigger-toast`, `admin-backup-restore-modal`, `admin-backup-restore-typing-gate-enabled` |
| user-guide | `user-auth-login`, `user-auth-forgot`, `user-profile-mounted`, `user-profile-connected-accounts`, `user-projects-list`, `user-projects-create-form`, `user-project-detail-overview`, `user-sbom-tab`, `user-obligations-distribution`, `user-notifications-inbox` |

## Blocker (next session must fix this first)

After the storageState refactor only **3 of 14** captures pass on a clean run. The pre-auth views (login, forgot-password) and the *first* logged-in capture (profile-mounted) succeed; everything subsequent fails with "navigation to /projects didn't settle".

**Diagnosis**: refresh-token rotation. CLAUDE.md §품질·보안·운영 §3 codifies "refresh 회전 + 재사용 탐지". globalSetup logs in once and saves the cookie. The first spec consumes the cookie's refresh token to mint an access token (refresh rotates → new refresh issued → not in storage state). The second spec adopts the same storage state, replays the now-rotated refresh, the backend flags reuse → 401 → SPA redirects to `/login`.

The 14 PNGs that did land are a mix of:
- Pre-auth views (login, forgot) — storage state irrelevant.
- Captures from the *prior* commit's per-spec login flow (which worked for the first 5 logins before rate-limit kicked in).

**Recommended fix (route a)** — inject the access token directly into zustand:

```ts
// global-setup.ts (sketch)
// ... after login ...
const accessToken = await page.evaluate(() =>
  (window as any).__authStore?.accessToken ?? null
);
fs.writeFileSync(SEED_PATH, JSON.stringify({ ...seed, accessToken }, null, 2));

// In each spec's beforeEach (or via a fixture)
test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ token }) => {
    const w = window as any;
    if (typeof w.__setAccessToken === "function") w.__setAccessToken(token);
  }, { token: SEED.accessToken });
});
```

This bypasses refresh-token rotation entirely — every page boots with the in-memory token already set, no refresh dance, no 401 race.

**Alternative (route b)**: dev-only env flag to disable rotation during capture runs. Heavier, touches backend.

## State for the next session

- Branch: `chore/screenshot-en-guides` (this branch — keep, do not re-create)
- PR: [#54](https://github.com/trustedoss/trustedoss-portal/pull/54) — draft, must come out of draft + merge once captures complete and EN markdown reference sweep lands
- main HEAD: `87b7678`

**Order of operations next session**:

1. Apply route (a) — `addInitScript` access-token injection. Test by running `make screenshots-capture` and confirm 22/22 pass.
2. Re-capture `user-profile-connected-accounts` (current asset on disk is from the prior partial run; we want all final PNGs to come from the same run).
3. Visually verify each of the 8 newly-emitted PNGs against the matching guide prose.
4. Sweep all 10 EN user-guide markdown files to insert `![…](/img/screenshots/<slug>.png)` references at the contextually right paragraphs. One commit per page or one bulk commit — operator's call.
5. `npm run typecheck` / `npm run lint` / Docusaurus EN build green.
6. PR ready-for-review, merge.

## Captures pending (Session 2 close-out)

- `user-scans-queue` — global scan queue with seeded scan rows.
- `user-components-list` — Components tab (50 rows seeded).
- `user-licenses-donut` — Licenses tab donut chart.
- `user-vulns-list` — Vulnerabilities tab (30 vulns seeded).
- `user-approvals-inbox` — Approvals inbox (empty until policy hits).
- `user-notifications-prefs` — Notification preferences screen.
- `user-integrations-keys` — API keys section.
- `user-integrations-key-create` — Create API key dialog open.

## Out-of-scope (deferred to subsequent sessions)

- `admin-guide` bulk (5 pages, ~20 cuts) — Session 2.5, separate PR (per the user's §3 split decision).
- KO mirror + `./img/...` legacy reference sweep + GitHub Pages visual sanity — Session 3, separate PR.
- Visual regression CI / animated walkthroughs / per-locale Korean captures / a11y alt-text audit / image compression automation — separate sprints (chore-backlog § "Screenshots automation" → 후속).

## Memory updates

No new memory entries from this session — the rotation interaction is documented in this handoff and in the PR #54 description so the next session can pick it up cold. The earlier Session 1 memory entries (`feedback_seed_component_prefix_isolation`, `feedback_dev_reset_frontend_npm_install`, `feedback_dev_reset_alembic_gap`) remain valid and load-bearing.

## See also

- Source prompt: `docs/sessions/_next-session-prompt-guide-screenshots.md`
- Session 1 handoff: `docs/sessions/2026-05-10-screenshot-infra.md` (PR #53, merged at `87b7678`)
- PR #54 (Draft): <https://github.com/trustedoss/trustedoss-portal/pull/54>
