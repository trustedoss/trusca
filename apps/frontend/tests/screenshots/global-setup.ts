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
 * Seed tag: the string that makes this run's project, component and team
 * names distinct from another run's.
 *
 * Locally it is a timestamp, because repeat runs share one dev database and
 * would otherwise collide: the SPA's project list would show several rows
 * with one name and `openProjectDetail("alpha")` would fail Playwright's
 * strict mode, and duplicate component purls would violate
 * `uq_components_purl` outright.
 *
 * In CI it is derived from the config file name instead. A CI job starts from
 * an empty database, so runs cannot collide with each other; the only
 * collision left is between the configs that share this globalSetup (visual,
 * a11y, responsive, screenshots, walkthroughs), which run in sequence against
 * that one database. Naming per config keeps them apart while keeping each
 * config's names identical from one CI run to the next.
 *
 * Identical names are what the visual gate needs. The seeded strings are
 * rendered in the project heading, the breadcrumb, the project list, the
 * scans table, the components table and the top bar's team switcher, so a
 * timestamp in them meant every capture differed from every other by however
 * many pixels those strings occupy. That was the gate's dominant source of
 * run-to-run noise, and the diff ceiling had to clear it.
 *
 * Spec files do not import these names; they read the persisted `.seed.json`
 * through `_helpers.readSeedProjectNames()`, so either choice reaches them
 * the same way.
 */
function seedTag(config: FullConfig): string {
  if (!process.env.CI) return String(Date.now());
  const configFile = config.configFile ?? "";
  const derived = path
    .basename(configFile)
    .replace(/^playwright\./, "")
    .replace(/\.config\.[cm]?[jt]s$/, "");
  // An empty `configFile` means Playwright ran without one, which none of our
  // pipelines do. Fall back rather than seeding every config as "" and
  // colliding.
  return derived || String(Date.now());
}

function buildProjectNames(tag: string): string[] {
  return [`screenshots-bulk-alpha-${tag}`, `screenshots-bulk-beta-${tag}`];
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  const tag = seedTag(config);
  const seed = seedE2eUser({
    projectNames: buildProjectNames(tag),
    superAdmin: true,
    withScan: true,
    componentCount: 50,
    componentPrefix: `screenshot-bulk-${tag}`,
    // Everything else the seed generates at random hangs off this: the team
    // name in the top bar, the seeded emails in the admin table, the git URL
    // on the project page, and the synthetic CVE ids. All of them are on
    // screen.
    stableSuffix: tag,
    vulnerabilityCount: 30,
    withObligations: true,
    withOAuthIdentity: "github",
    // Marathon bundle 5 (4a): header bell unread badge fixture.
    // Three unread notifications spread across 3 distinct kinds so the
    // bell badge renders "3" and the inbox preview shows mixed icons.
    notificationCount: 3,
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
