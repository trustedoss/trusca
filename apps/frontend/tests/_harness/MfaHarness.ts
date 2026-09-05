/**
 * MfaHarness: domain verbs for two-step sign-in.
 *
 * Covers the three surfaces the feature added: the enrolment wizard on
 * `/profile`, the code step on `/login`, and signing in with a recovery code.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2):
 *   - No mocking of our own backend. Real HTTP against docker-compose dev.
 *   - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *   - Selectors live inside the harness; spec files never touch CSS/text.
 *
 * Why the harness computes codes rather than reading them from somewhere:
 * a second factor has no fixture. The only way to sign in as somebody with
 * one is to hold their secret and produce the same six digits an
 * authenticator app would, which is what {@link codeFor} does. That makes the
 * scenario an end-to-end statement about interoperability as well: if our
 * transcription of RFC 6238 disagreed with the standard, this is where it
 * would show, because the code is computed here from the secret the server
 * handed out and checked there against the secret the server stored.
 */
import { createHmac } from "node:crypto";

import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

/** RFC 6238 defaults, and what every authenticator app implements. */
const PERIOD_SECONDS = 30;
const DIGITS = 6;

/** RFC 4648 base32, which is how a TOTP secret is written down. */
function decodeBase32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const cleaned = secret.replace(/=+$/, "").replace(/\s+/g, "").toUpperCase();
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const char of cleaned) {
    const index = alphabet.indexOf(char);
    if (index === -1) throw new Error(`not base32: ${char}`);
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((value >>> bits) & 0xff);
    }
  }
  return Buffer.from(out);
}

/**
 * The six digits an authenticator app would show for `secret` right now.
 *
 * RFC 4226 dynamic truncation: the low nibble of the last byte picks the
 * offset, four bytes are read from there, the top bit is masked off, and the
 * remainder modulo 10^6 is zero-padded.
 */
export function codeFor(secret: string, atSeconds?: number): string {
  const now = atSeconds ?? Math.floor(Date.now() / 1000);
  const counter = Math.floor(now / PERIOD_SECONDS);

  const message = Buffer.alloc(8);
  message.writeUInt32BE(Math.floor(counter / 2 ** 32), 0);
  message.writeUInt32BE(counter >>> 0, 4);

  const digest = createHmac("sha1", decodeBase32(secret)).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary =
    ((digest[offset] & 0x7f) << 24) |
    (digest[offset + 1] << 16) |
    (digest[offset + 2] << 8) |
    digest[offset + 3];

  return String(binary % 10 ** DIGITS).padStart(DIGITS, "0");
}

export class MfaHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── enrolment, on /profile ──────────────────────────────────────────

  /**
   * Walk the wizard end to end and return the secret and the recovery codes.
   *
   * Three steps because the backend insists on three, and the middle one is
   * the point: nothing is turned on until a code proves the app really holds
   * the secret, so somebody who closes the tab is left exactly as they were.
   */
  async enrol(password: string): Promise<{ secret: string; codes: string[] }> {
    await this.page.goto(`${this.baseUrl}/profile`);
    await expect(this.page.getByTestId("profile-mfa")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });

    await this.page.getByTestId("profile-mfa-start").click();
    await this.page.getByTestId("profile-mfa-password").fill(password);
    await this.page.getByTestId("profile-mfa-step-up-submit").click();

    const secretField = this.page.getByTestId("profile-mfa-secret");
    await expect(secretField).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const secret = await secretField.inputValue();
    expect(secret).not.toEqual("");

    await this.page.getByTestId("profile-mfa-code").fill(codeFor(secret));
    await this.page.getByTestId("profile-mfa-confirm").click();

    const panel = this.page.getByTestId("profile-mfa-codes");
    await expect(panel).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const codes = await panel.locator("li").allInnerTexts();

    return { secret, codes: codes.map((value) => value.trim()) };
  }

  /** The step-up refuses, and says so, when the proof is wrong. */
  async expectStepUpRefused(wrongPassword: string): Promise<void> {
    await this.page.goto(`${this.baseUrl}/profile`);
    await this.page.getByTestId("profile-mfa-start").click();
    await this.page.getByTestId("profile-mfa-password").fill(wrongPassword);
    await this.page.getByTestId("profile-mfa-step-up-submit").click();

    await expect(this.page.getByTestId("profile-mfa-error")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // And nothing was handed out on the way.
    await expect(this.page.getByTestId("profile-mfa-secret")).toBeHidden();
  }

  // ───── signing in ──────────────────────────────────────────────────────

  /**
   * The password half only. Leaves the browser on the code step.
   *
   * Separate from {@link signInWithCode} so a scenario can assert what is
   * true in between: the password is proved and there is no session yet.
   */
  async submitPassword(email: string, password: string): Promise<void> {
    await this.page.goto(`${this.baseUrl}/login`);
    await this.page.getByTestId("login-email").fill(email);
    await this.page.getByTestId("login-password").fill(password);
    await this.page.getByTestId("login-submit").click();

    await expect(this.page.getByTestId("login-mfa-form")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** Finish the code step with whatever six digits are supplied. */
  async submitCode(code: string): Promise<void> {
    await this.page.getByTestId("login-mfa-code").fill(code);
    await this.page.getByTestId("login-mfa-submit").click();
  }

  /** Password, then the code an app would show. Ends signed in. */
  async signInWithCode(
    email: string,
    password: string,
    secret: string,
  ): Promise<void> {
    await this.submitPassword(email, password);
    await this.submitCode(codeFor(secret));
    await this.page.waitForURL(/\/(dashboard)?$|\/projects/, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** Password, then a recovery code where the six digits are asked for. */
  async signInWithRecoveryCode(
    email: string,
    password: string,
    recoveryCode: string,
  ): Promise<void> {
    await this.submitPassword(email, password);
    await this.submitCode(recoveryCode);
    await this.page.waitForURL(/\/(dashboard)?$|\/projects/, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** The code step stayed, with an error, rather than letting anyone in. */
  async expectCodeRefused(): Promise<void> {
    await expect(this.page.getByTestId("login-error")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("login-mfa-form")).toBeVisible();
  }

  // ───── session ─────────────────────────────────────────────────────────

  /** Sign out through the account menu, as a person would. */
  async signOut(): Promise<void> {
    const trigger = this.page.getByTestId("header-profile-menu");
    await expect(trigger).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await trigger.click();
    await this.page.getByTestId("logout-button").click();
    await this.page.waitForURL(/\/login/, { timeout: DEFAULT_TIMEOUT_MS });
  }
}
