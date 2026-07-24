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
 * G7 section (feat/g7-conformance, Phase A / A6): seeding with `withG7`
 * appends 4 advisory G7 AI minimum-element checks to the verdict's checks
 * JSONB — statuses pinned from the REAL evaluator over the recorded
 * `aibom-owasp-1_7.json` fixture (see `_G7_SEED_PLAN` in seed_e2e_user.py):
 *
 *   g7-slp-name         slp     pass  source=declared
 *   g7-slp-data-flow    slp     warn  source=na  (human review)
 *   g7-model-hash-value models  warn  source=auto, missing ["<model>"] (offenders)
 *   g7-model-license    models  pass  source=auto, evidence ["Apache-2.0"]
 *
 * → deterministic tally: present 2 / autoTotal 3, advisory 1, review 1,
 *   clusters ["slp", "models"] (canonical registry order). All G7 assertions
 *   go through data attributes so the same verbs pass on EN and KO.
 *
 * E2E is nightly-gated (see .github/workflows/e2e-nightly.yml) — run pre-merge
 * with `gh workflow run e2e-nightly.yml --ref <branch>`.
 */
import { expect, test, type Page, type TestInfo } from "@playwright/test";

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

    // All fourteen catalogue checks render (9 core format checks + the 5
    // verdict-neutral regulatory field checks, CycloneDX only —
    // feat/sbom-conformance-crosswalk).
    expect(await portal.conformanceCheckCount()).toBe(14);

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

test.describe("@sbom-conformance G7 AI minimum-elements section (feat/g7-conformance)", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  /** Seed a G7-carrying sbom scan, log in, open its detail page. */
  async function openG7Scan(
    page: Page,
    testInfo: TestInfo,
    projectName: string,
  ): Promise<PortalPage | null> {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [projectName],
      withG7: true,
    });
    if (seed === null) return null;
    expect(seed.sbom_scan_id, "seed must return an sbom scan id").toBeTruthy();
    expect(seed.g7_check_count, "seed must append the 4 G7 checks").toBe(4);

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoScanDetail(seed.sbom_scan_id as string);
    return portal;
  }

  test("renders the G7 section with the aggregate tally headline", async ({
    page,
  }, testInfo) => {
    const portal = await openG7Scan(page, testInfo, "sbom-g7");
    if (portal === null) return;

    // The core verdict is untouched by the advisory G7 checks (aggregation
    // contract): still warn, still exactly 14 base rows in the base table
    // (9 core + 5 regulatory field checks for a CycloneDX ingest).
    const result = await portal.expectConformancePanel();
    expect(result).toBe("warn");
    expect(await portal.conformanceCheckCount()).toBe(14);

    // G7 headline — 2 of 3 automated elements present; 1 advisory absence;
    // 1 element needs human review. All read from data attributes (EN=KO).
    const tally = await portal.expectG7Section();
    expect(tally).toEqual({ present: 2, autoTotal: 3 });
    await portal.expectG7AdvisoryCount(1);
    await portal.expectG7ReviewCount(1);
  });

  test("groups G7 checks into per-cluster cards in canonical order", async ({
    page,
  }, testInfo) => {
    const portal = await openG7Scan(page, testInfo, "sbom-g7-clusters");
    if (portal === null) return;

    await portal.expectG7Section();

    // The 4 seeded checks span exactly two clusters, rendered in the
    // canonical registry order (slp before models), 2 rows each.
    expect(await portal.g7ClusterIds()).toEqual(["slp", "models"]);
    expect(await portal.g7ClusterCheckCount("slp")).toBe(2);
    expect(await portal.g7ClusterCheckCount("models")).toBe(2);

    // Row-level status + source classification per cluster.
    await portal.expectG7Check("g7-slp-name", {
      status: "pass",
      source: "declared",
    });
    await portal.expectG7Check("g7-model-hash-value", {
      status: "warn",
      source: "auto",
    });
    await portal.expectG7Check("g7-model-license", {
      status: "pass",
      source: "auto",
    });

    // The passing license element surfaces its real evidence chip (mono
    // value straight from the fixture — locale-independent).
    expect(await portal.g7CheckEvidence("g7-model-license")).toEqual([
      "Apache-2.0",
    ]);
  });

  test("marks the no-automated-source row as needing human review", async ({
    page,
  }, testInfo) => {
    const portal = await openG7Scan(page, testInfo, "sbom-g7-review");
    if (portal === null) return;

    await portal.expectG7Section();

    // The source=na element is warn + "na" — visibly distinct from the
    // automated advisory warn (g7-model-hash-value, source=auto) via the
    // source badge, the review tally badge, and the explanatory note.
    await portal.expectG7Check("g7-slp-data-flow", {
      status: "warn",
      source: "na",
    });
    await portal.expectG7ReviewCount(1);
  });

  test("does not render the G7 section for a core-only verdict", async ({
    page,
  }, testInfo) => {
    // Plain withSbom seed — no g7-* checks in the verdict.
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["sbom-g7-neg"],
      withSbom: true,
    });
    if (seed === null) return;
    expect(seed.sbom_scan_id, "seed must return an sbom scan id").toBeTruthy();
    expect(seed.g7_check_count ?? 0).toBe(0);

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoScanDetail(seed.sbom_scan_id as string);

    // Panel renders (core verdict exists) but the G7 section does not.
    await portal.expectConformancePanel();
    await portal.expectNoG7Section();
  });
});
