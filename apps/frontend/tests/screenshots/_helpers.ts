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
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Where global-setup.ts persists the seed summary + access token. */
const SEED_PATH = path.join(__dirname, ".seed.json");

interface SeedFile {
  email: string;
  password: string;
  user_id: string;
  team_id: string;
  project_names: string[];
  project_ids: string[];
  accessToken?: string | null;
}

let cachedSeed: SeedFile | null = null;

function readSeed(): SeedFile {
  if (cachedSeed !== null) return cachedSeed;
  if (!fs.existsSync(SEED_PATH)) {
    throw new Error(
      `seed file missing at ${SEED_PATH} — globalSetup did not run`,
    );
  }
  cachedSeed = JSON.parse(fs.readFileSync(SEED_PATH, "utf8")) as SeedFile;
  return cachedSeed;
}

/**
 * Project names persisted by `global-setup.ts` for this capture run.
 * Timestamped per run so spec files do not have to hard-code a name
 * that would collide with a prior run's leftover rows. The spec files
 * use `[0]` (alpha) for the primary scenario and `[1]` (beta) when
 * a second project is needed.
 */
export function readSeedProjectNames(): string[] {
  return readSeed().project_names;
}

/**
 * Inject the seeded super-admin's access token into the in-memory
 * zustand store on every navigation in this `Page`.
 *
 * Why not just `use.storageState`? The backend's refresh-token
 * rotation policy (CLAUDE.md §품질·보안 §3 — refresh + reuse
 * detection) invalidates the cookie's refresh token the first time
 * a spec consumes it. Subsequent specs that adopt the same
 * `storageState` would fail at the second `/auth/refresh` call.
 *
 * This helper sidesteps the rotation entirely: every fresh page
 * boots with the access token already populated in zustand, no
 * refresh dance, no 401. Works because `apps/frontend/src/lib/api.ts`
 * exposes `window.__setAccessToken` in dev builds.
 *
 * Call once per test (`beforeEach`) so each fresh `Page` carries
 * the hook before any navigation runs.
 */
export async function applyAuthFromSeed(page: Page): Promise<void> {
  const seed = readSeed();
  const token = seed.accessToken ?? null;
  if (token === null) {
    throw new Error(
      "seed file missing accessToken — re-run globalSetup against a dev build",
    );
  }
  await page.addInitScript((tokenValue: string) => {
    const w = window as unknown as Record<string, unknown>;
    const apply = (): boolean => {
      const fn = w.__setAccessToken as
        | ((t: string | null) => void)
        | undefined;
      if (typeof fn === "function") {
        fn(tokenValue);
        return true;
      }
      return false;
    };
    if (!apply()) {
      // The hook is mounted lazily by `apps/frontend/src/lib/api.ts`
      // (after the SPA's first import). Retry on the next microtask
      // tick — that is enough for the typical Vite + React 18 boot.
      const handle = setInterval(() => {
        if (apply()) clearInterval(handle);
      }, 25);
      // Safety net: stop polling after 5s; spec assertions will surface
      // any real auth failure as a test timeout.
      setTimeout(() => clearInterval(handle), 5_000);
    }
  }, token);
}

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
 * Capture only the bounding rectangle of a section identified by testId.
 *
 * For stacked single-page sections (Integrations API keys + Webhooks,
 * Notifications inbox + Preferences, Profile identity + Connected
 * accounts) the 1440×900 viewport fits both sections, so
 * `scrollIntoViewIfNeeded()` is a no-op and a second viewport capture
 * returns the same frame — leaving the two PNGs byte-identical. This
 * helper reads the element's bounding box and feeds a clamped clip
 * rectangle to `page.screenshot()` so the second-section asset is
 * distinct from its sibling.
 */
export async function captureSection(
  page: Page,
  slug: string,
  testId: string,
  padding = 16,
): Promise<void> {
  if (!/^[a-z0-9-]+$/.test(slug)) {
    throw new Error(
      `captureSection: slug "${slug}" must be kebab-case ([a-z0-9-]+)`,
    );
  }
  await hideDevOnlyChrome(page);
  const rect = await page.evaluate(
    ({ id, pad }) => {
      const el = document.querySelector(`[data-testid="${id}"]`);
      if (!el) return null;
      const r = (el as HTMLElement).getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const x = Math.max(0, Math.floor(r.left - pad));
      const y = Math.max(0, Math.floor(r.top - pad));
      const width = Math.max(
        1,
        Math.min(vw - x, Math.ceil(r.width + pad * 2)),
      );
      const height = Math.max(
        1,
        Math.min(vh - y, Math.ceil(r.height + pad * 2)),
      );
      return { x, y, width, height };
    },
    { id: testId, pad: padding },
  );
  if (!rect) {
    throw new Error(
      `captureSection: testId "${testId}" not found on page (slug=${slug})`,
    );
  }
  const out = path.join(SCREENSHOT_DIR, `${slug}.png`);
  await page.screenshot({ path: out, clip: rect, animations: "disabled" });
}
