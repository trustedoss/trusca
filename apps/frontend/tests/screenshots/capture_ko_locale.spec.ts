/**
 * Marathon bundle 9 (4d) — KO locale-specific guide screenshots.
 *
 * Most screens in the EN docs (admin tables, code blocks, severity
 * badges) read the same in KO because the underlying tokens are kept
 * in English (Korean SCA practitioners write `CVE`, `SBOM`, `CycloneDX`
 * untranslated — see the `i18n-specialist` glossary and the locale
 * audit in PR #79). Re-capturing every screen in KO would double the
 * asset directory for marginal value.
 *
 * This spec captures only the screens where a KO user sees a *layout*
 * difference, not just a text swap:
 *
 *   - Auth pages (login + forgot): Korean form labels + helper copy
 *     consume different horizontal real-estate and use a different
 *     button order ("로그인" / "취소"). A KO user clicking through the
 *     guide sees this immediately.
 *   - Project create form: Korean field labels are longer than their
 *     English counterparts, which compresses the input column. The
 *     EN capture under-represents how dense the form looks in KO.
 *   - Notification preferences: each kind row has a Korean label that
 *     wraps differently than the English one ("스캔 완료" vs "Scan
 *     completed"); the per-row toggle column shifts.
 *   - Admin DT status card: the breaker badge ("정상" vs "CLOSED")
 *     and refresh action button width drift between locales.
 *
 * The bulk EN capture matrix (`capture_user_guide.spec.ts` /
 * `capture_admin_guide.spec.ts`) still owns every other screen.
 *
 * Output: PNGs land at `docs-site/static/img/screenshots/<slug>-ko.png`
 * via `captureLocaleScreenshot(page, slug, "ko")`. KO markdown files
 * reference the `-ko` variant explicitly; EN markdown is unchanged.
 */
import { test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import {
  applyAuthFromSeed,
  captureLocaleScreenshot,
  setUiLanguage,
} from "./_helpers";

// ════════════════════════════════════════════════════════════════════
// Pre-auth captures (auth views must clear the shared storage state)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots-ko user-guide/auth-and-profile (pre-auth, ko)", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await setUiLanguage(page, "ko");
  });

  test("user-auth-login-ko — login page (KO)", async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await captureLocaleScreenshot(page, "user-auth-login", "ko");
  });

  test("user-auth-forgot-ko — forgot-password page (KO)", async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.gotoForgotPassword();
    await captureLocaleScreenshot(page, "user-auth-forgot", "ko");
  });
});

// ════════════════════════════════════════════════════════════════════
// Authenticated KO captures
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots-ko user-guide (authenticated, ko)", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
    await setUiLanguage(page, "ko");
  });

  test("user-projects-create-form-ko — new project form (KO)", async ({
    page,
  }) => {
    await page.goto("/projects/new");
    await page
      .getByTestId("project-create-form")
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureLocaleScreenshot(page, "user-projects-create-form", "ko");
  });

  test("user-notifications-prefs-ko — preferences panel (KO)", async ({
    page,
  }) => {
    await page.goto("/notifications");
    await page
      .getByTestId("notifications-prefs-section")
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureLocaleScreenshot(page, "user-notifications-prefs", "ko");
  });
});

test.describe.serial("@screenshots-ko admin-guide (authenticated, ko)", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
    await setUiLanguage(page, "ko");
  });

  test("admin-dt-status-ko — DT status card (KO)", async ({ page }) => {
    const portal = new PortalPage(page);
    await portal.gotoAdminDT();
    await page
      .getByTestId("admin-dt-status-card")
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureLocaleScreenshot(page, "admin-dt-status", "ko");
  });
});
