/**
 * G0-6 — narrow-viewport overflow gate.
 *
 * Why this exists
 * ---------------
 * The W14 global bar could not fit a phone. Roughly 450 px of
 * non-shrinkable controls sat in a row with no wrap and no overflow
 * handling, so on a 390 px screen the surplus spilled off the right edge
 * and took the sign-out button — the app's only one — with it. Nobody saw
 * it because nothing in the repository rendered below 800 px: every
 * Playwright config pins 1440×900, and the two narrow e2e cases stop at
 * 800. The independent review found it by reading CSS, not by running
 * anything.
 *
 * Why an overflow assertion rather than phone baselines
 * ----------------------------------------------------
 * Eight more screenshots at 390 px would double the baseline set and its
 * maintenance, and would still only catch drift on the exact screens
 * captured. What actually went wrong is a *category* — content wider than
 * its container — and that is cheap to assert directly, with nothing to
 * keep up to date. A layout can change freely; it just may not overflow.
 *
 * 390 px is the worst realistic case (iPhone 12/13/14 mini and the common
 * Android logical width). Between it and `lg` (1024 px) the same drawer
 * layout only gains room, so one width covers the band.
 *
 * Why the numbers are inflated before the second pass (G0-9)
 * ----------------------------------------------------------
 * This gate once passed on CI and failed locally, for the same commit. The
 * trend panel's `sr-only` table would not shrink below its min-content
 * width and pushed the dashboard sideways at 390 px — but the CI seed wrote
 * single-digit counts, so the table was narrow enough to fit and the run
 * was green. The defect reached `main` and surfaced on the next PR, whose
 * data happened to be larger.
 *
 * A gate whose verdict depends on how much the seed happened to write means
 * "passes on this data", and data changes. So each screen is judged twice:
 * once as it renders, and once with every number widened to a fixed worst
 * case. The second pass is the one that cannot drift — it asserts the same
 * layout regardless of what the fixture holds, which is what "no overflow
 * at 390 px" was always meant to mean.
 */
import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { AuthHarness } from "../_harness/auth";
import { AUTHENTICATED_SCREENS } from "../_harness/representativeScreens";
import { applyAuthFromSeed } from "../screenshots/_helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUDGET_PATH = path.join(__dirname, "narrow-baseline.json");

/**
 * Frozen spill counts, per screen — see `expectSpillsWithinBudget`.
 *
 * Refreshing it is a CI-only operation, for the same reason the
 * accessibility baseline is (G0-9): a local run measures the local seed,
 * and this gate has already produced opposite verdicts locally and on the
 * runner for the same commit.
 *
 *   gh workflow run ui-gates.yml --ref <branch> -f update_baselines=true
 *
 * Expect these numbers NOT to reproduce locally — the two seeds differ, and
 * on `projects-list` they differ by a lot (13/24 on the runner against 5/43
 * here when this was recorded). A local run of this spec tells you whether
 * a screen got dramatically worse; the runner's tells you whether it can
 * merge.
 */
const BUDGET: Record<string, number> = fs.existsSync(BUDGET_PATH)
  ? (JSON.parse(fs.readFileSync(BUDGET_PATH, "utf8")) as Record<string, number>)
  : {};

const UPDATE_BUDGET = process.env.NARROW_UPDATE_BASELINE === "1";
const CI = process.env.CI === "true" || process.env.CI === "1";

if (UPDATE_BUDGET && !CI && process.env.ALLOW_LOCAL_NARROW_BASELINE !== "1") {
  throw new Error(
    "Refusing to write a narrow-viewport baseline outside CI. This gate has " +
      "already passed on the runner and failed locally for one commit, " +
      "because the two seeds render different amounts of text.\n\n" +
      "  gh workflow run ui-gates.yml --ref <branch> -f update_baselines=true\n\n" +
      "then commit the JSON from the `visual-baselines` artifact.",
  );
}

