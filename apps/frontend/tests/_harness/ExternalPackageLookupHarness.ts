// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * ExternalPackageLookupHarness: `/packages/lookup` page domain verbs.
 *
 * The deps.dev round trip itself is out of scope here (Phase 1's backend
 * integration tests + a manual live spot-check already cover it) -- this
 * harness drives the page against whatever the spec has routed
 * `**\/v1/external-packages*` to return, same "stub a third-party-backed
 * endpoint" pattern as `auth.spec.ts`'s `**\/auth/oauth/providers` stub.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class ExternalPackageLookupHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  async gotoLookup(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/packages/lookup`);
    await expect(this.page.getByTestId("external-package-lookup-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async lookup(ecosystem: string, name: string): Promise<void> {
    await this.page.getByTestId("external-package-ecosystem").selectOption(ecosystem);
    await this.page.getByTestId("external-package-name").fill(name);
    await this.page.getByTestId("external-package-lookup-submit").click();
  }

  async expectResult(purl: string): Promise<void> {
    const result = this.page.getByTestId("external-package-lookup-result");
    await expect(result).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(result).toContainText(purl);
  }

  async clickIntakeCta(): Promise<void> {
    await this.page.getByTestId("external-package-lookup-intake-cta").click();
  }
}
