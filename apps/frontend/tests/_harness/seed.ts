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
  /**
   * The kind='sbom' received-SBOM scan seeded on the first project when
   * `SeedOptions.withSbom` is set (model 3). Open `/scans/<id>` to assert the
   * conformance panel. `null` when `withSbom` was off.
   */
  sbom_scan_id?: string | null;
  /**
   * feat/g7-conformance. Number of advisory G7 checks appended to the seeded
   * conformance verdict — 4 when `SeedOptions.withG7` is set, else 0.
   */
  g7_check_count?: number;
  /** Number of components attached to the first project's scan (0 by default). */
  component_count?: number;
  /** Number of vulnerability findings attached to the first project's scan. */
  vulnerability_count?: number;
  /**
   * Phase C KEV e2e. Number of vulnerability-mode CVEs flagged as CISA KEV
   * entries (`kev=true` + `kev_date_added` + a `kev_due_date` cycled through
   * `SeedOptions.kevDueSpread`). Always ≤ `vulnerability_count`; 0 when
   * `kevCount` was off.
   */
  kev_count?: number;
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
  /**
   * G3.3 source-tree e2e. Absolute path (inside the backend/worker container)
   * of the preserved-source tarball staged for the first project's scan when
   * `SeedOptions.withSource` is set. `null` when preservation was skipped
   * (quota / over-cap — the service never raises) or `withSource` was off.
   * The spec does not read the path directly (it drives the viewer through the
   * harness); it is surfaced for debuggability of the seed run.
   */
  source_tarball?: string | null;
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
   * Seed a kind='sbom' (received-SBOM) succeeded scan on the FIRST project plus
   * its conformance verdict (model 3). Independent of `withScan`. The scan id
   * comes back as `SeedSummary.sbom_scan_id` — open `/scans/<id>` to assert the
   * conformance panel.
   */
  withSbom?: boolean;
  /**
   * feat/g7-conformance. Append 4 advisory G7 AI minimum-element checks to
   * the seeded conformance verdict (2 clusters: `slp` + `models`; statuses
   * pass x2 / absent-warn x1 / human-review x1 — pinned from the real
   * evaluator over the recorded `aibom-owasp-1_7.json` fixture). Implies
   * `withSbom`. The count comes back as `SeedSummary.g7_check_count`.
   */
  withG7?: boolean;
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
   * Phase C KEV e2e. Flag the FIRST N `vulnerabilityCount` CVEs (seed-plan
   * order) as CISA KEV entries: `kev=true`, `kev_date_added` (today − 30 d)
   * and a `kev_due_date` cycled through `kevDueSpread`. Requires
   * `vulnerabilityCount >= kevCount` — the script exits 2 (ValueError)
   * otherwise, and the helper throws a descriptive Error. Default: 0.
   */
  kevCount?: number;
  /**
   * SLA-state cycle for the `kevCount` due dates. Comma-separated tokens
   * from `overdue` (today − 3 d) / `imminent` (today + 3 d) / `ok`
   * (today + 30 d) — the offsets sit inside the FE `dueDate.ts` bands with
   * margin so a UTC↔local day skew never flips a state. Defaults to
   * `"overdue,imminent,ok"` in the Python script (all three states seeded
   * when `kevCount >= 3`).
   */
  kevDueSpread?: string;
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
   * Test-hardening Tier N follow-up. Mint + persist a refresh token for this
   * (password) user so the spec can authenticate via the refresh-cookie path
   * (`AuthHarness.loginViaRefreshCookie`) instead of `POST /auth/login`. This
   * keeps a full single-IP suite run under the 5/min login limiter — the
   * artifact that made the e2e suite non-single-pass-runnable. `noPassword`
   * already implies this; set it explicitly for a normal password user.
   */
  withRefreshToken?: boolean;
  /**
   * Marathon bundle 5 (4a). Insert N unread notifications for the
   * primary user so screenshot captures can show the header bell with
   * a non-zero badge. Kinds rotate through the closed enum so the list
   * page renders mixed icons. Default: 0.
   */
  notificationCount?: number;
  /**
   * G3.3 source-tree e2e. Stage a real preserved-source tarball for the
   * first project's succeeded scan so the `/source-tree` + `/source-file`
   * endpoints return a populated tree (lights up
   * `source_tree.spec.ts` S3/S4) instead of the 404 empty-state. Implies
   * `withScan`.
   *
   * IMPORTANT — execution location: the preserved tarball is written under
   * the backend's `WORKSPACE_HOST_PATH` (`/tmp/trustedoss`), which in the dev
   * stack is the `scan-workspace` Docker volume mounted into the backend +
   * worker containers — NOT a host directory. So when this option is set the
   * seed MUST run *inside the backend container* (via `docker-compose exec`)
   * so the tarball lands in the same volume the API reads. The plain
   * host-`python3` path (used by every other seed) would write the tarball to
   * the host filesystem where the container can never see it, and the viewer
   * would still 404. The helper switches to the container-exec runner
   * automatically when this flag is true; the DB rows are written to the same
   * shared Postgres either way. Default: off.
   */
  withSource?: boolean;
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

  // Script flags WITHOUT the leading interpreter/script token, so the same
  // list works for both the host runner (`python3 <script> ...`) and the
  // container runner (`python -m scripts.seed_e2e_user ...`).
  const scriptArgs = ["--project-names", opts.projectNames.join(",")];
  if (opts.password) {
    scriptArgs.push("--password", opts.password);
  }
  if (opts.email) {
    scriptArgs.push("--email", opts.email);
  }
  if (opts.withScan || (opts.componentCount ?? 0) > 0 || opts.withSource) {
    // --component-count > 0 and --with-source both imply --with-scan in the
    // script; we still pass the flag explicitly when the caller asked for a
    // scan but no components, so the spec stays self-documenting at the call
    // site.
    scriptArgs.push("--with-scan");
  }
  if (opts.withSbom || opts.withG7) {
    // Independent of --with-scan: seeds a kind='sbom' scan + conformance verdict
    // on the first project (model 3). `--with-g7` implies `--with-sbom` in the
    // script too; we pass it explicitly so the call site stays self-documenting.
    scriptArgs.push("--with-sbom");
  }
  if (opts.withG7) {
    scriptArgs.push("--with-g7");
  }
  if ((opts.componentCount ?? 0) > 0) {
    scriptArgs.push("--component-count", String(opts.componentCount));
  }
  if (opts.componentPrefix) {
    scriptArgs.push("--component-prefix", opts.componentPrefix);
  }
  if ((opts.vulnerabilityCount ?? 0) > 0) {
    scriptArgs.push("--vulnerability-count", String(opts.vulnerabilityCount));
    // The Python script's `--vulnerability-count` flag implies `--with-scan`
    // there too, but we set both anyway for consistency.
    if (!scriptArgs.includes("--with-scan")) scriptArgs.push("--with-scan");
  }
  if (opts.vulnerabilitySeverityMix) {
    scriptArgs.push(
      "--vulnerability-severity-mix",
      opts.vulnerabilitySeverityMix,
    );
  }
  if ((opts.kevCount ?? 0) > 0) {
    scriptArgs.push("--kev-count", String(opts.kevCount));
  }
  if (opts.kevDueSpread) {
    scriptArgs.push("--kev-due-spread", opts.kevDueSpread);
  }
  if (opts.withObligations) {
    scriptArgs.push("--with-obligations");
  }
  if (opts.withSource) {
    scriptArgs.push("--with-source");
  }
  if (opts.superAdmin) {
    scriptArgs.push("--super-admin");
  }
  if ((opts.extraMembers ?? 0) > 0) {
    scriptArgs.push("--extra-members", String(opts.extraMembers));
  }
  if (opts.extraTeamAdmin) {
    scriptArgs.push("--extra-team-admin");
  }
  if (opts.withOAuthIdentity) {
    scriptArgs.push("--with-oauth-identity", opts.withOAuthIdentity);
  }
  if (opts.withRefreshToken) {
    scriptArgs.push("--with-refresh-token");
  }
  if (opts.noPassword) {
    scriptArgs.push("--no-password");
  }
  if ((opts.notificationCount ?? 0) > 0) {
    scriptArgs.push("--with-notifications", String(opts.notificationCount));
  }

  // G3.3 — `withSource` writes a preserved-source tarball under the backend's
  // WORKSPACE_HOST_PATH (`/tmp/trustedoss`), which is the `scan-workspace`
  // Docker volume in the dev stack, NOT a host directory. The tarball must
  // land in the same volume the API reads, so this seed runs INSIDE the
  // backend container. Every other seed stays on the fast host-`python3`
  // path. See SeedOptions.withSource for the rationale.
  if (opts.withSource) {
    return runSeedInContainer(scriptArgs);
  }
  return runSeedOnHost(scriptHost, scriptArgs);
}

