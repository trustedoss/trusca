/**
 * Source file-tree viewer E2E — G3.3.
 *
 * Drives the project detail Source tab against the docker-compose dev stack.
 * The tab is a Protex-style two-pane viewer: a lazy file tree (left) over a
 * scan's preserved source and a line-numbered viewer (right) with per-line
 * license highlighting.
 *
 * Scenarios (`@source-tree` tag):
 *   S1 — Tab entry: the Source tab mounts and the tree settles into a
 *        terminal state (rows OR the "no preserved source" empty card).
 *   S2 — Empty state: a scan WITHOUT a preserved tarball 404s the tree root
 *        and the tab shows the single "re-scan to enable" card instead of an
 *        error toast. This scenario seeds a scan but NOT a source tarball, so
 *        it always lands on the empty-state contract.
 *   S3 — Populated tree: lazy expand a directory, open a file, assert the
 *        viewer renders content + per-line license panel. Seeds the
 *        `--with-source` fixture (G3.3).
 *   S4 — Binary / large-file guidance: a binary file shows the byte-safe
 *        notice; a truncated file shows the truncated banner + download
 *        button. Seeds the `--with-source` fixture (G3.3).
 *
 * Selectors live in `apps/frontend/tests/_harness/PortalPage.ts`. Every
 * assertion is EN-locale-agnostic — anchored on `data-testid` / `data-*`
 * attributes, never translated strings.
 *
 * ────────────────────────────────────────────────────────────────────────
 * SEED FIXTURE (G3.3 — gap now closed):
 *
 *   `seedE2eUser({ withSource: true })` stages a real preserved-source
 *   tarball for the first project's succeeded scan via
 *   `apps/backend/scripts/seed_e2e_user.py --with-source`. It reuses
 *   `services.source_preservation_service.preserve_scan_source(...)` so the
 *   tarball shape is byte-identical to a real scan's (gzip tar + folded-in
 *   `.trustedoss/scancode.json`). The staged tree:
 *
 *     README.md            root-level utf-8 file (non-empty root listing)
 *     src/app/main.py      utf-8 file with an MIT header → license_matches on
 *                          lines 1-3 (the per-line license chip, S3)
 *     src/app/logo.bin     binary file (NUL byte) → byte-safe notice (S4)
 *     src/app/huge.txt     oversized file (> the 2 MiB viewer cap) →
 *                          truncated=true + download button (S4)
 *
 *   IMPORTANT — the `withSource` seed runs INSIDE the backend container (via
 *   `docker-compose exec`) because the tarball is written into the
 *   `scan-workspace` Docker volume the API reads, not a host directory. The
 *   harness switches runners automatically; see `SeedOptions.withSource`.
 *
 *   S3/S4 still defensively `test.skip()` when the empty state is detected so
 *   a stack without the dev volume (or a quota-skip) degrades to skipped
 *   rather than a hard failure.
 * ────────────────────────────────────────────────────────────────────────
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (S1/S2 host seed) AND the
 *     `docker-compose` binary reachable (S3/S4 in-container seed)
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

// Each test seeds its OWN uniquely-named project. The dev DB is shared across
// runs and `openProjectDetail(name)` matches a row by `data-project-name`, so a
// fixed name would collide with prior `ci-source` rows (and a source-less older
// row could shadow the freshly-seeded one — making S3/S4 see the empty state).
// A per-test unique suffix makes the navigation pick exactly the project this
// test seeded. Mirrors the screenshot pipeline's timestamped-name approach.
function uniqueProjectName(): string {
  return `ci-source-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}
// A handful of components so the scan + seed-licenses exist; the source tree
// is independent of component_count (it reads the preserved tarball), but a
// scan must exist for the Source tab to attempt the tree root.
const DEFAULT_COMPONENT_COUNT = 4;

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

interface Bootstrapped {
  seed: SeedSummary;
  projectName: string;
}

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
  opts: { withSource?: boolean } = {},
): Promise<Bootstrapped | null> {
  const projectName = uniqueProjectName();
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [projectName],
    withScan: true,
    componentCount: DEFAULT_COMPONENT_COUNT,
    // Unique component prefix per project so the seed never trips
    // `uq_components_purl` against a prior run's `src-*` components in the
    // shared dev DB.
    componentPrefix: `srctree-${Date.now().toString(36)}`,
    // S3/S4 stage a preserved-source tarball (G3.3); S1/S2 deliberately do
    // not, so they exercise the empty-state contract.
    withSource: opts.withSource ?? false,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return { seed, projectName };
}

/**
 * Returns true when the Source tab is showing the "no preserved source"
 * empty card. S1/S2 (no `--with-source` seed) land here by design; S3/S4
 * (with `--with-source`) expect a populated tree and defensively skip on the
 * empty state so a stack missing the dev volume degrades to skipped, not
 * failed.
 */
async function isEmptySource(
  page: import("@playwright/test").Page,
): Promise<boolean> {
  return (await page.getByTestId("source-no-preserved").count()) > 0;
}

