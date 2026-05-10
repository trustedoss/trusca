/**
 * Shared helpers for the guide-screenshot capture pipeline.
 *
 * Multiple spec files (admin/backup PoC + user-guide bulk + future admin
 * pages) all want the same {viewport hiding, slug-validating PNG writer,
 * seed-or-skip wrapper}, so they live here. The spec files import what
 * they need and stay focused on per-page choreography.
 */
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { test, type Page, type TestInfo } from "@playwright/test";

import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Repo root, computed from this file's location at module load. */
export const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");

/** Where committed PNG assets live. EN + KO Markdown share these paths. */
export const SCREENSHOT_DIR = path.join(
  REPO_ROOT,
  "docs-site",
  "static",
  "img",
  "screenshots",
);

/**
 * Hide dev-only chrome that does not belong in shipped guide assets.
 *
 * The dev SPA mounts `<ReactQueryDevtools/>` which renders a floating
 * bottom-right toggle button. Production builds tree-shake the import
 * (`import.meta.env.DEV` branch), so the docs reader never sees it — but
 * captures taken against the dev stack do, and they leak into the asset.
 *
 * We inject a stylesheet that hides every TanStack Devtools surface
 * (button + open panel) by class prefix. Removing the elements outright
 * would race the Devtools' own re-render cycle; CSS is durable.
 */
export async function hideDevOnlyChrome(page: Page): Promise<void> {
  await page.addStyleTag({
    content: `
      .tsqd-parent-container,
      [class*="tsqd-"],
      [aria-label*="React Query" i] {
        display: none !important;
        visibility: hidden !important;
      }
    `,
  });
}

/**
 * Write a viewport screenshot under `docs-site/static/img/screenshots/`.
 *
 * `fullPage: false` keeps the asset bounded to the 1440×900 viewport that
 * runtime users actually see; the alternative (full-page sewn capture)
 * produces tall narrow PNGs that read like printout artefacts in the
 * docs. Dev-only chrome is hidden right before the capture so the asset
 * matches what production users will see.
 */
export async function captureScreenshot(
  page: Page,
  slug: string,
): Promise<void> {
  if (!/^[a-z0-9-]+$/.test(slug)) {
    throw new Error(
      `captureScreenshot: slug "${slug}" must be kebab-case ([a-z0-9-]+)`,
    );
  }
  await hideDevOnlyChrome(page);
  const out = path.join(SCREENSHOT_DIR, `${slug}.png`);
  await page.screenshot({ path: out, fullPage: false });
}

/**
 * Acquire a seeded super-admin or skip with a friendly error so the
 * capture run never exits non-zero just because the dev stack is not up.
 */
export function tryAcquireSeed(
  testInfo: TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed — bring docker-compose dev up first: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
    return null;
  }
}

/**
 * Build a seed-options object with the standard guide-capture profile
 * (super_admin + 50 components + 30 vulns + obligations + GitHub OAuth)
 * and a per-page namespaced + timestamped `componentPrefix` so back-to-
 * back runs don't collide on `uq_components_purl`.
 */
export function buildSeedOptions(
  pageSlug: string,
  projectNames: string[],
): Parameters<typeof seedE2eUser>[0] {
  return {
    projectNames,
    superAdmin: true,
    withScan: true,
    componentCount: 50,
    componentPrefix: `screenshot-${pageSlug}-${Date.now()}`,
    vulnerabilityCount: 30,
    withObligations: true,
    withOAuthIdentity: "github",
  };
}

/**
 * `test.beforeAll` callback wrapper that handles Playwright's required
 * destructure-pattern argument shape and ESLint's `no-empty-pattern`
 * lint rule in one place.
 */
export function withSeedBeforeAll(
  pageSlug: string,
  projectNames: string[],
  setSeed: (s: SeedSummary | null) => void,
): void {
  // Playwright requires the first beforeAll argument to be an object-
  // destructure pattern (even if empty). ESLint's no-empty-pattern would
  // otherwise reject `({}, testInfo)` — disable threads both.
  // eslint-disable-next-line no-empty-pattern
  test.beforeAll(async ({}, testInfo) => {
    setSeed(tryAcquireSeed(testInfo, buildSeedOptions(pageSlug, projectNames)));
  });
}
