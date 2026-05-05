/**
 * PortalPage — Playwright harness skeleton.
 *
 * Phase 0 PR #3 ships only the harness shape: a navigation root + a small
 * vocabulary of high-level methods. Real Playwright execution is wired up in
 * PR #5 (Phase 1 authentication) when a meaningful login/dashboard surface
 * exists. The shape mirrors the v1 PortalPage so test-writer agents can reuse
 * the muscle memory.
 *
 * Why ship the harness now: PR #5 will land 14+ scenarios in a single
 * session, so having the entry point + supported-language enum already in
 * tree avoids a same-PR refactor of every spec we touch.
 */
import type { Locator, Page } from "@playwright/test";

import { SUPPORTED_LANGUAGES, type SupportedLanguage } from "@/lib/i18n";

const DEFAULT_BASE_URL = "http://localhost:5173";

export class PortalPage {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(path: string = "/"): Promise<void> {
    await this.page.goto(`${this.baseUrl}${path}`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await this.page
      .getByTestId("home-main")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  // ───── i18n ────────────────────────────────────────────────────────────
  languageToggle(): Locator {
    return this.page.getByTestId("language-toggle");
  }

  async currentLanguage(): Promise<SupportedLanguage> {
    const value = await this.languageToggle().getAttribute(
      "data-current-language",
    );
    return assertSupported(value);
  }

  async toggleLanguage(): Promise<SupportedLanguage> {
    await this.languageToggle().click();
    return this.currentLanguage();
  }

  // ───── PR #5 placeholders ──────────────────────────────────────────────
  // The methods below intentionally throw so accidental early use surfaces
  // a clear "not wired yet" error instead of a silent test pass.
  async login(_email: string, _password: string): Promise<void> {
    throw new Error("PortalPage.login: wired in PR #5 (Phase 1)");
  }

  async logout(): Promise<void> {
    throw new Error("PortalPage.logout: wired in PR #5 (Phase 1)");
  }
}

function assertSupported(value: string | null): SupportedLanguage {
  if (value && (SUPPORTED_LANGUAGES as readonly string[]).includes(value)) {
    return value as SupportedLanguage;
  }
  throw new Error(`Unsupported language attribute: ${String(value)}`);
}
