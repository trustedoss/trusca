/**
 * VEX consume (import) E2E — v2.1 Track A (A3).
 *
 * Drives the project Vulnerabilities tab VEX UI against the docker-compose dev
 * stack. Scenarios:
 *
 *   S1 — Permission gate: a developer sees the VEX import trigger disabled.
 *   S2 — team_admin (super_admin) imports an OpenVEX document; the summary
 *        reports ≥ 1 applied, the affected row gains the VEX marker, the
 *        "VEX-suppressed only" filter narrows to it, and its drawer shows the
 *        VEX provenance (author) — rendered as inert text.
 *
 * All selectors live in `apps/frontend/tests/_harness/PortalPage.ts`; every
 * assertion uses `data-testid` / `data-*` attributes (locale-agnostic).
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 */
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const VULN_COUNT = 12;

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed — bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
  opts: { projectName: string; superAdmin?: boolean },
): Promise<SeedSummary | null> {
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [opts.projectName],
    withScan: true,
    componentCount: VULN_COUNT,
    componentPrefix: "vex",
    vulnerabilityCount: VULN_COUNT,
    superAdmin: opts.superAdmin ?? false,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

/**
 * Reconstruct the seeded `purl_with_version` from a seeded CVE id. The e2e seed
 * (`seed_e2e_user.py`) names finding i with:
 *   - cve_id = `CVE-2099-VLN-<suffix>-<idx:05d>`
 *   - purl   = `pkg:npm/vuln-<idx>-<suffix>@1.0.0`
 * where `suffix` is a 10-char hex and `idx` is zero-padded to 5 digits. We
 * split on the LAST hyphen (idx is digits, suffix is hex without hyphens) to
 * recover both halves, then rebuild the purl deterministically.
 */
function purlForSeededCve(cveId: string): string {
  // CVE-2099-VLN-<suffix>-<idx>
  const m = cveId.match(/^CVE-2099-VLN-([0-9a-f]+)-(\d{5})$/);
  if (!m) {
    throw new Error(`unexpected seeded CVE id shape: ${cveId}`);
  }
  const [, suffix, idx] = m;
  return `pkg:npm/vuln-${idx}-${suffix}@1.0.0`;
}

function writeOpenVexDoc(cveId: string, purl: string): string {
  const doc = {
    "@context": "https://openvex.dev/ns/v0.2.0",
    "@id": "https://example.com/vex/e2e-a3",
    author: "TrustedOSS E2E",
    timestamp: "2026-05-24T00:00:00Z",
    statements: [
      {
        vulnerability: { name: cveId },
        products: [{ "@id": purl }],
        status: "not_affected",
        impact_statement: "Vulnerable code path is not reachable in this build.",
      },
    ],
  };
  const dir = mkdtempSync(join(tmpdir(), "vex-e2e-"));
  const file = join(dir, "openvex.json");
  writeFileSync(file, JSON.stringify(doc), "utf8");
  return file;
}

test.describe("@vex project VEX consume UI", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) a developer sees the VEX import trigger disabled", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, {
      projectName: "vex-dev",
      superAdmin: false,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("vex-dev");
    await portal.selectVulnerabilitiesTab();

    // Export is always available (read); import is gated to team_admin↑.
    await expect(page.getByTestId("vex-export-openvex")).toBeVisible();
    await expect(page.getByTestId("vex-export-cyclonedx")).toBeVisible();
    expect(await portal.isVexImportEnabled()).toBe(false);
  });

  test("S2) team admin imports OpenVEX → marker + filter + drawer provenance", async ({
    page,
  }, testInfo) => {
    // super_admin satisfies the team_admin import gate (and the SPA role gate).
    const seed = await bootstrap(testInfo, page, {
      projectName: "vex-admin",
      superAdmin: true,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("vex-admin");
    await portal.selectVulnerabilitiesTab();

    // Import must be enabled for the admin.
    expect(await portal.isVexImportEnabled()).toBe(true);

    // Pick the first seeded finding and build a VEX doc that targets it.
    const firstCveId = await page
      .getByTestId("vulnerability-row")
      .first()
      .getAttribute("data-cve-id");
    expect(firstCveId).toBeTruthy();
    const cveId = firstCveId as string;
    const purl = purlForSeededCve(cveId);
    const docPath = writeOpenVexDoc(cveId, purl);

    // Import → summary reports at least one applied finding.
    await portal.importVexDocument(docPath);
    const applied = await portal.getVexImportApplied();
    expect(applied).not.toBeNull();
    expect(applied as number).toBeGreaterThanOrEqual(1);
    await portal.closeVexImportDialog();
    await portal.expectVulnerabilitiesTabReady();

    // The page now shows at least one VEX-marked row.
    await expect
      .poll(() => portal.getVexMarkedRowCount())
      .toBeGreaterThanOrEqual(1);

    // The "VEX-suppressed only" filter narrows to the imported finding(s).
    await portal.enableVexSuppressedFilter();
    const rows = page.getByTestId("vulnerability-row");
    await expect(rows.first()).toBeVisible();
    expect(await portal.getVexMarkedRowCount()).toBeGreaterThanOrEqual(1);

    // Open the imported finding's drawer → VEX provenance section is present
    // and carries the document author.
    await portal.openVulnerabilityDrawer(cveId);
    expect(await portal.drawerHasVexProvenance()).toBe(true);
    const author = await portal.getDrawerVexAuthor();
    expect(author).toContain("TrustedOSS E2E");
  });
});
