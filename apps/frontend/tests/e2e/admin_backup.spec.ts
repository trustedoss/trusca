/**
 * Admin Backup E2E — Phase 5 manual-aligned coverage.
 *
 * Drives `/admin/backup` against the live docker-compose dev stack with a
 * super-admin seed user. Selectors live in `tests/_harness/AdminBackupHarness.ts`
 * — every assertion is rooted in `data-testid` (locale-agnostic).
 *
 * Scenarios (manual-walkthrough alignment):
 *   1. Page mounts for super-admin and the table renders (with rows or
 *      the empty card). Existence-hide guard verified inversely (a regular
 *      developer hitting /admin/backup gets the 404 page).
 *   2. Type-to-confirm gate — picking a file mounts the warning strip;
 *      the destructive Restore button stays disabled until the literal
 *      "restore" token is typed (case-sensitive).
 *   3. Adversarial: the backend's backup-name regex must reject path-
 *      traversal / control bytes in the DELETE path with 4xx + Problem
 *      Details (never 500).
 *   4. Adversarial: restore upload with a mis-shaped file (not a gzip,
 *      tiny "header" only) must be rejected via Problem Details — the
 *      decompression-bomb / file-shape validator code path runs without
 *      requiring a 10GB body to actually be streamed.
 *
 * The "manual trigger writes a real backup file" scenario is deferred when
 * the Celery worker is stale (the dev stack's worker image is currently
 * missing aiosmtplib; see prompt context). The toast assertion still runs;
 * the row check is wrapped in `test.fixme` when worker health is unknown
 * to keep the test deterministic.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 */
import { expect, test } from "@playwright/test";

import { AdminBackupHarness } from "../_harness/AdminBackupHarness";
import { AuthHarness } from "../_harness/auth";
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

