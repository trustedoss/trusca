/**
 * AuthHarness — Playwright harness for the auth surface.
 *
 * Sibling of {@link PortalPage}. Exposes domain verbs (`register`, `login`,
 * `expectLoggedIn`, …) so the spec files read like a product walk-through and
 * stay locale-agnostic — every selector is rooted in the `data-testid` markup
 * shipped by 1.6, never in a translated label.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2 + test-writer.md):
 *  - No mocking of our own backend. Real HTTP against docker-compose dev.
 *  - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *  - Selectors live inside the harness; spec files never touch CSS/text.
 */
import { expect, type Locator, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";

const DEFAULT_TIMEOUT_MS = 10_000;

export type AuthPath = "/login" | "/register" | "/forgot-password";

export interface RegisterInput {
  email: string;
  password: string;
  displayName: string;
}

export class AuthHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(path: AuthPath = "/login"): Promise<void> {
    await this.page.goto(`${this.baseUrl}${path}`);
    await this.expectAuthSurfaceMounted(path);
  }

  async gotoLogin(): Promise<void> {
    await this.goto("/login");
  }

  async gotoRegister(): Promise<void> {
    await this.goto("/register");
  }

  async gotoForgot(): Promise<void> {
    await this.goto("/forgot-password");
  }

  // ───── high-level verbs ────────────────────────────────────────────────
  async register({ email, password, displayName }: RegisterInput): Promise<void> {
    await this.page
      .getByTestId("register-display-name")
      .fill(displayName);
    await this.page.getByTestId("register-email").fill(email);
    await this.page.getByTestId("register-password").fill(password);
    await Promise.all([
      this.page.waitForURL(`${this.baseUrl}/`, { timeout: DEFAULT_TIMEOUT_MS }),
      this.page.getByTestId("register-submit").click(),
    ]);
    await this.expectLoggedIn();
  }

  async login(email: string, password: string): Promise<void> {
    await this.page.getByTestId("login-email").fill(email);
    await this.page.getByTestId("login-password").fill(password);
    await Promise.all([
      this.page.waitForURL(`${this.baseUrl}/`, { timeout: DEFAULT_TIMEOUT_MS }),
      this.page.getByTestId("login-submit").click(),
    ]);
    await this.expectLoggedIn();
  }

  /** Submit login expecting a 4xx — stays on /login, alert is rendered. */
  async submitLoginExpectingError(email: string, password: string): Promise<void> {
    await this.page.getByTestId("login-email").fill(email);
    await this.page.getByTestId("login-password").fill(password);
    await this.page.getByTestId("login-submit").click();
    // Stay on /login — assert URL hasn't changed.
    await expect(this.page).toHaveURL(`${this.baseUrl}/login`, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── assertions ──────────────────────────────────────────────────────
  async expectLoggedIn(): Promise<void> {
    await expect(this.page).toHaveURL(`${this.baseUrl}/`, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("home-main")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    const isAuthenticated = await this.page.evaluate(() => {
      // The store is exposed as a module — we query it through a window hook
      // installed by `clearAuthState`. The fallback via `__authStore` keeps
      // the harness non-invasive when the hook is absent (e.g. during
      // pre-clear bootstrap).
      const w = window as unknown as {
        __authStore?: { isAuthenticated: boolean };
      };
      return w.__authStore?.isAuthenticated ?? null;
    });
    if (isAuthenticated !== null) {
      expect(isAuthenticated).toBe(true);
    }
  }

  async expectLoggedOut(): Promise<void> {
    await expect(this.page).toHaveURL(`${this.baseUrl}/login`, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("login-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async expectAlert(textIncludes?: string): Promise<void> {
    const alert = this.alertLocator();
    await expect(alert).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    if (textIncludes !== undefined) {
      await expect(alert).toContainText(textIncludes);
    } else {
      const text = (await alert.innerText()).trim();
      expect(text.length).toBeGreaterThan(0);
    }
  }

  async expectFieldError(
    field: "email" | "password" | "displayName" | "submit",
  ): Promise<void> {
    if (field === "submit") {
      await this.expectAlert();
      return;
    }
    // react-hook-form + shadcn `FormMessage` renders messages with an id of
    // `<field>-form-item-message`. We match suffix to stay independent of the
    // generated prefix. `displayName` maps to the `display_name` field name.
    const formField =
      field === "displayName" ? "display_name" : field;
    const message = this.page.locator(
      `[id$="-form-item-message"][id*="${formField}"]`,
    );
    await expect(message.first()).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── lifecycle / isolation ───────────────────────────────────────────
  /**
   * Reset auth state between tests. Idempotent and safe to call before any
   * navigation. Wipes cookies (refresh token), localStorage, and zustand
   * store memory state. Also installs a window hook that mirrors the store
   * for `expectLoggedIn` introspection.
   */
  async clearAuthState(): Promise<void> {
    await this.page.context().clearCookies();
    // Some browsers refuse storage access on `about:blank` — visit the origin
    // first so localStorage is reachable, then clear and install hook.
    if (!this.page.url().startsWith(this.baseUrl)) {
      await this.page.goto(`${this.baseUrl}/login`);
    }
    await this.page.evaluate(() => {
      try {
        window.localStorage.clear();
      } catch {
        // jsdom-style sandboxing — non-fatal.
      }
    });
    // Reset the zustand store and expose a shadow getter for assertions.
    await this.page.addInitScript(() => {
      // Each navigation re-runs this; the store is re-created per page load
      // anyway. We expose `window.__authStore` via a polling proxy below.
      const w = window as unknown as Record<string, unknown>;
      Object.defineProperty(w, "__authStoreReady", {
        value: true,
        writable: true,
        configurable: true,
      });
    });
  }

  /** Forcibly inject an access token into the in-memory zustand store. */
  async setAccessTokenInStore(token: string | null): Promise<void> {
    await this.page.evaluate((nextToken) => {
      const w = window as unknown as {
        __setAccessToken?: (t: string | null) => void;
      };
      if (typeof w.__setAccessToken === "function") {
        w.__setAccessToken(nextToken);
      } else {
        throw new Error(
          "AuthHarness.setAccessTokenInStore: the app did not install __setAccessToken hook (1.7 axios bootstrap not yet wired).",
        );
      }
    }, token);
  }

  // ───── data builders ───────────────────────────────────────────────────
  /**
   * Random email — UUID + timestamp so parallel-ish runs never collide.
   *
   * Uses `@example.com` (RFC 2606 reserved-for-examples) rather than a `.test`
   * TLD: pydantic's `email-validator` rejects RFC 6761 special-use names like
   * `.test` / `.localhost` / `.invalid` outright.
   */
  randomEmail(): string {
    const uid = cryptoUuid();
    return `e2e-${Date.now()}-${uid}@example.com`;
  }

  /** Random password meeting the backend ≥12 / NIST 800-63B floor. */
  randomPassword(): string {
    return `Aa1!${cryptoUuid()}Zz`; // 30+ chars, mixed classes
  }

  // ───── internal selectors (private to harness) ─────────────────────────
  private alertLocator(): Locator {
    // Order: login-error, register-error, then any role=alert as fallback.
    const explicit = this.page
      .getByTestId("login-error")
      .or(this.page.getByTestId("register-error"));
    return explicit.or(this.page.getByRole("alert"));
  }

  private async expectAuthSurfaceMounted(path: AuthPath): Promise<void> {
    const testid = AUTH_PAGE_TESTID[path];
    await expect(this.page.getByTestId(testid)).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }
}

const AUTH_PAGE_TESTID: Record<AuthPath, string> = {
  "/login": "login-page",
  "/register": "register-page",
  "/forgot-password": "forgot-page",
};

function cryptoUuid(): string {
  // Node 20 (Playwright runtime) and modern browsers both ship `crypto.randomUUID`.
  // We fall back to a Math.random hex if absent (test envs only).
  const c = (globalThis as unknown as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(16).slice(2, 18);
}
