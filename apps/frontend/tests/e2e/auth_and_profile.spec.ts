/**
 * Auth + Profile E2E — Phase 5 manual-aligned coverage.
 *
 * Verifies the manual-walkthrough fixes around the `/profile` Connected
 * Accounts surface and the `urn:trustedoss:problem:oauth_unlink_blocks_login`
 * regression guard.
 *
 * Scenarios:
 *   1. Login → /profile mounts → account header surfaces the seeded email.
 *   2. Login → /profile → fresh user has zero OAuth identities (empty card)
 *      and no Unlink button is present (defensive — a regression that
 *      mounts the row without an identity would trip this test).
 *   3. Adversarial: rawDelete with various malformed identifiers must
 *      respond 4xx + Problem Details (never 500, never a stack trace).
 *      Parametrized inputs cover the classes from
 *      `feedback_adversarial_input_parametrize`: control bytes, traversal,
 *      oversized strings, scheme injection.
 *
 * NOTE on the "happy-path Unlink succeeds" + "blocks-login" scenarios:
 * those require an existing OAuth identity on the seeded user. The Phase 4
 * seed helper does not provision OAuth identities (they're issued by the
 * external IdP callback) — those scenarios are deferred (`test.fixme`)
 * until a `--with-oauth-identity` seed flag exists. The Problem URN
 * regression is still exercised at the API level via the adversarial
 * scenario block, which is the highest-value guard.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import {
  OAUTH_UNLINK_BLOCKS_LOGIN_TYPE,
  ProfileHarness,
} from "../_harness/ProfileHarness";
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
      `seed precondition failed — bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@manual-aligned profile + connected accounts", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) /profile mounts and the account header surfaces the seeded email", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["profile-mount"],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const profile = new ProfileHarness(page);
    // Exercise the AppShell entry point — manual walkthrough emphasizes
    // the header link is the supported nav (sidebar has no profile entry).
    await profile.openProfileViaHeader();

    const renderedEmail = await profile.getAccountEmail();
    expect(renderedEmail).toBe(seed.email);
  });

  test("2) fresh user shows the empty Connected Accounts card", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["profile-empty-identities"],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const profile = new ProfileHarness(page);
    await profile.gotoProfile();

    // Pass an empty array — the harness routes to the empty-card assertion.
    await profile.expectConnectedAccounts([]);

    // Defensive — the Unlink button must NOT mount when zero identities
    // exist. A regression that renders the row without an identity would
    // trip this assertion.
    await expect(
      page.getByTestId("profile-identity-unlink"),
    ).toHaveCount(0);
  });

  // Last-only blocks-login (urn:trustedoss:problem:oauth_unlink_blocks_login).
  // Marathon bundle 2 (D1) unblocked this scenario: the seed now supports
  // `noPassword: true` which provisions an OAuth-only user (empty
  // hashed_password) plus a deterministic refresh-token cookie so the
  // spec authenticates without driving a real IdP callback. Unlinking the
  // sole identity must trip `OAuthUnlinkBlocksLoginError` and surface the
  // inline red banner (`profile-unlink-blocks-login`).
  test("3) last-only OAuth Unlink surfaces blocks-login alert", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["profile-blocks-login"],
      withOAuthIdentity: "github",
      noPassword: true,
    });
    if (seed === null) return;

    // Sanity — the seed must have honored the OAuth-only request and minted
    // a refresh token so the spec can authenticate.
    expect(seed.no_password).toBe(true);
    expect(seed.refresh_token).not.toBeNull();
    expect(seed.refresh_token).toBeDefined();
    expect(seed.password).toBe("");

    const auth = new AuthHarness(page);
    await auth.loginViaRefreshCookie(seed.refresh_token!.token);

    const profile = new ProfileHarness(page);
    await profile.gotoProfile();
    await profile.expectConnectedAccounts(["github"]);

    await profile.unlinkProvider("github");
    await profile.expectUnlinkBlocked("github");

    // Post-condition: the identity row is still present (no row deletion
    // happened) and a re-fetch would still report exactly one identity.
    await profile.expectConnectedAccounts(["github"]);
  });

  test("4) Unlink succeeds when password fallback exists", async ({
    page,
  }, testInfo) => {
    // Seed user has BOTH a password and a GitHub identity. The unlink path
    // checks "is the identity being removed the user's last auth method?"
    // — with the password set, the answer is no, so the row is removed and
    // the success toast surfaces.
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["profile-unlink-fallback"],
      withOAuthIdentity: "github",
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const profile = new ProfileHarness(page);
    await profile.gotoProfile();
    // Defensive — the seeded row should mount before we click Unlink.
    await profile.expectConnectedAccounts(["github"]);

    await profile.unlinkProvider("github");
    await profile.expectUnlinkSuccess();

    // Post-condition: the empty card mounts because the user is back to
    // zero connected identities. The list re-fetch fires implicitly on
    // the unlink response so we don't need to trigger a manual reload.
    await profile.expectConnectedAccounts([]);
  });

  test.describe("adversarial — malformed identity ids rejected with 4xx", () => {
    // Each case is a class of attacker input the backend identity router
    // must reject without leaking 500. We do NOT URI-encode the value —
    // that is the whole point of the test (memory
    // `feedback_adversarial_input_parametrize`).
    const cases: Array<{ name: string; rawId: string; expectStatus: number[] }> = [
      {
        name: "control bytes (CRLF)",
        rawId: "deadbeef%0d%0aSet-Cookie:%20pwn=1",
        expectStatus: [400, 404, 422],
      },
      {
        name: "path traversal",
        rawId: "../../etc/passwd",
        // FastAPI typically rewrites traversal at the routing layer → 404.
        expectStatus: [400, 404, 422, 405],
      },
      {
        name: "scheme injection",
        rawId: "javascript:alert(1)",
        expectStatus: [400, 404, 422],
      },
      {
        name: "null byte",
        rawId: "abc%00def",
        expectStatus: [400, 404, 422],
      },
      {
        name: "oversized (4 KiB)",
        rawId: "a".repeat(4096),
        expectStatus: [400, 404, 414, 422],
      },
      {
        name: "non-uuid plain text",
        rawId: "not-a-uuid",
        expectStatus: [400, 404, 422],
      },
    ];

    for (const variant of cases) {
      test(`oauth-identities DELETE — ${variant.name}`, async ({
        page,
      }, testInfo) => {
        const seed = tryAcquireSeed(testInfo, {
          projectNames: [`profile-adv-${slug(variant.name)}`],
        });
        if (seed === null) return;

        const auth = new AuthHarness(page);
        await auth.gotoLogin();
        await auth.login(seed.email, seed.password);

        const profile = new ProfileHarness(page);
        await profile.gotoProfile();

        const response = await profile.rawDeleteIdentity(variant.rawId);
        expect(
          variant.expectStatus,
          `unexpected status for ${variant.name}: ${response.status()}`,
        ).toContain(response.status());

        // The response must NEVER be a 500 (RFC 7807 catch-all should not
        // be tripped — the validator must reject earlier).
        expect(response.status()).toBeLessThan(500);

        // 4xx from our middleware uses application/problem+json. The 414
        // (URI Too Long) case may come from the proxy upstream as
        // text/plain — accept either, but assert no HTML stack trace.
        const ct = response.headers()["content-type"] ?? "";
        expect(ct.includes("text/html")).toBe(false);
        // Body should not include a Python traceback marker — that would
        // indicate the unhandled-exception path leaked.
        const body = await response.text();
        expect(body).not.toContain("Traceback (most recent call last)");
        // Sanity — the URN we *don't* want to see is the blocks-login one
        // (that should only fire on a legit identity id, not on garbage).
        expect(body).not.toContain(OAUTH_UNLINK_BLOCKS_LOGIN_TYPE);
      });
    }
  });
});

function slug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
