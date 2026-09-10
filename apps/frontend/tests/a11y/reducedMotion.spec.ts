/**
 * #425: `prefers-reduced-motion` verification.
 *
 * `src/index.css` collapses every animation/transition duration to 0.01ms
 * under `@media (prefers-reduced-motion: reduce)` (W15's contrast work).
 * That rule has never been exercised by a test: the mechanism could be
 * broken by a typo, a later stylesheet's higher-specificity rule, or a
 * component that hard-codes an inline animation and nothing here would
 * notice. This asserts the override actually reaches a real animated
 * element, not just that the CSS block still exists.
 *
 * `app-main` (`AppShell.tsx`) is the target: it carries `animate-in
 * fade-in-0 duration-slow` unconditionally on every route, the same
 * element `a11y.spec.ts`'s `settle()` and `sidebar.spec.ts`'s skip-link
 * test already key off. `duration-slow` is 250ms (`index.css`), so the
 * reduced-motion assertion has real room to be wrong in.
 */
import { expect, test } from "@playwright/test";

import { applyAuthFromSeed } from "../screenshots/_helpers";

async function animationDurationMs(page: import("@playwright/test").Page): Promise<number> {
  const raw = await page
    .getByTestId("app-main")
    .evaluate((el) => getComputedStyle(el).animationDuration);
  // Chromium normalises `animation-duration` to seconds ("0.25s"), not the
  // "250ms" the source declares, so `Number.parseFloat` alone silently reads
  // "0.25s" as 0.25*ms*, which is how the "no preference" control case first
  // came back looking reduced when it was not.
  const value = Number.parseFloat(raw);
  return raw.trim().endsWith("ms") ? value : value * 1000;
}

test.describe("@a11y prefers-reduced-motion", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("collapses the app shell's route-change animation to effectively 0", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await page.getByTestId("app-main").waitFor({ state: "visible" });

    expect(
      await animationDurationMs(page),
      "prefers-reduced-motion: reduce did not collapse app-main's animation-duration; " +
        "check the @media block in src/index.css still wins the specificity fight",
    ).toBeLessThan(1);
  });

  test("the same element runs the full-duration animation without the preference", async ({
    page,
  }) => {
    // The control side of the assertion above: if this also read near-zero,
    // the first test would be passing for the wrong reason (an animation
    // that never ran, rather than one that was actually shortened).
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto("/");
    await page.getByTestId("app-main").waitFor({ state: "visible" });

    expect(await animationDurationMs(page)).toBeGreaterThan(100);
  });
});
