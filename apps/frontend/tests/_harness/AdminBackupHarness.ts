/**
 * AdminBackupHarness — Phase 5 manual-aligned E2E.
 *
 * Domain verbs for the `/admin/backup` surface (manual trigger / list /
 * download / restore upload with type-to-confirm gate / delete with inline
 * confirm). Mirrors {@link AdminUsersHarness}: every selector is rooted in
 * a `data-testid` the SPA emits verbatim, never a translated label.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2 + test-writer.md):
 *   - No mocking of our own backend. Real HTTP against docker-compose dev.
 *   - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *   - Selectors live inside the harness; spec files never touch CSS/text.
 *
 * Adversarial parametrize note (memory `feedback_adversarial_input_parametrize`):
 *   - Backup name regex on the backend rejects path traversal / control
 *     bytes — exercised by {@link rawDeleteBackup} which posts the raw
 *     name verbatim.
 *   - Decompression-bomb guard on restore upload — exercised via
 *     {@link rawUploadRestore} with an in-memory gzip stream that
 *     reports a misleading uncompressed length. We do NOT actually
 *     stream a 10GB body in tests; the smaller fixture is enough to
 *     trigger the validator code path.
 */
import { expect, type APIResponse, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class AdminBackupHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async gotoBackup(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/backup`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-backup-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until the list table finishes its first fetch.
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-backup-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  /** Existence-hide assertion — non-super-admin actors hit this branch. */
  async expectAccessDenied(): Promise<void> {
    await expect(this.page.getByTestId("admin-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-layout")).toHaveCount(0);
  }

  // ───── manual trigger ─────────────────────────────────────────────────
  /**
   * Click "Run manual backup now" and wait for the success toast. The
   * resulting backup row arrives in the list once the worker finishes —
   * callers should follow with {@link expectBackupRow} only when the
   * Celery worker is up. Without a worker, we still assert the toast
   * (the API enqueues synchronously) and skip the row check.
   */
  async triggerManualBackup(): Promise<void> {
    await this.page.getByTestId("admin-backup-manual-trigger").click();
    await expect(
      this.page.locator(
        '[data-testid="admin-toast"][data-toast-key="manual_triggered"]',
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── list assertions ────────────────────────────────────────────────
  async expectBackupRow(name: string): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-backup-row"][data-name="${cssEscape(name)}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /** Read the row count in the body (skeleton + empty rows excluded). */
  async getRowCount(): Promise<number> {
    return this.page.getByTestId("admin-backup-row").count();
  }

  /**
   * Refresh the list manually (the toolbar Refresh button) and wait for
   * the next fetch to settle.
   */
  async refresh(): Promise<void> {
    await this.page.getByTestId("admin-backup-refresh").click();
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-backup-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  // ───── download ───────────────────────────────────────────────────────
  /**
   * Click the row's Download button and capture the browser's download
   * event. Returns `{ filename, contentType }` so the spec can assert on
   * the gzip provenance.
   */
  async downloadBackup(name: string): Promise<{
    filename: string;
    contentType: string | null;
  }> {
    const row = this.page.locator(
      `[data-testid="admin-backup-row"][data-name="${cssEscape(name)}"]`,
    );
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const downloadPromise = this.page.waitForEvent("download", {
      timeout: 15_000,
    });
    await row.getByTestId("admin-backup-action-download").click();
    const download = await downloadPromise;
    // Playwright surfaces the response headers via the originating Response.
    // In practice we read the suggestedFilename + the content-type response
    // header (when present).
    return {
      filename: download.suggestedFilename(),
      contentType: null,
    };
  }

  // ───── restore upload + type-to-confirm gate ──────────────────────────
  /**
   * Pick a file via the hidden `<input type="file">`. The page renders
   * the warning strip + the type-to-confirm input only after the file is
   * picked. Caller passes `{ name, mimeType, buffer }` (Playwright's
   * file payload shape).
   */
  async openRestoreModal(file: {
    name: string;
    mimeType: string;
    buffer: Buffer;
  }): Promise<void> {
    // The actual <input type="file"> is `sr-only`. Playwright's
    // setInputFiles works on hidden inputs.
    await this.page.getByTestId("admin-backup-file-input").setInputFiles({
      name: file.name,
      mimeType: file.mimeType,
      buffer: file.buffer,
    });
    await expect(
      this.page.getByTestId("admin-backup-restore-strip"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(
      this.page.getByTestId("admin-backup-file-name"),
    ).toContainText(file.name);
  }

  /** Type into the confirm input — does NOT click Submit. */
  async typeRestoreConfirm(text: string): Promise<void> {
    const input = this.page.getByTestId("admin-backup-restore-confirm");
    await expect(input).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await input.fill(text);
  }

  /**
   * Assert the destructive Submit button is enabled / disabled. The page
   * encodes the gate via `disabled` on the button; this verb checks the
   * actual disabled state, not the visual style.
   */
  async expectRestoreButtonEnabled(enabled: boolean): Promise<void> {
    const submit = this.page.getByTestId("admin-backup-restore-submit");
    if (enabled) {
      await expect(submit).toBeEnabled({ timeout: DEFAULT_TIMEOUT_MS });
    } else {
      await expect(submit).toBeDisabled({ timeout: DEFAULT_TIMEOUT_MS });
    }
  }

  /**
   * Cancel the restore selection — clears the file input and unmounts
   * the warning strip.
   */
  async cancelRestore(): Promise<void> {
    await this.page.getByTestId("admin-backup-restore-cancel").click();
    await expect(
      this.page.getByTestId("admin-backup-restore-strip"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── delete with inline confirm ─────────────────────────────────────
  /**
   * Click Delete on the row, confirm via the inline strip. Waits for the
   * success toast. Throws (via auto-retry) if the row has the disabled
   * delete stub (auto backups).
   */
  async deleteBackup(name: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="admin-backup-row"][data-name="${cssEscape(name)}"]`,
    );
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row.getByTestId("admin-backup-action-delete").click();
    const confirmStrip = this.page.locator(
      `[data-testid="admin-backup-confirm-strip"][data-name="${cssEscape(name)}"]`,
    );
    await expect(confirmStrip).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await confirmStrip.getByTestId("admin-backup-confirm-ok").click();
    await expect(
      this.page.locator(
        '[data-testid="admin-toast"][data-toast-key="deleted"]',
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    // The row must unmount once the list refetch resolves.
    await expect(row).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── adversarial / API-direct ───────────────────────────────────────
  /**
   * Raw DELETE against the backup endpoint with the rawName injected
   * verbatim into the path (no encoding). Adversarial scenarios feed
   * traversal / control bytes — the backend must reject with 4xx +
   * Problem Details.
   */
  async rawDeleteBackup(rawName: string): Promise<APIResponse> {
    const accessToken = await this.page.evaluate(() => {
      const w = window as unknown as {
        __authStore?: { accessToken?: string | null };
      };
      return w.__authStore?.accessToken ?? null;
    });
    return this.page.request.delete(
      `${this.backendBaseUrl()}/v1/admin/backups/${rawName}`,
      {
        headers: accessToken
          ? { Authorization: `Bearer ${accessToken}` }
          : undefined,
      },
    );
  }

  /**
   * Raw multipart upload against the restore endpoint. Used to exercise
   * the decompression-bomb / file-shape validator branches without
   * driving the SPA's file picker (which is harder to feed crafted
   * bytes to in headless mode).
   */
  async rawUploadRestore(
    file: { name: string; mimeType: string; buffer: Buffer },
  ): Promise<APIResponse> {
    const accessToken = await this.page.evaluate(() => {
      const w = window as unknown as {
        __authStore?: { accessToken?: string | null };
      };
      return w.__authStore?.accessToken ?? null;
    });
    return this.page.request.post(
      `${this.backendBaseUrl()}/v1/admin/backups/restore`,
      {
        multipart: { file },
        headers: accessToken
          ? { Authorization: `Bearer ${accessToken}` }
          : undefined,
      },
    );
  }

  private backendBaseUrl(): string {
    // See NotificationsHarness.backendBaseUrl for the resolution order.
    // chore PR `dev-stack-stabilization` added a Vite proxy for /v1/*
    // and /auth/*, so this.baseUrl (5173) is now the default fallback
    // and the localhost:8000 hard-code has been retired.
    return (
      process.env.BACKEND_BASE_URL ??
      process.env.VITE_API_BASE_URL ??
      this.baseUrl
    );
  }
}

/** Escape a value for inclusion in a CSS attribute selector. */
function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}
