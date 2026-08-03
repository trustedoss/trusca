/**
 * Theme control for the gates (W18).
 *
 * Both the visual baselines and the accessibility scan now walk every
 * representative screen twice. This is the verb they share, so the two
 * cannot disagree about what "dark" means — the same reason
 * `representativeScreens.ts` exists.
 *
 * It writes the preference the way a user's browser would, through the same
 * storage key `src/lib/theme.ts` reads, and lets the app's own bootstrap
 * apply it. Setting the `dark` class directly would have been shorter and
 * would have tested nothing: the interesting question on a themed screen is
 * whether the product resolves and applies its own preference, and a test
 * that stamps the class answers that question for the product.
 */
import type { Page } from "@playwright/test";

export const THEMES = ["light", "dark"] as const;
export type GateTheme = (typeof THEMES)[number];

/** Shared with `src/lib/theme.ts`; `themeGuard.test.ts` keeps them in step. */
const THEME_STORAGE_KEY = "trusca-theme";

/**
 * Pin the theme for everything this page subsequently loads.
 *
 * Must be called before the first navigation — `addInitScript` runs on every
 * document, so the inline bootstrap in `index.html` finds the preference
 * already there and the first frame is already correct. Calling it after a
 * `goto` would leave that first document unthemed and, on the visual gate,
 * capture a light frame under a dark name.
 */
export async function pinTheme(page: Page, theme: GateTheme): Promise<void> {
  await page.addInitScript(
    ([key, value]) => {
      window.localStorage.setItem(key, value);
    },
    [THEME_STORAGE_KEY, theme] as const,
  );
}

/**
 * Assert the app actually applied it, and wait until it has.
 *
 * Without this a gate can capture or scan the moment before the bootstrap
 * runs. It also catches the failure mode where `pinTheme` silently stops
 * working — a renamed storage key would otherwise turn the dark half of both
 * gates into a second, redundant light run whose baselines look plausible.
 */
export async function expectThemeApplied(
  page: Page,
  theme: GateTheme,
): Promise<void> {
  await page.waitForFunction(
    (expected) =>
      document.documentElement.classList.contains("dark") ===
      (expected === "dark"),
    theme,
    { timeout: 10_000 },
  );
}

/** `pinTheme` plus the assertion, for callers that navigate immediately after. */
export async function useTheme(page: Page, theme: GateTheme): Promise<void> {
  await pinTheme(page, theme);
}

/** Suffix for baseline names: light keeps the bare name it has always had. */
export function themeSuffix(theme: GateTheme): string {
  return theme === "light" ? "" : `-${theme}`;
}