/**
 * Where an update run accumulates counts.
 *
 * On disk, one file per screen, rather than in a module-level object:
 * Playwright restarts the worker process after a failed test, which resets
 * anything held in memory. The first attempt at this baseline came back
 * missing three screens for exactly that reason — the run that writes the
 * budget is also the run most likely to have a failure in it. Same shape as
 * `a11y.spec.ts`, and for the same reason.
 */
const OBSERVED_DIR = path.join(__dirname, ".output", "spills");

/**
 * Write what this screen spilled, on every run rather than only on update.
 *
 * The budget records a number, and the failure message shows the first ten
 * offenders — which means that as long as the gate is green, nothing tells
 * you what the frozen 98 actually consists of. Paying the budget down starts
 * with knowing that, and re-deriving it meant hand-patching this spec each
 * time. The directory is Playwright's `outputDir` and gitignored, so this
 * costs a file per screen and nothing in review.
 */
function recordSpills(screen: string, spills: string[]): void {
  fs.mkdirSync(OBSERVED_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(OBSERVED_DIR, `${screen.replace(/[^a-z0-9]+/gi, "-")}.json`),
    JSON.stringify({ screen, count: spills.length, spills }, null, 2),
  );
}

/**
 * Assert nothing is wider than what contains it.
 *
 * Two checks, because they fail for different reasons. The document-level
 * one catches a page that scrolls sideways — the symptom a user notices.
 * The element-level one catches a flex row whose children overflow while
 * the page itself still fits, which is exactly the shape of the W14 bug:
 * `ml-auto` collapsed, the surplus went off-screen, and whether the page
 * grew depended on ancestor overflow rules.
 */
async function expectNoHorizontalOverflow(page: Page, screen: string) {
  const doc = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(
    doc.scrollWidth,
    `${screen}: the page scrolls sideways at 390 px ` +
      `(${doc.scrollWidth} > ${doc.clientWidth})`,
  ).toBeLessThanOrEqual(doc.clientWidth + 1);

  const bar = page.getByTestId("app-topbar");
  if (await bar.count()) {
    const overflow = await bar.evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));
    expect(
      overflow.scrollWidth,
      `${screen}: the global bar overflows its own width at 390 px ` +
        `(${overflow.scrollWidth} > ${overflow.clientWidth}) — something in ` +
        `it will be pushed off screen`,
    ).toBeLessThanOrEqual(overflow.clientWidth + 1);
  }

  await expectSpillsWithinBudget(page, screen);
}

/**
 * Every element whose content is wider than the box it renders in.
 *
 * The document-level check above cannot fail on the authenticated screens.
 * The shell's scroll container clips horizontally, so `documentElement`
 * stays exactly 390 px no matter what is inside it — measured directly:
 * widening 142 numbers to 23 digits each moved it by zero pixels. A gate
 * whose primary assertion is structurally incapable of failing is worse
 * than an absent one, because the green tick is read as coverage.
 *
 * What a user actually experiences when a phone layout breaks is content
 * cut off, or pushed out of a container that was never meant to scroll. So
 * the assertion moved down a level: any element that is not itself a
 * scroller, whose content exceeds its own width.
 *
 * Two exclusions, both for things that are decisions rather than defects:
 *
 *   overflow-x: auto | scroll   declared scrollable
 *   text-overflow: ellipsis     declared truncatable — the row link that
 *                               holds 135 px of project name in 56 px is
 *                               doing exactly what `truncate` asks, and
 *                               the user sees the ellipsis
 *
 * `overflow-x: hidden` without an ellipsis is *not* excluded. It clips
 * silently, and content a phone user cannot reach or know about is the
 * shape of the `sr-only` table this gate caught once already.
 */
