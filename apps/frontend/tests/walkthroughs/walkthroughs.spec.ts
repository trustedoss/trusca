/**
 * Marathon bundle 9 (4c) — animated walkthrough specs.
 *
 * Each test in this file records a short user flow as webm. The
 * postprocess step (`make walkthroughs-encode`) converts the webm
 * to mp4 (h264 baseline) + a low-FPS gif preview, both written to
 * `docs-site/static/img/walkthroughs/<slug>.{mp4,gif}`. Markdown
 * pages embed the mp4 via `<video>` and link the gif as a poster
 * fallback.
 *
 * Why two flows (and not the rumored 4–5)? The mp4 + gif pair for a
 * single 12-second flow runs ~600 KB; pile up half a dozen and the
 * docs repo turns into a binary asset graveyard. The two chosen here
 * cover the two most common first-time-user questions:
 *
 *   1. "What does the project detail experience look like?" →
 *      `walkthrough-project-tour`. Click through Overview / Components
 *      / Vulnerabilities / Licenses tabs in sequence so the viewer
 *      sees how the four lenses on a project relate.
 *
 *   2. "How do I drill into a CVE?" →
 *      `walkthrough-cve-triage`. Open the Vulnerabilities tab, click
 *      a row, watch the drawer slide in with the analysis section.
 *
 * Both flows use the seed projects already created by
 * `screenshots/global-setup.ts`. No additional fixtures.
 *
 * Output naming protocol: each test calls
 * `test.info().annotations.push({ type: "slug", description: "<slug>" })`
 * before the recording starts. The postprocess script reads the
 * `*.json` test-result file Playwright drops alongside the video and
 * pairs slug → video to produce deterministic mp4/gif filenames.
 */
import { test } from "@playwright/test";

import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { PortalPage } from "../_harness/PortalPage";
import { applyAuthFromSeed } from "../screenshots/_helpers";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Resolve the seeded project's UUID at spec-time. We avoid the
 * project-list openProjectDetail dance because it is sensitive to
 * pagination + cache state in the dev DB (back-to-back capture runs
 * leave stale projects that push the freshly-seeded one off-screen).
 * Direct ``/projects/<id>`` navigation is locale-agnostic and reliable.
 */
function readPrimaryProjectId(): string {
  const seedPath = path.join(
    __dirname,
    "..",
    "screenshots",
    ".seed.json",
  );
  const raw = JSON.parse(fs.readFileSync(seedPath, "utf8")) as {
    project_ids?: string[];
  };
  const id = raw.project_ids?.[0];
  if (!id) {
    throw new Error("seed missing project_ids[0]");
  }
  return id;
}

test.describe.serial("@walkthroughs", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  // After each test, drop a sidecar `slug.txt` into the test's
  // output directory. The encode script reads this file to map
  // each `video.webm` to its target mp4/gif filename — Playwright's
  // auto-generated directory names truncate long test titles and
  // strip our slug prefix, so a sidecar is the only reliable
  // slug → video mapping.
  test.afterEach(async (_fixtures, testInfo) => {
    const slug = testInfo.annotations.find((a) => a.type === "slug")
      ?.description;
    if (slug) {
      fs.mkdirSync(testInfo.outputDir, { recursive: true });
      const slugPath = path.join(testInfo.outputDir, "slug.txt");
      fs.writeFileSync(slugPath, slug);
    }
  });

  test("walkthrough-project-tour — tab through Overview/Components/Vulns/Licenses", async ({
    page,
  }, testInfo) => {
    testInfo.annotations.push({
      type: "slug",
      description: "walkthrough-project-tour",
    });

    const portal = new PortalPage(page);

    // Start on the project list — gives the viewer a frame of reference
    // before we drill into a specific project. The 800 ms ``waitForTimeout``
    // is deliberate: video readers need a beat to read the page, otherwise
    // tabs flash by faster than the eye can follow.
    await portal.gotoProjects();
    await portal.expectProjectListVisible();
    await page.waitForTimeout(800);

    // Direct navigation by UUID — see readPrimaryProjectId() comment.
    await page.goto(`/projects/${readPrimaryProjectId()}`);
    await portal.expectProjectDetailMounted();
    await page.waitForTimeout(1000);

    await portal.selectTab("components");
    await portal.expectComponentsTabReady();
    await page.waitForTimeout(1200);

    await portal.selectTab("vulnerabilities");
    await portal.expectVulnerabilitiesTabReady();
    await page.waitForTimeout(1200);

    await portal.selectTab("licenses");
    await page.waitForTimeout(1500);
  });

  test("walkthrough-cve-triage — open vulnerabilities tab + drawer", async ({
    page,
  }, testInfo) => {
    testInfo.annotations.push({
      type: "slug",
      description: "walkthrough-cve-triage",
    });

    const portal = new PortalPage(page);

    await portal.gotoProjects();
    await portal.expectProjectListVisible();
    await page.waitForTimeout(600);
    await page.goto(`/projects/${readPrimaryProjectId()}`);
    await portal.expectProjectDetailMounted();
    await page.waitForTimeout(800);

    await portal.selectTab("vulnerabilities");
    await portal.expectVulnerabilitiesTabReady();
    await page.waitForTimeout(1200);

    // Click the first row — drawer slides in. The video pause that follows
    // gives the viewer a moment to register the analysis section before
    // the recording cuts.
    await portal.openFirstVulnerabilityDrawer();
    await page.waitForTimeout(2000);
  });
});
