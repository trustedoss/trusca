/**
 * axios HTTP surface — Phase 1 PR #6 task 1.7.
 *
 * Single instance for every authenticated call from the SPA. Responsibilities:
 *
 *   1. Attach `Authorization: Bearer <accessToken>` from the in-memory zustand
 *      store on every request.
 *   2. Send `withCredentials: true` so the refresh cookie (path=/auth) flows
 *      on /auth/refresh calls. Other paths get the cookie too — which is fine
 *      because the backend scopes the cookie tightly so it only serializes
 *      onto /auth.
 *   3. On a 401, invoke /auth/refresh **once**, swap the access token, and
 *      replay the original request. Concurrent 401s coalesce into a single
 *      in-flight refresh promise (singleflight) to avoid a refresh storm and
 *      the resulting "reuse detected" cascade from the backend.
 *   4. If /auth/refresh itself fails, reset auth state and dispatch a
 *      `auth:expired` window event. The router-aware listener navigates to
 *      /login — keeping this module router-free.
 *   5. Convert every non-2xx into a {@link ProblemError} so call sites have
 *      one error type to catch.
 *
 * Hard rules (CLAUDE.md §3 + 1.7 brief):
 *   - No router import here.
 *   - Access token never persisted (memory only).
 *   - import.meta.env is read inside `resolveBaseUrl()` rather than cached at
 *     module scope (CLAUDE.md rule #11 spirit). axios.create() runs once but
 *     `baseURL` is fixed at construction; tests stub by mocking the module.
 *   - The dev `window.__setAccessToken` / `window.__authStore` hooks are
 *     installed only when `import.meta.env.DEV` is truthy so the production
 *     bundle does not ship them.
 */
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import { ProblemError, parseProblemBody } from "@/lib/problem";
import { effectiveRole } from "@/lib/roles";
import { type AuthUser, useAuthStore } from "@/stores/authStore";

interface RetryableConfig extends InternalAxiosRequestConfig {
  /** Marker so we never refresh-and-retry the same request twice. */
  _retry?: boolean;
  /** Bypass the 401-refresh dance for /auth/refresh itself. */
  _skipAuthRefresh?: boolean;
}

function resolveBaseUrl(): string {
  // Read at call time, not at module load. Test setups can swap this through
  // `vi.stubEnv` and a fresh import; tests hit the helper directly.
  const raw =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
    "http://localhost:8000";
  return raw.replace(/\/+$/, "");
}

export const API_BASE_URL_GETTER = resolveBaseUrl;

export const api: AxiosInstance = axios.create({
  baseURL: resolveBaseUrl(),
  withCredentials: true,
  // We always negotiate JSON. The backend's RFC 7807 errors arrive with
  // application/problem+json — axios will still parse them as JSON because
  // the body is JSON; the response interceptor inspects `data` directly.
  headers: { Accept: "application/json" },
});

// ---------- request interceptor: attach bearer token ------------------------

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization =
      `Bearer ${token}`;
  }
  return config;
});

// ---------- singleflight refresh -------------------------------------------

let inflightRefresh: Promise<string> | null = null;

/**
 * POST /auth/refresh once, even if N concurrent requests get a 401 in the
 * same tick. Returns the new access token on success.
 *
 * Critical for security review: a refresh-token-rotation backend treats a
 * second use of the same refresh token as compromise → the user is logged
 * out. Without singleflight, two parallel 401s would fire two refreshes; the
 * second would arrive with the just-rotated cookie and trip RefreshReuseDetected.
 */
function refreshOnce(): Promise<string> {
  if (inflightRefresh) {
    return inflightRefresh;
  }
  inflightRefresh = (async () => {
    try {
      const response = await api.post<{ access_token: string }>(
        "/auth/refresh",
        null,
        // _skipAuthRefresh prevents the response interceptor from looping
        // on a /auth/refresh that itself returns 401.
        { _skipAuthRefresh: true } as AxiosRequestConfig & {
          _skipAuthRefresh?: boolean;
        },
      );
      const next = response.data?.access_token;
      if (typeof next !== "string" || next.length === 0) {
        throw new Error("refresh response missing access_token");
      }
      useAuthStore.getState().setAccessToken(next);
      return next;
    } catch (refreshErr) {
      // L-2 (PR #6 follow-up): emit reset + auth:expired EXACTLY ONCE per
      // singleflight refresh, not once per concurrent awaiter. Two simultaneous
      // 401s share the same `inflightRefresh` promise, so each awaiter would
      // otherwise re-run reset()/dispatchEvent() in its own catch block. The
      // /login redirect is idempotent today, but a future non-idempotent
      // listener (analytics, toast) would multiply-fire.
      useAuthStore.getState().reset();
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("auth:expired"));
      }
      throw refreshErr;
    } finally {
      // Always clear so a later 401 can trigger a fresh refresh.
      inflightRefresh = null;
    }
  })();
  return inflightRefresh;
}

