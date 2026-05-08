/**
 * Auth E2E — Phase 1 PR #6 task 1.9.
 *
 * Covers the gateway flows shipped by 1.6 (Login / Register / Forgot pages)
 * against a real FastAPI + Postgres backend (docker-compose dev stack).
 *
 * Scenarios:
 *   1. register → auto-login → home reachable
 *   2. login failure → inline alert, URL stays on /login
 *   3. (gated on 1.7) access token expiry → axios interceptor refreshes →
 *      original request retried successfully
 *
 * Why the harness: every selector is rooted in `data-testid`, so EN/KO label
 * changes from 1.8 cannot break a single assertion here. See
 * `tests/_harness/auth.ts`.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";

test.describe("@auth gateway flows", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) register → auto-login → home is reachable", async ({ page }) => {
    const auth = new AuthHarness(page);
    const email = auth.randomEmail();
    const password = auth.randomPassword();

    await auth.gotoRegister();
    await auth.register({ email, password, displayName: "E2E User" });

    // `register()` already waits for /projects and asserts app-sidebar visible —
    // re-assert here for spec readability.
    await auth.expectLoggedIn();

    // AppShell is mounted and the sidebar is visible after login.
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
  });

  test("2) login with bad credentials surfaces inline alert", async ({
    page,
  }) => {
    const auth = new AuthHarness(page);
    const email = auth.randomEmail();
    const password = "wrong-password-1234";

    await auth.gotoLogin();
    await auth.submitLoginExpectingError(email, password);

    // RFC 7807 detail is rendered inside the destructive Alert. Locale-agnostic:
    // we only assert presence + non-empty content, not the literal text.
    await auth.expectAlert();

    // URL must not have navigated away from /login.
    await expect(page).toHaveURL(/\/login$/);
  });

  test("3) expired access token → auto-refresh → original request retried", async ({
    page,
  }) => {
    // 1.7 (axios interceptor + window.__setAccessToken hook + automatic
    // /auth/refresh on 401) is not yet merged. Until it is, this scenario is
    // skipped. Removal procedure documented in the PR handoff.
    const interceptorReady = await page.evaluate(() => {
      const w = window as unknown as { __setAccessToken?: unknown };
      return typeof w.__setAccessToken === "function";
    }).catch(() => false);

    test.skip(
      !interceptorReady,
      "1.7 axios interceptor not yet wired — unskip when window.__setAccessToken hook lands.",
    );

    const auth = new AuthHarness(page);
    const email = auth.randomEmail();
    const password = auth.randomPassword();

    // Provision an account, then sign in cleanly so the refresh cookie is set.
    await auth.gotoRegister();
    await auth.register({ email, password, displayName: "Refresh User" });
    await auth.expectLoggedIn();

    // Forge a syntactically-valid but already-expired JWT (exp = 0). The
    // backend will reject it with 401 → the axios interceptor (1.7) must call
    // /auth/refresh, swap in the new access token, and replay the original
    // request. We do NOT touch the refresh cookie — that's the contract under
    // test.
    const expiredJwt = makeExpiredJwt();
    await auth.setAccessTokenInStore(expiredJwt);

    // Trigger an authenticated request. Reload re-runs RequireAuth which (in
    // 1.7) hydrates via /auth/me → 401 → refresh → retry → 200.
    await page.reload();
    await auth.expectLoggedIn();
  });
});

/**
 * Build a JWT with header/payload that decode but `exp = 0`. Signature is a
 * placeholder — backend rejects it, but the rejection path is exactly what we
 * want to exercise (401 → refresh).
 */
function makeExpiredJwt(): string {
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(
    JSON.stringify({ sub: "expired", exp: 0, iat: 0 }),
  );
  return `${header}.${payload}.invalid-signature`;
}

function b64url(input: string): string {
  // Playwright runs under Node — Buffer is available.
  return Buffer.from(input, "utf8")
    .toString("base64")
    .replace(/=+$/, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}
