/**
 * E2E seed helper — Phase 2 PR #9.
 *
 * Bridges the gap between Playwright (Node) and the Postgres-backed
 * fixtures the spec needs. The auth surface has no team-creation endpoint
 * by design (Phase 3 onboarding wizard work) and freshly-registered users
 * have no memberships, so a brand-new user cannot create a project via
 * REST. This helper invokes the Python seed script
 * (`apps/backend/scripts/seed_e2e_user.py`) and parses the JSON summary
 * line so specs can use the resulting credentials + project ids.
 *
 * Failure modes:
 *   - Python or backend unreachable → throws a descriptive Error so the
 *     spec can `test.skip(...)` rather than fail.
 *   - Backend container in use rather than host runtime: the helper picks
 *     the docker-compose pattern when DOCKER_COMPOSE env var is set,
 *     otherwise runs `python3` from PATH.
 */
import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { existsSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const SEED_SCRIPT_REL = "apps/backend/scripts/seed_e2e_user.py";

export interface SeedSummary {
  email: string;
  password: string;
  user_id: string;
  /**
   * Phase 4 PR #13. Mirrors the ``--super-admin`` flag — when true the
   * primary user has ``User.is_superuser=True`` and the SPA's existence-hide
   * guard renders the admin layout. Always present in v2 seed output (older
   * scripts emit ``undefined`` and the admin specs treat that as ``false``).
   */
  is_super_admin?: boolean;
  team_id: string;
  project_names: string[];
  project_ids: string[];
  /** Populated when SeedOptions.withScan is true. Same length as project_ids. */
  scan_ids?: string[];
  /** Number of components attached to the first project's scan (0 by default). */
  component_count?: number;
  /** Number of vulnerability findings attached to the first project's scan. */
  vulnerability_count?: number;
  /** Number of obligation rows attached to the seeded licenses (PR #13). */
  obligation_count?: number;
  /**
   * Phase 4 PR #13. Per-user metadata for users seeded via
   * ``--extra-members``. The list is ordered (index 0 = first extra user).
   * When ``--extra-team-admin`` is set, the first extra is ``team_admin``;
   * the rest are ``developer``.
   */
  extra_members?: Array<{
    user_id: string;
    email: string;
    role: "team_admin" | "developer";
  }>;
  /**
   * Phase 5 D bundle. Populated when ``SeedOptions.withOAuthIdentity`` is
   * set. Carries the seeded row's id + provider + the deterministic
   * ``provider_user_id`` fixture so the spec can correlate the row's
   * server-side state with what the SPA renders.
   */
  oauth_identity?: {
    id: string;
    provider: "github" | "google";
    provider_user_id: string;
  } | null;
  /**
   * Marathon bundle 2 (D1). When ``SeedOptions.noPassword`` is true the
   * seed stores an empty ``hashed_password`` and password login is
   * impossible. To let the e2e authenticate as the OAuth-only user
   * without driving a real IdP callback, the seed mints + persists a
   * refresh token and exposes it here. The spec sets this as the
   * ``refresh_token`` HttpOnly cookie via
   * ``page.context().addCookies(...)`` and the SPA bootstrap then
   * trades it for an access token via ``POST /v1/auth/refresh``.
   */
  refresh_token?: {
    token: string;
    cookie_name: string;
    expires_at: string;
  } | null;
  /** True when ``SeedOptions.noPassword`` was honored. */
  no_password?: boolean;
  /** Number of unread notifications inserted (Marathon bundle 5 / 4a). */
  notification_count?: number;
}

export interface SeedOptions {
  projectNames: string[];
  password?: string;
  email?: string;
  /**
   * Seed a `succeeded` scan per project and wire it as
   * `project.latest_scan_id`. Required for the project-detail flows.
   */
  withScan?: boolean;
  /**
   * Number of components to attach to the first project's scan. Implies
   * `withScan`. Default: 0 (no components seeded). Phase 3 PR #10
   * scenarios pass 50 for the small flows and 10000 for the virtual-scroll
   * scenario.
   */
  componentCount?: number;
  /**
   * Name prefix for the seeded components. Component i is named
   * `{prefix}-{i}`. Default: `comp`. Search-flow scenarios fix this to a
   * known string (e.g. `react`) so the spec can search by substring
   * without learning ids.
   */
  componentPrefix?: string;
  /**
   * Phase 3 PR #11. Number of CVE findings to attach to the first
   * project's scan. Each finding gets a fresh component_version + a fresh
   * Vulnerability with deterministic severity + status mix. Implies
   * `withScan`. Default: 0 (no findings seeded).
   */
  vulnerabilityCount?: number;
  /**
   * Optional severity mix override for `vulnerabilityCount`. Format:
   *   "critical:N,high:N,medium:N,low:N,info:N,unknown:N"
   * The script clamps the sum to `vulnerabilityCount`. Defaults to the
   * built-in mix (2 critical / 5 high / 10 medium / 20 low / 5 info /
   * 2 unknown).
   */
  vulnerabilitySeverityMix?: string;
  /**
   * Phase 3 PR #13. When true, attach a small obligation catalog to each
   * seed-license created by `componentCount`. No-op when `componentCount`
   * is 0 because no seed-licenses exist.
   */
  withObligations?: boolean;
  /**
   * Phase 4 PR #13. Mark the seeded primary user as a super-admin
   * (``User.is_superuser=True``). Required for the admin-panel e2e
   * scenarios — without it the existence-hide guard renders 404.
   */
  superAdmin?: boolean;
  /**
   * Phase 4 PR #13. Seed N additional users in the same team as the
   * primary user. Their emails follow ``e2e-extra-{i}-<suffix>@example.com``
   * and they share the primary user's password. Output JSON gets an
   * ``extra_members`` list with per-user ``user_id``/``email``/``role``.
   */
  extraMembers?: number;
  /**
   * Phase 4 PR #13. When set in addition to ``extraMembers``, the *first*
   * extra user is given ``team_admin`` role instead of ``developer``.
   */
  extraTeamAdmin?: boolean;
  /**
   * Phase 5 D bundle. Insert one OAuthIdentity row for the primary user
   * pinned to the chosen provider. Used by `auth_and_profile.spec.ts` to
   * exercise the Unlink-with-fallback scenario without driving a real
   * IdP callback. The primary user still receives the password the seed
   * normally sets, so the SPA login flow keeps working — the OAuth
   * identity is a secondary auth method.
   */
  withOAuthIdentity?: "github" | "google";
  /**
   * Marathon bundle 2 (D1). Provision an OAuth-only user — empty
   * ``hashed_password``, requires ``withOAuthIdentity``. The seed mints +
   * persists a refresh token whose value comes back in
   * ``SeedSummary.refresh_token`` so the spec can authenticate via the
   * refresh-cookie path (the only viable entry for an OAuth-only user
   * without driving a real IdP callback).
   *
   * Schema-level guard: passing ``noPassword: true`` without
   * ``withOAuthIdentity`` results in the script exiting with code 2
   * (validation failure) and the helper throws a descriptive Error.
   */
  noPassword?: boolean;
  /**
   * Marathon bundle 5 (4a). Insert N unread notifications for the
   * primary user so screenshot captures can show the header bell with
   * a non-zero badge. Kinds rotate through the closed enum so the list
   * page renders mixed icons. Default: 0.
   */
  notificationCount?: number;
}

/**
 * Run the Python seed script and return the parsed JSON summary.
 *
 * The script writes one JSON line to stdout. Any other line (including
 * structlog output) is ignored. Throws an Error with the captured stderr
 * when the script exits non-zero so the spec can decide whether to skip.
 */
export function seedE2eUser(opts: SeedOptions): SeedSummary {
  const scriptHost = path.join(REPO_ROOT, SEED_SCRIPT_REL);
  if (!existsSync(scriptHost)) {
    throw new Error(`seed script not found: ${scriptHost}`);
  }

  const args = [
    scriptHost,
    "--project-names",
    opts.projectNames.join(","),
  ];
  if (opts.password) {
    args.push("--password", opts.password);
  }
  if (opts.email) {
    args.push("--email", opts.email);
  }
  if (opts.withScan || (opts.componentCount ?? 0) > 0) {
    // --component-count > 0 implies --with-scan in the script; we still
    // pass the flag explicitly when the caller asked for a scan but no
    // components, so the spec stays self-documenting at the call site.
    args.push("--with-scan");
  }
  if ((opts.componentCount ?? 0) > 0) {
    args.push("--component-count", String(opts.componentCount));
  }
  if (opts.componentPrefix) {
    args.push("--component-prefix", opts.componentPrefix);
  }
  if ((opts.vulnerabilityCount ?? 0) > 0) {
    args.push("--vulnerability-count", String(opts.vulnerabilityCount));
    // The Python script's `--vulnerability-count` flag implies `--with-scan`
    // there too, but we set both anyway for consistency.
    if (!args.includes("--with-scan")) args.push("--with-scan");
  }
  if (opts.vulnerabilitySeverityMix) {
    args.push("--vulnerability-severity-mix", opts.vulnerabilitySeverityMix);
  }
  if (opts.withObligations) {
    args.push("--with-obligations");
  }
  if (opts.superAdmin) {
    args.push("--super-admin");
  }
  if ((opts.extraMembers ?? 0) > 0) {
    args.push("--extra-members", String(opts.extraMembers));
  }
  if (opts.extraTeamAdmin) {
    args.push("--extra-team-admin");
  }
  if (opts.withOAuthIdentity) {
    args.push("--with-oauth-identity", opts.withOAuthIdentity);
  }
  if (opts.noPassword) {
    args.push("--no-password");
  }
  if ((opts.notificationCount ?? 0) > 0) {
    args.push("--with-notifications", String(opts.notificationCount));
  }

  // Default DATABASE_URL points at the host-mapped Postgres exposed by
  // docker-compose dev. SECRET_KEY is required by core.config.secret_key()
  // — but only when APP_ENV != "dev". We force APP_ENV=dev so the helper
  // works without secret-shuffling.
  const env = {
    ...process.env,
    APP_ENV: process.env.APP_ENV ?? "dev",
    DATABASE_URL:
      process.env.DATABASE_URL ??
      "postgresql+asyncpg://trustedoss:trustedoss@localhost:5432/trustedoss",
  };

  // Resolution order:
  //   1. PYTHON env override (CI / explicit)
  //   2. python3.11 if available — backend code uses 3.10+ syntax
  //      (`from datetime import UTC`) that breaks on macOS' default 3.9.
  //   3. python3 (last resort).
  // The first interpreter that returns exit 0 wins. Otherwise we report
  // the last failure to the caller.
  const candidates: string[] = [];
  if (process.env.PYTHON) candidates.push(process.env.PYTHON);
  candidates.push("python3.11", "python3");

  let lastResult: SpawnSyncReturns<string> | undefined;
  let lastInterpreter = "";
  for (const interpreter of candidates) {
    const result = spawnSync(interpreter, args, { encoding: "utf8", env });
    lastResult = result;
    lastInterpreter = interpreter;
    if (result.error) {
      // ENOENT — interpreter not found; fall through to the next candidate.
      continue;
    }
    if (result.status === 0) {
      return parseSeedSummary(result.stdout);
    }
    // Non-zero exit but the interpreter ran — surface the error directly.
    throw new Error(
      `seed script exited ${result.status} via ${interpreter}: ${result.stderr.trim()}`,
    );
  }

  if (lastResult?.error) {
    throw new Error(
      `failed to spawn ${lastInterpreter}: ${lastResult.error.message}`,
    );
  }
  throw new Error("no python interpreter could run the seed script");
}

function parseSeedSummary(stdout: string): SeedSummary {
  // Pick the last non-empty line that parses as JSON. structlog from helper
  // imports may emit log lines before the summary.
  const lines = stdout
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (line.startsWith("{") && line.endsWith("}")) {
      try {
        return JSON.parse(line) as SeedSummary;
      } catch {
        continue;
      }
    }
  }
  throw new Error(`seed script produced no JSON line — stdout was:\n${stdout}`);
}