async function findSpills(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("*"))) {
      // Zero-width elements are not rendered; 1 px ones are `sr-only`, which
      // shrinks a box to a pixel and clips it precisely so sighted users
      // never see the content. Its content is wider than its box BY DESIGN,
      // so counting it reported the screen-reader table on the dashboard,
      // the visually-hidden labels in the bar, and every future one, as
      // layout defects.
      //
      // Excluding them does not give back the defect this gate caught in
      // G0-9. That was an `sr-only` TABLE refusing to shrink below its
      // min-content width and pushing the page sideways — the damage showed
      // up on its ANCESTORS, which are not `sr-only` and are still measured.
      if (el.clientWidth <= 1) continue;
      // The TanStack Query devtools launcher is dev-server-only chrome that
      // ships to nobody. The a11y scan excludes it for the same reason; this
      // gate was counting it on all eight screens.
      if (el.closest(".tsqd-parent-container, [class*='tsqd-']")) continue;
      const style = getComputedStyle(el);
      if (style.overflowX === "auto" || style.overflowX === "scroll") continue;
      if (style.textOverflow === "ellipsis") continue;
      // A pixel of slack: sub-pixel layout rounds against us on fractional
      // widths and would otherwise report every flex row on the page.
      if (el.scrollWidth <= el.clientWidth + 1) continue;
      // The content has to be wider than the PHONE, not merely wider than
      // its own box. Those are different complaints and only the first is
      // this gate's:
      //
      //   44 px inside a 40 px button   a notification badge on the corner,
      //                                 drawn outside and fully visible —
      //                                 the design, not a defect
      //   767 px inside a 390 px page   content the phone cannot show
      //
      // Without this line the gate reported the badge on seven screens and
      // every fixed-width table header cell, which is how a budget grows to
      // 57 and stops being read.
      const viewport = document.documentElement.clientWidth;
      if (el.scrollWidth <= viewport) continue;

      const id = el.dataset.testid ? `[${el.dataset.testid}]` : "";
      const cls = String(el.className).split(/\s+/).slice(0, 3).join(".");
      out.push(
        `${el.tagName.toLowerCase()}${id}${cls ? "." + cls : ""} ` +
          `(${el.clientWidth} < ${el.scrollWidth})`,
      );
    }
    return out;
  });
}

/**
 * Hold each screen to its recorded number of spills.
 *
 * Zero is not reachable today — the element-level check finds real breakage
 * on every authenticated screen, from a project detail page holding 758 px
 * of content in 390 to a dashboard grid at 499. Demanding zero now would
 * mean either a reactive-layout campaign bolted onto a gate change, or the
 * gate being switched off; the project has answered this question before,
 * in `a11y-baseline.json`, and the answer is a budget that only travels
 * downward.
 *
 * The repairs belong with W18, which is already going to touch these
 * layouts. What this buys in the meantime is that none of it gets worse,
 * and that the next phone regression fails a check instead of reaching a
 * user.
 */
function expectSpillsWithinBudget(page: Page, screen: string) {
  return findSpills(page).then((spills) => {
    const budget = BUDGET[screen] ?? 0;

    recordSpills(screen, spills);
    if (UPDATE_BUDGET) return;

    expect(
      spills.length,
      spills.length > budget
        ? `${screen}: ${spills.length} element(s) hold content wider than ` +
          `themselves at 390 px in a box that does not scroll, up from a ` +
          `frozen ${budget}. The content is cut off or pushed out of ` +
          `view:\n  ${spills.slice(0, 10).join("\n  ")}`
        : `${screen}: spills dropped from ${budget} to ${spills.length} — ` +
          `record it, so the budget cannot be spent again by the next ` +
          `regression. Refresh via ui-gates.yml (see the header).`,
    ).toBe(budget);
  });
}

/**
 * The controls a user cannot afford to lose on a phone.
 *
 * Visibility alone is not enough: an element pushed past the right edge
 * still reports as visible. These assert the box is actually inside the
 * viewport.
 */
async function expectWithinViewport(page: Page, testId: string) {
  const box = await page.getByTestId(testId).boundingBox();
  expect(box, `${testId} has no layout box`).not.toBeNull();
  const width = page.viewportSize()?.width ?? 0;
  expect(
    (box?.x ?? 0) + (box?.width ?? 0),
    `${testId} extends past the right edge of a 390 px viewport`,
  ).toBeLessThanOrEqual(width + 1);
  expect((box?.x ?? 0), `${testId} starts left of the viewport`).toBeGreaterThanOrEqual(-1);
}

