/**
 * The font wait every capture shares.
 *
 * `document.fonts.ready` is not enough on its own. It resolves when no font
 * load is in flight, and on a cold page nothing is in flight yet: the
 * `@font-face` rules arrive with a stylesheet, and until that stylesheet
 * lands the browser has no font to load and reports itself ready. A capture
 * taken then renders in the fallback face. Self-hosting the two families
 * (`src/fonts.css`, #122) removed the CDN round-trip but not the race: the
 * stylesheet is still fetched, and `index.css` still imports it.
 *
 * Two back-to-back baseline captures from one commit caught exactly that on
 * 2026-08-14: `projects-list.png` came out in the fallback face in one of
 * them, at 1.8 % of the viewport. The ceiling at the time was 2 %, so a
 * screenshot in the wrong typeface passed the gate.
 *
 * So the wait is in two parts. At least one Inter face has to have reached
 * `loaded`, which cannot happen before the stylesheet registers the faces,
 * and no Inter face may still be in flight. Naming a weight instead would be
 * wrong: a browser fetches only the faces a page uses, so asking for
 * `600 16px Inter` on a screen with no semibold text waits forever, which is
 * how the first version of this failed on admin-users.
 *
 * The second clause used to read `document.fonts.status`, which is a
 * document-wide snapshot: it returns to "loading" for ANY family, so a
 * single unrelated face that never settles held up a capture whose own font
 * had been ready for seconds. That timed out four times on 2026-08-15,
 * across two screens, with nothing wrong in the screenshots. Scoping the
 * clause to Inter keeps both guarantees and drops the dependency on
 * everything else the page happens to fetch.
 *
 * If the stylesheet never lands the wait times out and the test fails, which
 * is the honest outcome: the alternative is a baseline recording the wrong
 * font. On that timeout it reports which faces were in what state, because
 * four investigations of a bare "Timeout 20000ms exceeded" produced four
 * guesses and no cause.
 *
 * It lives here, rather than in the visual spec that first needed it,
 * because the documentation-screenshot pipeline has the same shutter and had
 * no wait at all: a capture run that lost the race put a page in the wrong
 * typeface into the published guides, and nothing failed to say so (#113).
 */
import type { Page } from "@playwright/test";

export async function waitForWebFonts(page: Page): Promise<void> {
  try {
    await page.waitForFunction(
      () => {
        const faces = Array.from(document.fonts);
        const inter = faces.filter((f) => f.family === "Inter");
        return (
          inter.length > 0 &&
          inter.some((f) => f.status === "loaded") &&
          // Scoped to Inter, not `document.fonts.status`. That property is
          // a snapshot of the whole document and returns to "loading" for
          // any family, so one unrelated face that never settles blocked
          // a capture whose own font had been ready for seconds. This
          // timed out four times on 2026-08-15, on two different screens,
          // while the screenshots themselves were fine.
          //
          // The guarantee that matters is unchanged: at least one Inter
          // face loaded, and no Inter face still in flight.
          inter.every((f) => f.status !== "loading")
        );
      },
      undefined,
      { timeout: 20_000 },
    );
  } catch (err) {
    // Say which face was stuck. The previous version failed with a bare
    // "Timeout 20000ms exceeded" pointing at the line, which told four
    // separate investigations nothing and left the cause a guess.
    const state = await page.evaluate(() =>
      Array.from(document.fonts).map((f) => ({
        family: f.family,
        weight: f.weight,
        style: f.style,
        status: f.status,
      })),
    );
    throw new Error(
      `waitForWebFonts timed out. document.fonts.status=` +
        `${await page.evaluate(() => document.fonts.status)}, faces=` +
        `${JSON.stringify(state)}\n\nOriginal: ${
          err instanceof Error ? err.message : String(err)
        }`,
    );
  }
}
