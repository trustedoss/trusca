/**
 * Playwright globalSetup for the screenshot capture pipeline.
 *
 * Pinpoints the auth side-effect: the backend rate-limits login at
 * 5 attempts per IP per minute (CLAUDE.md §품질·보안·운영 §3). A
 * per-spec login matrix would trip that limit halfway through the
 * 25-cut bulk run. Instead we log in once during globalSetup, persist
 * the resulting cookies + localStorage to disk, and have every spec
 * adopt that storage state via `playwright.screenshots.config.ts use`.
 *
 * Side-effects (deliberate):
 *   - Seeds one super-admin user with multiple projects (one per page
 *     scenario). All spec files share the user; only the project they
 *     navigate to differs. Cross-page seed isolation is not needed for
 *     read-only screenshots.
 *   - Writes `.storage-state.json` (cookies + localStorage) consumed
 *     by `use.storageState`.
 *   - Writes `.seed.json` so the spec files can resolve the project
 *     names + scan ids without re-seeding.
 *
 * Both side-effect files live alongside this script and are gitignored
 * via `apps/frontend/tests/screenshots/.gitignore`.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type FullConfig } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { seedE2eUser } from "../_harness/seed";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const STATE_PATH = path.join(__dirname, ".storage-state.json");
export const SEED_PATH = path.join(__dirname, ".seed.json");

/**
 * Project names for this capture run. Timestamped so back-to-back runs
 * do not collide with each other's projects in the shared dev DB —
 * the SPA's project list otherwise shows multiple rows with the same
 * name and `openProjectDetail("alpha")` fails Playwright strict mode.
 *
 * Spec files do not import these names directly; they read the
 * persisted `.seed.json` via `_helpers.readSeedProjectNames()` so the
 * timestamp picked here matches.
 */
function buildProjectNames(stamp: number): string[] {
  return [
    `screenshots-bulk-alpha-${stamp}`,
    `screenshots-bulk-beta-${stamp}`,
  ];
}

/* eslint-disable @typescript-eslint/no-unused-vars */
export default async function globalSetup(_config: FullConfig): Promise<void> {
  const stamp = Date.now();
  const seed = seedE2eUser({
    projectNames: buildProjectNames(stamp),
    superAdmin: true,
    withScan: true,
    componentCount: 50,
    // Timestamped prefix avoids `uq_components_purl` collisions across
    // back-to-back capture runs (e.g. local iteration).
    componentPrefix: `screenshot-bulk-${stamp}`,
    vulnerabilityCount: 30,
    withObligations: true,
    withOAuthIdentity: "github",
  });

  const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ baseURL });
  const page = await ctx.newPage();
  const auth = new AuthHarness(page, baseURL);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);

  // Pull the access token out of the in-memory zustand store so spec
  // files can re-inject it via `addInitScript` on every fresh page.
  // This bypasses the refresh-token rotation policy (CLAUDE.md
  // §품질·보안 §3) which would otherwise invalidate `storageState`
  // after the second spec re-uses the cookie's refresh token.
  const accessToken: string | null = await page.evaluate(() => {
    const w = window as unknown as { __authStore?: { accessToken?: string | null } };
    return w.__authStore?.accessToken ?? null;
  });

  await ctx.storageState({ path: STATE_PATH });
  await browser.close();

  fs.writeFileSync(
    SEED_PATH,
    JSON.stringify({ ...seed, accessToken }, null, 2),
  );
}
/* eslint-enable @typescript-eslint/no-unused-vars */