test.describe("@manual-aligned admin backup", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) page mounts for super-admin and table renders", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-backup-mount"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();

    // Either rows or empty-card. Both are valid post-mount states; the
    // harness's expectMounted already waited on aria-busy=false.
    const rowCount = await backup.getRowCount();
    if (rowCount === 0) {
      await expect(page.getByTestId("admin-backup-empty")).toBeVisible();
    } else {
      // Sanity — at least one row carries a data-name attribute.
      await expect(
        page.locator('[data-testid="admin-backup-row"]').first(),
      ).toHaveAttribute("data-name", /.+/);
    }
  });

  test("2) non-super-admin actor hits the existence-hide page", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-backup-deny"],
      // Default role is developer — `superAdmin` omitted on purpose.
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const backup = new AdminBackupHarness(page);
    await page.goto(`${backup.baseUrl}/admin/backup`);
    await backup.expectAccessDenied();
  });

  test("3) restore type-to-confirm gate disables Submit until 'restore' is typed", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-backup-restore-gate"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();

    // Pick a tiny placeholder file. The restore endpoint will validate
    // shape later — at this point we only care about the UI gate.
    const file = {
      name: "placeholder-restore.tar.gz",
      mimeType: "application/gzip",
      // Intentionally not a real gzip — this scenario only drives the
      // typing gate UI; no submission is fired.
      buffer: Buffer.from("not-a-real-gzip"),
    };

    await backup.openRestoreModal(file);
    // Initial state: confirm input empty → Submit disabled.
    await backup.expectRestoreButtonEnabled(false);

    // Negative: a non-matching token keeps it disabled.
    await backup.typeRestoreConfirm("Restore"); // capital R differs
    await backup.expectRestoreButtonEnabled(false);

    await backup.typeRestoreConfirm("delete");
    await backup.expectRestoreButtonEnabled(false);

    // Exact literal enables Submit.
    await backup.typeRestoreConfirm("restore");
    await backup.expectRestoreButtonEnabled(true);

    // Cleanup — cancel the selection so we don't leave the dialog mounted
    // for a follow-on test in the same spec file.
    await backup.cancelRestore();
  });

  test("4) manual backup trigger creates a row", async ({
    page,
  }, testInfo) => {
    // Marathon bundle 3 (D2) refactored ``tasks.backup`` to call
    // ``pg_dump`` / ``psql`` directly via DATABASE_URL — the old shell-
    // delegation path that required ``docker-compose`` inside the worker
    // container is gone. The worker image now ships postgresql-client-17
    // (apps/backend/Dockerfile.worker) so the manual trigger writes a real
    // ``manual-YYYYMMDDTHHMMSSZ`` row in ``backups/``.
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-backup-trigger"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    const before = await backup.getRowCount();
    await backup.triggerManualBackup();

    await backup.refresh();
    await backup.waitForManualBackupRow(30_000);

    const after = await backup.getRowCount();
    expect(after).toBeGreaterThan(before);
  });

  test.describe("adversarial — backup name DELETE rejects malformed input", () => {
    const cases: Array<{
      name: string;
      rawName: string;
      expectStatus: number[];
    }> = [
      {
        name: "path traversal up",
        rawName: "..%2F..%2Fetc%2Fpasswd",
        expectStatus: [400, 403, 404, 422],
      },
      {
        name: "absolute path",
        rawName: "%2Fetc%2Fpasswd",
        expectStatus: [400, 403, 404, 422],
      },
      {
        name: "control bytes (CRLF)",
        rawName: "manual-%0d%0aSet-Cookie:%20pwn=1.tar.gz",
        expectStatus: [400, 403, 404, 422],
      },
      {
        name: "null byte injection",
        rawName: "manual%00.tar.gz",
        expectStatus: [400, 403, 404, 422],
      },
      {
        name: "wrong extension",
        rawName: "manual-deadbeef.zip",
        expectStatus: [400, 403, 404, 422],
      },
      {
        name: "oversized name (1 KiB)",
        rawName: "a".repeat(1024) + ".tar.gz",
        expectStatus: [400, 403, 404, 414, 422],
      },
    ];

    for (const variant of cases) {
      test(`DELETE /v1/admin/backups/{name} — ${variant.name}`, async ({
        page,
      }, testInfo) => {
        const seed = tryAcquireSeed(testInfo, {
          projectNames: [`adv-bk-${slug(variant.name)}`],
          superAdmin: true,
        });
        if (seed === null) return;

        const auth = new AuthHarness(page);
        await auth.gotoLogin();
        await auth.login(seed.email, seed.password);

        const backup = new AdminBackupHarness(page);
        // Mount the page so the SPA's axios bootstrap installs the access
        // token hook used by rawDeleteBackup.
        await backup.gotoBackup();

        const response = await backup.rawDeleteBackup(variant.rawName);
        expect(
          variant.expectStatus,
          `unexpected status for ${variant.name}: ${response.status()}`,
        ).toContain(response.status());
        expect(response.status()).toBeLessThan(500);

        const ct = response.headers()["content-type"] ?? "";
        // No HTML traceback — must be JSON or text/plain at worst.
        expect(ct.includes("text/html")).toBe(false);
        const body = await response.text();
        expect(body).not.toContain("Traceback (most recent call last)");
      });
    }
  });

  test("5) restore upload — mis-shaped fixture is rejected with 4xx + Problem", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-backup-restore-shape"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();

    // Tiny non-gzip body with a tar.gz extension. The validator must
    // reject it before ever touching the decompressor. We verify the
    // status is 4xx and the body is application/problem+json (or at least
    // never an HTML stack trace).
    const response = await backup.rawUploadRestore({
      name: "bogus-restore.tar.gz",
      mimeType: "application/gzip",
      buffer: Buffer.from("THIS-IS-NOT-A-GZIP-STREAM"),
    });
    expect(response.status()).toBeGreaterThanOrEqual(400);
    expect(response.status()).toBeLessThan(500);
    const ct = response.headers()["content-type"] ?? "";
    expect(ct.includes("text/html")).toBe(false);
    const body = await response.text();
    expect(body).not.toContain("Traceback (most recent call last)");
  });
});

function slug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
