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
  team_id: string;
  project_names: string[];
  project_ids: string[];
}

export interface SeedOptions {
  projectNames: string[];
  password?: string;
  email?: string;
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
