# Screenshots automation — full 4-PR series complete

> Source prompt: `docs/sessions/_next-session-prompt-guide-screenshots.md`
> Date: 2026-05-10
> PRs: #53 → #54 → #55 → #56 (all merged)
> Status: ✅ Series complete; one cleanup chore (Session 4) tracked under chore-backlog.

## What landed across the series

### PR #53 — Session 1: Infra + admin/backup PoC (`87b7678`)
- `apps/frontend/playwright.screenshots.config.ts` (1440×900, isolated from e2e matrix)
- `apps/frontend/tests/screenshots/capture.spec.ts` (admin/backup 4 cuts)
- `Makefile` targets `screenshots-capture` / `screenshots-clean`
- `docs-site/static/img/screenshots/` (canonical asset location, EN+KO share via absolute path)
- `.gitignore` for staging area
- `docs-site/docs/contributor-guide/getting-started.md` (+KO) gains a *Regenerate guide screenshots* operator section
- 2 placeholder PNGs migrated → `git rm`'d

### PR #54 — Session 2: user-guide bulk (`fd48e69`)
- `tests/screenshots/global-setup.ts` — single seed + login persists `cookies + localStorage + access token`
- `_helpers.ts:applyAuthFromSeed` — `addInitScript` injects `__setAccessToken` so every fresh page boots authenticated, sidestepping the 5/min IP login rate-limit and refresh-token rotation policy (CLAUDE.md §품질·보안 §3)
- New harnesses: `ApprovalsHarness`, `ScansQueueHarness`
- New PortalPage verbs: `selectSbomTab`, `expectSbomTabReady`
- `capture_user_guide.spec.ts` — 10 `describe.serial` blocks (one per docs page)
- 15 user-guide PNG cuts landed
- 3 cuts marked `test.fixme` with explicit reasons: `user-scans-queue`, `user-approvals-inbox`, `user-notifications-prefs`
- 7 EN markdown files updated (3 placeholder migrations + 12 new references)

### PR #55 — Session 2.5: admin-guide bulk (`72dcc6e`)
- `capture_admin_guide.spec.ts` — 5 `describe.serial` blocks
- 6 admin-guide PNG cuts: `admin-users-list`, `admin-teams-list`, `admin-dt-status`, `admin-audit-list`, `admin-disk-list`, `admin-health-cards`
- 4 EN markdown files updated (6 new references)

### PR #56 — Session 3: KO mirror (`8f457df`)
- 10 KO markdown files mirror the EN diff, alt text translated
- EN+KO share a single PNG asset (no i18n-side asset duplication)
- Docusaurus EN + KO build SUCCESS

## Cumulative captures (25 PNG, all 1440×900)

| Group | Slugs |
|---|---|
| admin/backup (PR #53) | `admin-backup-list`, `admin-backup-trigger-toast`, `admin-backup-restore-modal`, `admin-backup-restore-typing-gate-enabled` |
| user-guide (PR #54) | `user-auth-login`, `user-auth-forgot`, `user-profile-mounted`, `user-profile-connected-accounts`, `user-projects-list`, `user-projects-create-form`, `user-project-detail-overview`, `user-components-list`, `user-licenses-donut`, `user-vulns-list`, `user-sbom-tab`, `user-obligations-distribution`, `user-notifications-inbox`, `user-integrations-keys`, `user-integrations-key-create` |
| admin-guide (PR #55) | `admin-users-list`, `admin-teams-list`, `admin-dt-status`, `admin-audit-list`, `admin-disk-list`, `admin-health-cards` |

## Decisions cemented (prompt §4 — all 8)

1. ✅ Asset location `docs-site/static/img/screenshots/` (committed via `.gitkeep`); EN + KO share via absolute path.
2. ✅ Naming `<page-slug>-<section-slug>.png` (regex-guarded by `captureScreenshot()`).
3. ✅ PNG 1440×900 lossless (config-fixed viewport).
4. ✅ Harness-first — every spec consumes harness verbs, no direct `page.click()`.
5. ✅ EN-only captures; alt text only translated for KO.
6. ✅ Single seed profile (`--super-admin --with-scan --component-count 50 --vulnerability-count 30 --with-obligations --with-oauth-identity github`) hoisted into `globalSetup`.
7. ✅ PII-safe — `e2e-<suffix>` deterministic fixtures only.
8. ✅ Visual regression CI explicitly out-of-scope; documented in contributor-guide.

## Refinements vs the prompt

- Per-page namespacing of `componentPrefix` (timestamped) was added to prevent `uq_components_purl` collisions across back-to-back capture runs. Prompt §4.6 said "single seed" — globalSetup honors that with one user / many timestamped projects, which is a strict superset of the original decision.
- Decoupled the spec files from the `globalSetup` shape via `readSeedProjectNames()` — projects rename per run, but specs resolve them lazily.
- `addInitScript`-based access-token injection was the linchpin that turned `storageState` from "works once" into "works for every spec without retriggering refresh rotation".

## Pending work (chore-backlog § Screenshots automation 후속)

**Session 4 candidate** (single small PR — combine all of these):
- `admin/api-keys.md` (EN+KO) — markdown patch reusing the user-integrations PNGs.
- 3 `test.fixme` cuts:
  - `user-scans-queue` — `/scans` global queue mount predicate too strict for the seeded-but-no-running-scan shape.
  - `user-approvals-inbox` — `/approvals` mount times out; suspect team-scope mismatch with the bulk seed.
  - `user-notifications-prefs` — `notifications-prefs-section` not visible during capture; viewport / scroll-into-view race.
- 3 legacy `./img/...` placeholder references to migrate alongside the fixme captures (notifications-bell, notifications-prefs, integrations-webhooks).

**Subsequent sprints** (already in chore-backlog):
- Visual regression CI (Percy / Chromatic / Playwright `expect(page).toHaveScreenshot()`).
- Animated walkthroughs (`.gif` / `.mp4`).
- Locale-specific Korean captures (where Korean data is the point).
- a11y alt-text audit (i18n-specialist review).
- Image compression automation (`oxipng` / `pngquant` + CI size gate).

## Environment debt surfaced

Logged in chore-backlog § Screenshots automation 부산물:
- `apps/backend/Dockerfile.worker` `--no-cache` rebuild fails on arm64 macOS (`dotnet-sdk-8.0` not in microsoft debian/12 arm64 repo).
- `dev-reset.sh` does not auto-run `alembic upgrade head` after volume destroy.
- frontend container `node_modules` survives `dev-reset` and goes stale when `package.json` advances (`@radix-ui/react-tabs` import failure).
- `super_admin` `/projects` visibility shows ghost projects from prior capture runs; operators TRUNCATE before producing canonical assets.

These are independent of the capture work itself but were surfaced repeatedly while iterating on the pipeline. Each has a quick-fix recipe in the backlog entry.

## See also

- Source prompt: `docs/sessions/_next-session-prompt-guide-screenshots.md`
- Per-session handoffs:
  - `docs/sessions/2026-05-10-screenshot-infra.md` (Session 1)
  - `docs/sessions/2026-05-10-screenshot-session2-partial.md` (Session 2 mid-flight; fully resolved by the close-out commits in PR #54)
- `docs/chore-backlog.md` § Screenshots automation