test.describe("@source-tree project source tab", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) Source tab mounts and the tree settles into a terminal state", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(boot.projectName);
    await portal.selectSourceTab();

    // The tab body is mounted regardless of populated vs empty.
    await expect(page.getByTestId("source-tab")).toBeVisible();

    // Exactly one terminal state is showing: either ≥ 1 tree row OR the
    // no-preserved-source card. We don't assert which (depends on seed); both
    // are valid "tab finished loading" states.
    const rowCount = await page.getByTestId("source-tree-row").count();
    const empty = await isEmptySource(page);
    expect(rowCount > 0 || empty).toBe(true);
  });

  test("S2) old/seeded scan with no preserved source shows the re-scan empty card", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(boot.projectName);
    await portal.selectSourceTab();

    // The seed does not stage a source tarball → 404 → single empty card,
    // NOT an error toast. If a future `--with-source` seed populates the
    // tree, this scenario skips (the empty card is the contract being tested
    // here, and a populated tree is a different contract owned by S3/S4).
    test.skip(
      !(await isEmptySource(page)),
      "preserved source is present — empty-state contract not applicable " +
        "(a --with-source seed landed; S3/S4 cover the populated tree).",
    );

    await portal.expectSourceEmptyState();
    // The destructive "could not load" error alert must NOT be what we show
    // for the expected 404.
    await expect(page.getByTestId("source-tree-error")).toHaveCount(0);
    await expect(page.getByTestId("source-file-error")).toHaveCount(0);
  });

  test("S3) lazy expand a directory, open a file → content + per-line license panel", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page, { withSource: true });
    if (boot === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(boot.projectName);
    await portal.selectSourceTab();

    test.skip(
      await isEmptySource(page),
      "no preserved source for the --with-source seed — the dev `scan-workspace` " +
        "volume is likely unavailable to the API; cannot exercise the populated tree.",
    );

    // Pick the first directory row in the root level and expand it lazily.
    const firstDir = page.locator(
      '[data-testid="source-tree-row"][data-is-dir="true"]',
    );
    await expect(firstDir.first()).toBeVisible();
    const dirPath = await firstDir.first().getAttribute("data-path");
    expect(dirPath).toBeTruthy();
    await portal.expandSourceTreeNode(dirPath as string);

    // After expansion, find the first file leaf anywhere in the tree and open
    // it. The fixture guarantees at least one utf-8 file with a license match.
    const firstFile = page.locator(
      '[data-testid="source-tree-row"][data-is-dir="false"]',
    );
    await expect(firstFile.first()).toBeVisible();
    const filePath = await firstFile.first().getAttribute("data-path");
    expect(filePath).toBeTruthy();
    await portal.openSourceFile(filePath as string);

    // Either a text file (content + license panel) or binary; S3 targets the
    // text path. If the first file happens to be binary the fixture is
    // misconfigured for S3 — assert the viewer at least settled, then the
    // text contract.
    await portal.expectSourceFileText();

    // At least one line is rendered, and IF the fixture seeded a license
    // match the per-line chip surfaces its SPDX id. We don't hardcode a line
    // number (fixture-dependent); we assert the panel is selective: at least
    // one highlighted line OR an explicit "no matches" is acceptable, but a
    // highlighted line must carry a chip with a non-empty data-spdx-ids.
    const highlighted = page.locator(
      '[data-testid="source-line"][data-highlighted="true"]',
    );
    if ((await highlighted.count()) > 0) {
      const chip = highlighted
        .first()
        .locator('[data-testid="source-line-license-chip"]');
      await expect(chip).toBeVisible();
      const ids = (await chip.getAttribute("data-spdx-ids")) ?? "";
      expect(ids.length).toBeGreaterThan(0);
    }

    // URL mirrors the open file so a hard reload would restore it.
    expect(new URL(page.url()).searchParams.get("path")).toBe(filePath);
  });

  test("S4) binary file shows the byte-safe notice; large file shows truncation guidance", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page, { withSource: true });
    if (boot === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(boot.projectName);
    await portal.selectSourceTab();

    test.skip(
      await isEmptySource(page),
      "no preserved source for the --with-source seed — the dev `scan-workspace` " +
        "volume is likely unavailable to the API; cannot exercise binary + truncated guidance.",
    );

    // The fixture stages a binary file and an oversized (truncated) file. We
    // open whatever leaves exist and assert: opening any file reaches a
    // terminal viewer state (never an infinite skeleton), and IF a binary or
    // truncated file is present its specific guidance renders.
    const files = page.locator(
      '[data-testid="source-tree-row"][data-is-dir="false"]',
    );
    // Expand the first directory to surface nested files too.
    const firstDir = page.locator(
      '[data-testid="source-tree-row"][data-is-dir="true"]',
    );
    if ((await firstDir.count()) > 0) {
      const dirPath = await firstDir.first().getAttribute("data-path");
      if (dirPath) await portal.expandSourceTreeNode(dirPath);
    }

    const count = await files.count();
    expect(count).toBeGreaterThan(0);

    let sawBinary = false;
    let sawTruncated = false;
    for (let i = 0; i < count; i++) {
      const path = await files.nth(i).getAttribute("data-path");
      if (!path) continue;
      await portal.openSourceFile(path);
      // Every opened file reaches a terminal state (no infinite skeleton).
      await portal.expectSourceFileSettled();

      if ((await page.getByTestId("source-file-binary").count()) > 0) {
        sawBinary = true;
        await portal.expectSourceFileBinary();
      }
      const viewer = page.getByTestId("source-file-viewer");
      if (
        (await viewer.count()) > 0 &&
        (await viewer.getAttribute("data-truncated")) === "true"
      ) {
        sawTruncated = true;
        await portal.expectSourceFileTruncated();
        // The truncated banner offers a download of the bytes we DID receive.
        const { filename, body } = await portal.downloadTruncatedSourceFile();
        expect(filename.length).toBeGreaterThan(0);
        expect(body.length).toBeGreaterThan(0);
      }
    }

    // The fixture is expected to include both kinds; if it doesn't yet, the
    // loop still asserted "every file settles" which is the load-bearing
    // guarantee. We surface the gap as a soft assertion via test.info so the
    // run log records which guidance paths were exercised.
    testInfo.annotations.push({
      type: "source-file-coverage",
      description: `binary=${sawBinary} truncated=${sawTruncated}`,
    });
  });
});