/**
 * The widest number this product can plausibly show, as a string.
 *
 * Seven digits with separators: an organisation-wide component count, the
 * largest figure any of these screens renders. Every numeric text node is
 * rewritten to it before the second pass, so a column's width stops being a
 * property of the seed and becomes a property of the layout.
 *
 * Text is deliberately left alone. Names and identifiers already arrive
 * long from the seed (timestamped project names, scoped purls), and
 * rewriting them would test the fixture's imagination rather than the CSS —
 * whereas a digit count is bounded, and the bound is what a column has to
 * survive.
 */
const WIDEST_NUMBER = "9,999,999";

/**
 * Rewrite every numeric text node to {@link WIDEST_NUMBER}.
 *
 * Applied to the DOM rather than to the API responses because it must reach
 * every screen without knowing which endpoints each one calls, and because
 * the thing under test is what CSS does with a wide cell — not how the data
 * got there. React will restore the real values on its next render, which
 * is why the assertions run immediately after.
 */
async function widenEveryNumber(page: Page): Promise<number> {
  return page.evaluate((widest) => {
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
    );
    const numeric: Text[] = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = n.textContent ?? "";
      // Digits, optionally grouped or decimal — and nothing else. A cell
      // reading "3 of 40" keeps its words; one reading "3" does not.
      if (/^\s*\d[\d,.]*\s*$/.test(text)) numeric.push(n as Text);
    }
    numeric.forEach((n) => {
      n.textContent = widest;
    });
    return numeric.length;
  }, WIDEST_NUMBER);
}

function readPrimaryProjectId(): string {
  const seedPath = path.join(__dirname, "..", "screenshots", ".seed.json");
  const raw = JSON.parse(fs.readFileSync(seedPath, "utf8")) as {
    project_ids?: string[];
  };
  const id = raw.project_ids?.[0];
  if (!id) throw new Error("seed missing project_ids[0]");
  return id;
}

test.describe("@narrow", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("login (pre-auth)", async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await expectNoHorizontalOverflow(page, "login");
  });

  test.describe("authenticated", () => {
    test.beforeEach(async ({ page }) => {
      await applyAuthFromSeed(page);
    });

    for (const screen of AUTHENTICATED_SCREENS) {
      test(screen.id, async ({ page }) => {
        await screen.visit(page, { projectId: readPrimaryProjectId() });
        await expectNoHorizontalOverflow(page, screen.id);

        // The bar is shared by every authenticated screen, so checking its
        // essential controls once per screen costs nothing and catches a
        // regression wherever it is introduced.
        await expectWithinViewport(page, "logout-button");
        await expectWithinViewport(page, "sidebar-mobile-trigger");

        // Second pass — the verdict that does not depend on the seed.
        const widened = await widenEveryNumber(page);
        expect(
          widened,
          `${screen.id}: no numeric text node was found, so the widened ` +
            `pass asserted nothing. Either the screen genuinely shows no ` +
            `figures, or its content did not finish loading — and a gate ` +
            `that silently checks an empty page is worse than no gate.`,
        ).toBeGreaterThan(0);
        await expectNoHorizontalOverflow(page, `${screen.id} (widened)`);
        await expectWithinViewport(page, "logout-button");
      });
    }
  });

  test.afterAll(() => {
    if (!UPDATE_BUDGET || !fs.existsSync(OBSERVED_DIR)) return;
    const merged: Record<string, number> = {};
    for (const file of fs.readdirSync(OBSERVED_DIR)) {
      if (!file.endsWith(".json")) continue;
      const { screen, count } = JSON.parse(
        fs.readFileSync(path.join(OBSERVED_DIR, file), "utf8"),
      ) as { screen: string; count: number };
      merged[screen] = count;
    }
    const sorted = Object.fromEntries(
      Object.entries(merged).sort(([a], [b]) => a.localeCompare(b)),
    );
    fs.writeFileSync(BUDGET_PATH, JSON.stringify(sorted, null, 2) + "\n");
  });
});
