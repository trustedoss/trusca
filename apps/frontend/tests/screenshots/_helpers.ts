/**
 * Shared helpers for the guide-screenshot capture pipeline.
 *
 * Multiple spec files (admin/backup PoC + user-guide bulk + future
 * admin pages) all want the same {viewport hiding, slug-validating
 * PNG writer}, so they live here. Authentication is handled centrally
 * by `global-setup.ts` + `playwright.screenshots.config.ts use.storageState`,
 * so the helpers no longer concern themselves with seeding or login —
 * specs receive an already-authenticated `Page` and only need to
 * navigate and snapshot.
 */
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

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
