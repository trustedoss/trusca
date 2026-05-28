/**
 * UX audit — capture our 8 product screens for the competitive UX audit.
 *
 * SoT plan: `docs/ux/competitive-audit-plan-2026-05-27.md` (Phase A).
 *
 * Dual-purpose: outputs feed both the audit matrix and Docusaurus screenshots
 * (W6-#43c, v2.4.0 release-notes, W7-PR-A/B). See plan §13.
 *
 * Capture rules:
 *  - viewport 1440×900, deviceScaleFactor 2 (Retina-quality PNG)
 *  - UI language forced to English (toggle if KO is current)
 *  - Two PNG per screen: viewport-only (default screenshot) + fullPage
 *  - Fixed dataset: `fx-maven-node` (69 components, 11 CVEs)
 *  - Output: ../../../docs/ux/screens/ours/<semantic-name>.png
 *  - Metadata written to ../../../docs/ux/raw/capture-metadata.md
 *
 * Run: cd apps/frontend && npx playwright test ux-audit/capture-ours
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect, type Page, type Locator } from "@playwright/test";

import { AuthHarness } from "../../_harness/auth";
import { PortalPage } from "../../_harness/PortalPage";

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORTAL_BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const API_BASE = process.env.API_BASE ?? "http://localhost:8000";
const EMAIL = "frontend-admin@demo.trustedoss.dev";
const PASSWORD = "DemoTest2026!";
const FIXTURE_SLUG = "fx-maven-node";

const OUT_ROOT = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "..",
  "..",
  "docs",
  "ux",
  "screens",
  "ours",
);
const META_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "..",
  "..",
  "docs",
  "ux",
  "raw",
  "capture-metadata.md",
);

test.use({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});

// Single worker (Playwright default config already enforces) — screenshot runs
// in one browser session to keep auth + language toggle state coherent.
test.describe.configure({ mode: "serial" });

interface Capture {
  id: string; // O1..O8 (for audit matrix only)
  name: string; // semantic filename without .png
  route: (projectId: string) => string;
  prep?: (page: Page) => Promise<void>;
  // Optional locator to wait on before capture, in addition to AppShell sentinel
  ready?: (page: Page) => Locator | null;
  // Skip the full-page variant (e.g. drawer captures don't need it)
  skipFullPage?: boolean;
}

const CAPTURES: Capture[] = [
  {
    id: "O1",
    name: "dashboard",
    route: () => "/",
    ready: (p) => p.getByTestId("app-sidebar"),
  },
  {
    id: "O2",
    name: "project-list",
    route: () => "/projects",
    ready: (p) => p.getByTestId("project-list-page"),
  },
  {
    id: "O3",
    name: "project-detail-overview",
    route: (id) => `/projects/${id}?tab=overview`,
    ready: (p) => p.getByTestId("app-sidebar"),
  },
  {
    id: "O4",
    name: "project-detail-components",
    route: (id) => `/projects/${id}?tab=components`,
    ready: (p) => p.getByTestId("app-sidebar"),
  },
  {
    id: "O5",
    name: "project-detail-vulnerabilities",
    route: (id) => `/projects/${id}?tab=vulnerabilities`,
    ready: (p) => p.getByTestId("app-sidebar"),
  },
  {
    id: "O6",
    name: "drawer-vulnerability-detail",
    route: (id) => `/projects/${id}?tab=vulnerabilities`,
    skipFullPage: true,
    prep: async (page) => {
      // Click the first vuln row to open its drawer. Selector is broad: the
      // app uses `data-testid="vulnerability-row"` on every row.
      const firstRow = page.getByTestId("vulnerability-row").first();
      await firstRow.waitFor({ state: "visible", timeout: 10_000 });
      await firstRow.click();
      // Wait for the drawer to mount. Common testids tried in order.
      const drawer = page
        .getByTestId("vulnerability-drawer")
        .or(page.getByTestId("vuln-detail-drawer"))
        .or(page.getByRole("dialog"));
      await drawer.first().waitFor({ state: "visible", timeout: 10_000 });
      // Let any sub-data finish loading
      await page.waitForTimeout(800);
    },
  },
  {
    id: "O7",
    name: "project-detail-reports",
    route: (id) => `/projects/${id}?tab=reports`,
    ready: (p) => p.getByTestId("app-sidebar"),
  },
  {
    id: "O8",
    name: "scans-queue",
    route: () => "/scans",
    ready: (p) => p.getByTestId("app-sidebar"),
  },
];

const KO_CORE: string[] = ["dashboard", "project-detail-overview", "project-detail-vulnerabilities"];

async function discoverProjectId(page: Page): Promise<string> {
  // Use Playwright's APIRequestContext (shares no cookies with page — we
  // POST /auth/login fresh to get a token, then GET /v1/projects).
  const loginRes = await page.request.post(`${API_BASE}/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
    headers: { "Content-Type": "application/json" },
  });
  expect(loginRes.ok(), "API login").toBeTruthy();
  const token = (await loginRes.json()).access_token as string;

  const meRes = await page.request.get(`${API_BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(meRes.ok(), "API /auth/me").toBeTruthy();
  const memberships = (await meRes.json()).memberships ?? [];
  const teamId = memberships[0]?.team_id;
  expect(teamId, "team_id present").toBeTruthy();

  const listRes = await page.request.get(
    `${API_BASE}/v1/projects?team_id=${teamId}&q=${FIXTURE_SLUG}&size=10`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(listRes.ok(), "list projects").toBeTruthy();
  const items = (await listRes.json()).items ?? [];
  const proj = items.find((p: { slug: string; id: string }) => p.slug === FIXTURE_SLUG);
  expect(proj, `project ${FIXTURE_SLUG} found`).toBeTruthy();
  return proj.id as string;
}

async function ensureEnglish(portal: PortalPage): Promise<void> {
  const current = await portal.currentLanguage();
  if (current !== "en") {
    await portal.toggleLanguage();
    // Wait for re-render
    await portal.page.waitForTimeout(300);
    const next = await portal.currentLanguage();
    expect(next).toBe("en");
  }
}

async function ensureKorean(portal: PortalPage): Promise<void> {
  const current = await portal.currentLanguage();
  if (current !== "ko") {
    await portal.toggleLanguage();
    await portal.page.waitForTimeout(300);
    const next = await portal.currentLanguage();
    expect(next).toBe("ko");
  }
}

async function captureScreen(
  page: Page,
  portal: PortalPage,
  capture: Capture,
  projectId: string,
  subdir: string = "",
): Promise<{ viewport: string; fullPage: string | null }> {
  const url = `${PORTAL_BASE}${capture.route(projectId)}`;
  await page.goto(url);

  // AppShell sentinel
  await portal.expectMounted();

  // Optional readiness wait (per-capture testid)
  if (capture.ready) {
    const loc = capture.ready(page);
    if (loc) {
      await loc.waitFor({ state: "visible", timeout: 10_000 });
    }
  }

  // Let async list/chart data settle
  await page.waitForTimeout(1200);

  // Per-capture prep (e.g. open drawer)
  if (capture.prep) {
    await capture.prep(page);
  }

  const outDir = subdir ? path.join(OUT_ROOT, subdir) : OUT_ROOT;
  fs.mkdirSync(outDir, { recursive: true });

  const vpPath = path.join(outDir, `${capture.name}.png`);
  await page.screenshot({ path: vpPath, fullPage: false });

  let fullPath: string | null = null;
  if (!capture.skipFullPage) {
    fullPath = path.join(outDir, `${capture.name}-full.png`);
    await page.screenshot({ path: fullPath, fullPage: true });
  }
  return { viewport: vpPath, fullPage: fullPath };
}

test("capture our 8 screens (EN) + KO core 3", async ({ page }) => {
  test.setTimeout(180_000);

  const portal = new PortalPage(page, PORTAL_BASE);
  const auth = new AuthHarness(page, PORTAL_BASE);

  // 1. Discover project id (independent API session)
  const projectId = await discoverProjectId(page);

  // 2. UI login as frontend-admin. We do not call `auth.login` because its
  // `expectLoggedIn` asserts URL===`/`, but the app redirects to `/projects`
  // for some roles — we only need an authenticated session, not a specific
  // landing route.
  await auth.clearAuthState();
  await auth.gotoLogin();
  await page.getByTestId("login-email").fill(EMAIL);
  await page.getByTestId("login-password").fill(PASSWORD);
  await page.getByTestId("login-submit").click();
  await page
    .getByTestId("app-sidebar")
    .waitFor({ state: "visible", timeout: 15_000 });

  // 3. Force English UI
  await ensureEnglish(portal);

  // 4. Capture all 8 screens in EN
  const results: Array<{ id: string; name: string; viewport: string; fullPage: string | null }> = [];
  for (const cap of CAPTURES) {
    const out = await captureScreen(page, portal, cap, projectId);
    results.push({ id: cap.id, name: cap.name, ...out });
  }

  // 5. KO core 3 captures
  await ensureKorean(portal);
  const koResults: Array<{ name: string; viewport: string }> = [];
  for (const cap of CAPTURES.filter((c) => KO_CORE.includes(c.name))) {
    const out = await captureScreen(page, portal, cap, projectId, "ko");
    koResults.push({ name: cap.name, viewport: out.viewport });
  }

  // 6. Write capture metadata
  const now = new Date().toISOString();

  // Read git SHA from filesystem (the repo this spec lives in)
  const gitHeadPath = path.resolve(__dirname, "..", "..", "..", "..", "..", ".git", "HEAD");
  let gitSha = "unknown";
  try {
    const head = fs.readFileSync(gitHeadPath, "utf8").trim();
    if (head.startsWith("ref: ")) {
      const refPath = path.resolve(
        __dirname,
        "..",
        "..",
        "..",
        "..",
        "..",
        ".git",
        head.slice(5),
      );
      gitSha = fs.readFileSync(refPath, "utf8").trim().slice(0, 12);
    } else {
      gitSha = head.slice(0, 12);
    }
  } catch {
    /* keep "unknown" */
  }

  fs.mkdirSync(path.dirname(META_PATH), { recursive: true });
  const md = [
    "# scan-bench UX capture metadata\n",
    `- captured_at: ${now}`,
    `- git_sha: ${gitSha}`,
    `- account: ${EMAIL} (team_admin)`,
    `- dataset_project: ${FIXTURE_SLUG} (project_id ${projectId})`,
    `- viewport: 1440×900`,
    `- deviceScaleFactor: 2 (Retina)`,
    `- ui_language: en (primary) + ko (core 3)`,
    `- portal_base: ${PORTAL_BASE}`,
    `- api_base: ${API_BASE}`,
    "",
    "## Re-capture",
    "```",
    "cd apps/frontend && npx playwright test ux-audit/capture-ours",
    "```",
    "",
    "## EN captures",
    "",
    ...results.map(
      (r) =>
        `- **${r.id}** \`${r.name}.png\`${r.fullPage ? ` + \`${r.name}-full.png\`` : ""}`,
    ),
    "",
    "## KO captures",
    "",
    ...koResults.map((r) => `- \`ko/${r.name}.png\``),
    "",
  ].join("\n");
  fs.writeFileSync(META_PATH, md, "utf8");

  // Sanity: every expected file exists
  for (const r of results) {
    expect(fs.existsSync(r.viewport), `viewport file ${r.viewport}`).toBeTruthy();
    if (r.fullPage) {
      expect(fs.existsSync(r.fullPage), `full file ${r.fullPage}`).toBeTruthy();
    }
  }
  for (const r of koResults) {
    expect(fs.existsSync(r.viewport), `KO file ${r.viewport}`).toBeTruthy();
  }
});
