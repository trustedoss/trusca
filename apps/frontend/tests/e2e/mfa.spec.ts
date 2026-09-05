/**
 * Two-step sign-in, end to end.
 *
 * One scenario, in the order a person meets it: enrol on `/profile`, sign
 * out, sign back in with a code from the app, sign out again, and sign in
 * with a recovery code when the app is not to hand. The unit tests cover each
 * piece with the wire layer mocked; this is the only place that asks whether
 * the pieces agree with each other and with the real backend.
 *
 * Two things only this level can check.
 *
 * The codes are computed here from the secret the server handed out and
 * checked there against the secret the server stored, so a disagreement
 * between our transcription of RFC 6238 and the standard shows up as a
 * failing sign-in rather than as an authenticator app that mysteriously does
 * not work. Nothing below this level can catch that, because both halves of a
 * unit test would use the same implementation and agree with themselves.
 *
 * And the sign-out between the steps is not decoration. A test that enrolled
 * and then asserted against the session it already held would never ask the
 * question the feature exists to answer.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { codeFor, MfaHarness } from "../_harness/MfaHarness";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed - bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@manual-aligned two-step sign-in", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("enrol, sign in with a code, then with a recovery code", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["mfa-lifecycle"] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const mfa = new MfaHarness(page);

    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    // 1. Enrol. The step-up asks for the password even though the session is
    //    open, because what comes back keeps working after a password change.
    const { secret, codes } = await mfa.enrol(seed.password);
    expect(codes).toHaveLength(10);
    // Shown once and stored as hashes, so this is the only readable form.
    expect(new Set(codes).size).toBe(codes.length);

    await mfa.signOut();

    // 2. The password alone now stops at the code step. Asserted before the
    //    code is supplied, because "the code screen appeared" and "no session
    //    exists yet" are different claims and only the second one matters.
    await mfa.submitPassword(seed.email, seed.password);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);

    // 3. A wrong code is refused and the step stays open, so a mistyped digit
    //    is a retry rather than starting the whole sign-in again.
    await mfa.submitPassword(seed.email, seed.password);
    await mfa.submitCode("000000");
    await mfa.expectCodeRefused();

    // 4. The code an authenticator app would show gets in.
    await mfa.submitCode(codeFor(secret));
    await auth.expectLoggedIn();

    await mfa.signOut();

    // 5. And a recovery code gets in where the six digits are asked for,
    //    which is the path for somebody whose phone is gone.
    await mfa.signInWithRecoveryCode(seed.email, seed.password, codes[0]);
    await auth.expectLoggedIn();

    await mfa.signOut();

    // 6. The same recovery code does not work twice: ten of them is ten
    //    sign-ins, not ten attempts.
    await mfa.submitPassword(seed.email, seed.password);
    await mfa.submitCode(codes[0]);
    await mfa.expectCodeRefused();
  });

  test("the step-up refuses a wrong password and hands out no secret", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["mfa-step-up"] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const mfa = new MfaHarness(page);
    await mfa.expectStepUpRefused("not the right password at all");
  });
});
