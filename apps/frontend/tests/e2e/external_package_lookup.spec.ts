// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * External package lookup -> intake prefill E2E (C8 phase 3).
 *
 * What this pins: the wiring between two screens, not the deps.dev round
 * trip. `**\/v1/external-packages*` is stubbed (same "third-party-backed
 * endpoint" pattern as `auth.spec.ts`'s `**\/auth/oauth/providers` stub) so
 * the scenario is deterministic and independent of deps.dev's live
 * availability -- the real round trip is covered by Phase 1's backend
 * integration tests and a manual live spot-check, not by e2e.
 *
 * INTAKE_REQUESTS_ENABLED is off everywhere by default; this suite only
 * runs where the compose stack turned it on (ci.yml's e2e job sets it via
 * docker-compose.dev.yml -- see that file's `x-backend-env` anchor). Any
 * other run target (`npm run dev`, ui-gates.yml) leaves it off, so this
 * spec self-skips rather than failing against a stack that never asked for
 * the flag.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { ExternalPackageLookupHarness } from "../_harness/ExternalPackageLookupHarness";
import { IntakeHarness } from "../_harness/IntakeHarness";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const RUN_TOKEN = Date.now().toString(36);
let seedSequence = 0;

function uniquePrefix(testInfo: import("@playwright/test").TestInfo): string {
  seedSequence += 1;
  return `xpl${RUN_TOKEN}s${seedSequence}r${testInfo.retry}`;
}

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
): SeedSummary | null {
  const prefix = uniquePrefix(testInfo);
  try {
    return seedE2eUser({
      projectNames: [`xpl-${prefix}`],
      withRefreshToken: true,
    });
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed -- bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

const STUBBED_RESULT = {
  ecosystem: "npm",
  name: "lodash",
  found: true,
  version: "4.18.1",
  purl: "pkg:npm/lodash",
  licenses: ["MIT"],
  advisory_count: 0,
  advisory_ids: [],
  homepage_url: "https://lodash.com/",
  source_repo_url: "https://github.com/lodash/lodash",
  internal_projects: [],
};

test.describe("@package-lookup package lookup asks about the result", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("the CTA carries the resolved purl into a pre-filled intake request", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo);
    if (seed === null) return;

    const about = page.waitForResponse((res) => res.url().includes("/v1/about"));
    const auth = new AuthHarness(page);
    await auth.loginViaRefreshCookie(seed.refresh_token!.token);
    // The intake CTA only renders once `useDeploymentFeatures()` has an
    // answer -- everything is off until then (see that hook's docstring),
    // so a click before the first /v1/about response lands on nothing.
    await about;

    await page.route("**/v1/external-packages*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STUBBED_RESULT),
      }),
    );

    const lookup = new ExternalPackageLookupHarness(page);
    await lookup.gotoLookup();
    await lookup.lookup("npm", "lodash");
    await lookup.expectResult("pkg:npm/lodash");

    await lookup.clickIntakeCta();

    await expect(page).toHaveURL(/\/intake\?purl=pkg%3Anpm%2Flodash/);
    const intakePurl = page.getByTestId("intake-purl");
    await expect(intakePurl).toHaveValue("pkg:npm/lodash");

    const intake = new IntakeHarness(page);
    await intake.expectSurfacePresent();
    await intake.ask(seed.project_ids[0], "pkg:npm/lodash", "pulled from the lookup result");
    await intake.expectRequestFor("pkg:npm/lodash");
  });
});
