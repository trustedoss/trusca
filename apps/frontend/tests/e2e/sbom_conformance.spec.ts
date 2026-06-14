/**
 * @sbom-conformance — received-SBOM conformance panel on /scans/:scanId (model 3).
 *
 * Seeds a kind='sbom' scan + its conformance verdict (via `withSbom`), opens the
 * scan detail page, and asserts the conformance panel: the pass/warn/fail badge,
 * the per-check table, and the summary. The seeded SBOM is a small CycloneDX with
 * full PURLs + a dependency edge + declared licenses but NO component hashes, so
 * the scorer returns a deterministic **warn** (every mandatory check passes; the
 * recommended hash-coverage check warns).
 *
 * A second scenario confirms a non-sbom (source) scan shows NO panel.
 *
 * E2E is nightly-gated (see .github/workflows/e2e-nightly.yml) — run pre-merge
 * with `gh workflow run e2e-nightly.yml --ref <branch>`.
 */
import { expect, test, type TestInfo } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedOptions, type SeedSummary } from "../_harness/seed";

function tryAcquireSeed(
  testInfo: TestInfo,
  opts: SeedOptions,
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed — skipping (CI seeds against the dev stack): ${String(err)}`,
    );
    return null;
  }
}

test.describe("@sbom-conformance received-SBOM conformance panel (model 3)", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("renders the verdict badge + per-check table for an sbom scan", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["sbom-conf"],
      withSbom: true,
    });
    if (seed === null) return;
    expect(seed.sbom_scan_id, "seed must return an sbom scan id").toBeTruthy();

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    await portal.gotoScanDetail(seed.sbom_scan_id as string);

    // Deterministic verdict: full PURLs + graph + licenses, no hashes → warn.
    const result = await portal.expectConformancePanel();
    expect(result).toBe("warn");

    // All nine catalogue checks render.
    expect(await portal.conformanceCheckCount()).toBe(9);

    // Mandatory checks pass; the recommended hash check warns.
    await portal.expectConformanceCheck("purl", "pass");
    await portal.expectConformanceCheck("transitive", "pass");
    await portal.expectConformanceCheck("hash", "warn");

    // Summary reflects the detected format.
    expect(await portal.conformanceSummaryValue("conformance-source-format")).toBe(
      "cyclonedx",
    );
  });

  test("does not render the panel for a non-sbom (source) scan", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["sbom-conf-neg"],
      withScan: true,
      withSbom: true,
    });
    if (seed === null) return;
    const sourceScanId = (seed.scan_ids ?? []).find(
      (id) => id !== seed.sbom_scan_id,
    );
    expect(sourceScanId, "seed must include a non-sbom scan").toBeTruthy();

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    await portal.gotoScanDetail(sourceScanId as string);
    await portal.expectNoConformancePanel();
  });
});