// ---------- response interceptor: 401 → refresh, errors → ProblemError ------

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetryableConfig | undefined;
    const status = error.response?.status;

    if (
      status === 401 &&
      config &&
      !config._retry &&
      !config._skipAuthRefresh
    ) {
      config._retry = true;
      try {
        const nextToken = await refreshOnce();
        config.headers = config.headers ?? {};
        (config.headers as Record<string, string>).Authorization =
          `Bearer ${nextToken}`;
        return api.request(config);
      } catch {
        // L-2 fix: reset()+auth:expired now happen inside refreshOnce()'s catch
        // so they fire exactly once across N concurrent 401s. Here we only let
        // the original 401 fall through to the ProblemError mapping below.
      }
    }

    // Convert to ProblemError. Transport-level failures (no response) get
    // status 0 so callers can distinguish from a real backend error.
    if (!error.response) {
      throw new ProblemError(error.message || "network error", {
        status: 0,
        title: "network",
        detail: error.message || "network error",
        problem: null,
      });
    }
    const parsed = parseProblemBody(error.response.data, {
      status: error.response.status,
      statusText: error.response.statusText,
    });
    throw new ProblemError(parsed.detail, {
      status: error.response.status,
      title: parsed.title,
      detail: parsed.detail,
      problem: parsed.problem,
    });
  },
);

// ---------- typed wrappers (auth surface) ----------------------------------

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  full_name: string;
}

export interface MembershipWire {
  team_id: string;
  team_name: string;
  role: string;
}

export interface UserPublicWire {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  /** Present on /auth/me; absent on /auth/register. */
  memberships?: MembershipWire[];
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** Map the wire shape to the canonical {@link AuthUser}. */
function toAuthUser(u: UserPublicWire): AuthUser {
  const memberships = u.memberships ?? [];
  const teams = memberships.map((m) => ({
    id: m.team_id,
    name: m.team_name,
    role: m.role,
  }));
  return {
    id: u.id,
    email: u.email,
    displayName: u.full_name ?? u.email,
    // H-2: /auth/me has no top-level role — promote the HIGHEST membership
    // role so a team_admin is not treated as a developer across the app.
    role: effectiveRole(
      u.is_superuser,
      memberships.map((m) => m.role),
    ),
    isActive: u.is_active,
    isSuperuser: u.is_superuser,
    // memberships are ordered oldest-first by the backend; [0] is the stable
    // default team used for project creation / write scoping.
    teamId: teams[0]?.id ?? null,
    teams,
  };
}

export async function postLogin(payload: LoginPayload): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/auth/login", payload);
  return data;
}

export async function postRegister(
  payload: RegisterPayload,
): Promise<UserPublicWire> {
  const { data } = await api.post<UserPublicWire>("/auth/register", payload);
  return data;
}

export async function fetchMe(): Promise<AuthUser> {
  const { data } = await api.get<UserPublicWire>("/auth/me");
  return toAuthUser(data);
}

export async function postLogout(): Promise<void> {
  // 204 No Content — axios resolves with undefined data. We tolerate any
  // outcome since the caller already plans to clear local state.
  await api.post("/auth/logout", null, {
    _skipAuthRefresh: true,
  } as AxiosRequestConfig & { _skipAuthRefresh?: boolean });
}

// ---------- forgot / reset password (chore A1) -----------------------------

/**
 * Request a password-reset link. The backend (PR #22) ALWAYS returns 204
 * regardless of whether the email exists, so the UI must show the same
 * confirmation either way (anti-enumeration, CWE-204).
 *
 * Per-email cooldown surfaces as a Retry-After header — we don't read it
 * here because surfacing it would itself leak existence; the caller treats
 * every outcome (including transport errors) the same.
 */
export async function postForgotPassword(email: string): Promise<void> {
  await api.post("/auth/forgot-password", { email }, {
    _skipAuthRefresh: true,
  } as AxiosRequestConfig & { _skipAuthRefresh?: boolean });
}

/**
 * Confirm a password reset using the one-shot token from the email link.
 * On 204 the backend revokes every refresh token for the user.
 * On 422 (invalid/expired/reused token) the call throws ProblemError —
 * the caller maps `problem.title` to an i18n key for inline display.
 */
export async function postResetPassword(
  token: string,
  newPassword: string,
): Promise<void> {
  await api.post(
    "/auth/reset-password",
    { token, new_password: newPassword },
    {
      _skipAuthRefresh: true,
    } as AxiosRequestConfig & { _skipAuthRefresh?: boolean },
  );
}

// ---------- dev-only window hooks (e2e harness bridge) ---------------------

if (import.meta.env.DEV && typeof window !== "undefined") {
  // The Playwright harness (tests/_harness/auth.ts) reads these to (a) inject
  // an expired token into the store and (b) assert isAuthenticated.
  (window as unknown as Record<string, unknown>).__setAccessToken = (
    token: string | null,
  ) => useAuthStore.getState().setAccessToken(token);
  Object.defineProperty(window, "__authStore", {
    configurable: true,
    get: () => ({
      get isAuthenticated(): boolean {
        return useAuthStore.getState().isAuthenticated;
      },
      // Phase 5 manual-aligned harnesses (Notifications / Profile /
      // AdminBackup) issue direct fetch() against the backend with an
      // explicit Authorization header — the SPA's axios interceptor
      // doesn't fire for `page.request.*` calls. Cross-origin fetches
      // also drop the SPA's cookies, so the harness must read the
      // in-memory access token to forward it. Read-only by design.
      get accessToken(): string | null {
        return useAuthStore.getState().accessToken;
      },
    }),
  });
}