/**
 * Host runner — the default for every seed except `withSource`. Runs the
 * Python script directly against the host-mapped Postgres.
 */
function runSeedOnHost(
  scriptHost: string,
  scriptArgs: string[],
): SeedSummary {
  const args = [scriptHost, ...scriptArgs];

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

/**
 * Container runner — used only for `withSource`. Executes the seed module
 * inside the backend container (`docker-compose exec -T backend python -m
 * scripts.seed_e2e_user ...`) so the preserved-source tarball it writes lands
 * in the `scan-workspace` volume the API reads back. The container's own
 * `DATABASE_URL` (pointing at `postgres:5432`) + `APP_ENV` are used; we never
 * override them here.
 *
 * `COMPOSE_FILE` / `COMPOSE_SERVICE` env vars allow CI to point at a
 * non-default compose file or service name. The repo standard is the V1
 * `docker-compose` binary (hyphen) per CLAUDE.md core rule #10.
 */
function runSeedInContainer(scriptArgs: string[]): SeedSummary {
  const composeBin = process.env.COMPOSE_BIN ?? "docker-compose";
  const composeFile =
    process.env.COMPOSE_FILE ??
    path.join(REPO_ROOT, "docker-compose.dev.yml");
  const service = process.env.COMPOSE_SERVICE ?? "backend";

  const args = [
    "-f",
    composeFile,
    "exec",
    "-T",
    service,
    "python",
    "-m",
    "scripts.seed_e2e_user",
    ...scriptArgs,
  ];

  const result = spawnSync(composeBin, args, {
    encoding: "utf8",
    env: process.env,
  });
  if (result.error) {
    throw new Error(
      `failed to spawn ${composeBin} for in-container seed (is the dev ` +
        `stack up?): ${result.error.message}`,
    );
  }
  if (result.status !== 0) {
    throw new Error(
      `in-container seed exited ${result.status} via ${composeBin} exec ` +
        `${service}: ${result.stderr.trim()}`,
    );
  }
  return parseSeedSummary(result.stdout);
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
