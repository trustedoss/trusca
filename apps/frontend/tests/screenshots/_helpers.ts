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
 * Insert a `pending` ComponentApproval row directly into PostgreSQL so the
 * approvals queue + drawer have data to display for screenshot capture.
 *
 * The seed pipeline (`seed_e2e_user.py`) does not create approvals — that
 * normally happens lazily when a scan detects a conditional-license
 * component. For docs-only capture we shortcut the pipeline and `INSERT`
 * straight against `component_approvals` for the first project + its first
 * component in the seeded team. Idempotent: a second invocation is a no-op
 * because the partial unique index `ix_component_approvals_unique_open`
 * rejects a duplicate (component, project) pair when one is already
 * pending/under_review, and we swallow that error.
 *
 * We shell out to `docker-compose -f docker-compose.dev.yml exec -T
 * postgres psql` because the Playwright runner already has Docker access
 * (the dev stack must be up for the harness to work) and reaching out
 * directly avoids pulling a node-postgres dependency just for one seed
 * row. The function is a screenshot-only helper — production E2E paths
 * exercise the real API.
 */
export async function ensureSeededPendingApproval(): Promise<void> {
  const seed = readSeed();
  const teamId = (seed as unknown as { team_id?: string }).team_id;
  const projectId = seed.project_ids?.[0];
  if (!teamId || !projectId) {
    throw new Error(
      "ensureSeededPendingApproval: seed.team_id or seed.project_ids[0] missing",
    );
  }
  // CTE picks the first component for the project; ON CONFLICT DO NOTHING
  // covers the partial-unique-index path when an open approval already
  // exists from a previous capture run.
  // scan_components → component_versions → components: scan_components
  // references the *versioned* row; we join up to the canonical component
  // id (`components.id`) which is what `component_approvals.component_id`
  // expects. Order by purl so the picked component is deterministic.
  const sql = `
    WITH first_component AS (
      SELECT c.id AS component_id
      FROM components c
      JOIN component_versions cv ON cv.component_id = c.id
      JOIN scan_components sc ON sc.component_version_id = cv.id
      JOIN scans s ON s.id = sc.scan_id
      WHERE s.project_id = '${projectId}'
      ORDER BY c.purl
      LIMIT 1
    )
    INSERT INTO component_approvals
      (component_id, project_id, team_id, requested_by_user_id, status)
    SELECT component_id, '${projectId}', '${teamId}', NULL, 'pending'
    FROM first_component
    ON CONFLICT DO NOTHING;
  `;
  const { spawnSync } = await import("node:child_process");
  const result = spawnSync(
    "docker-compose",
    [
      "-f",
      "docker-compose.dev.yml",
      "exec",
      "-T",
      "postgres",
      "psql",
      "-U",
      "trustedoss",
      "-d",
      "trustedoss",
      "-v",
      "ON_ERROR_STOP=1",
      "-c",
      sql,
    ],
    { cwd: REPO_ROOT, encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(
      `ensureSeededPendingApproval: psql failed (status=${result.status}): ${result.stderr}`,
    );
  }
}
