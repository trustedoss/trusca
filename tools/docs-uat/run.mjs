#!/usr/bin/env node
/**
 * docs-uat runner — executes the annotated steps of one doc at one tier.
 *
 *   node tools/docs-uat/run.mjs --tier=gate --doc=quickstart.md
 *
 * Reads the manifest (rebuilt fresh from the docs so the run can never drift
 * from the source markdown), filters to the requested doc + tier + lang (EN),
 * and dispatches each step in document order:
 *
 *   shell  → run the literal fenced command (ctx=host) and check exit code
 *   api    → fetch the URL (with optional retry) and check the status code
 *   sql    → run SQL in the postgres container and check the row count
 *   ui     → hand the ui steps to Playwright (playwright.docs-uat.config.ts),
 *            which dispatches each `harness=verb(args)` against the existing
 *            PortalPage / AuthHarness verbs
 *   waiver → skipped, logged with its reason (never silently dropped)
 *
 * ctx=host prelude convention (handoff §5 — "doc command ≠ CI exec context"):
 *   1. ctx=host shell steps run from the repo root.
 *   2. `docker-compose ... exec` gets `-T` injected so it works without a TTY
 *      on CI runners. The doc keeps the human-friendly `exec` form; the
 *      runner adapts it. This is the ONLY rewrite — everything else runs
 *      verbatim, so the test really is the documented command.
 *
 * Pure Node ESM (Node 18+ for global fetch); the only spawned tooling is the
 * frontend's already-installed Playwright for ui steps.
 */
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { buildManifest, MANIFEST_PATH, REPO_ROOT } from "./extract.mjs";

const FRONTEND_DIR = path.join(REPO_ROOT, "apps", "frontend");
const API_BASE = process.env.DOCS_UAT_API_BASE || "http://localhost:8000";

function parseArgs(argv) {
  const out = { tier: "gate", lang: "en", doc: null, dryRun: false };
  for (const a of argv) {
    if (a === "--dry-run") out.dryRun = true;
    else if (a.startsWith("--tier=")) out.tier = a.slice(7);
    else if (a.startsWith("--doc=")) out.doc = a.slice(6);
    else if (a.startsWith("--lang=")) out.lang = a.slice(7);
  }
  return out;
}

/** Inject `-T` into docker-compose exec so it runs without a TTY (CI). */
function rewriteHostShell(cmd) {
  return cmd.replace(/(docker-compose[^\n]*?\bexec)\s+(?!-T\b)/g, "$1 -T ");
}

/** "30x5s" → { attempts: 30, intervalMs: 5000 }; falsy → single attempt. */
function parseRetry(spec) {
  if (!spec) return { attempts: 1, intervalMs: 0 };
  const m = spec.match(/^(\d+)x(\d+)(ms|s)?$/);
  if (!m) return { attempts: 1, intervalMs: 0 };
  const unit = m[3] === "ms" ? 1 : 1000;
  return { attempts: Number(m[1]), intervalMs: Number(m[2]) * unit };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function expectedExit(step) {
  const m = (step.expect || "exit:0").match(/^exit:(\d+)$/);
  return m ? Number(m[1]) : 0;
}

async function runShell(step) {
  const cmd = step.ctx === "host" ? rewriteHostShell(step.code) : step.code;
  const want = expectedExit(step);
  // `retry=NxMs` tolerates warmup races for ctx=host steps that depend on a
  // service that may not be ready yet (e.g. `alembic upgrade head` right after
  // `up` before postgres finishes its healthcheck). The doc shows the plain
  // command; the retry lives only in the (invisible) annotation.
  const { attempts, intervalMs } = parseRetry(step.retry);
  let got = 1;
  for (let i = 1; i <= attempts; i++) {
    const tag = attempts > 1 ? ` (attempt ${i}/${attempts})` : "";
    console.log(`  $ ${cmd.replace(/\n/g, "\n    ")}${tag}`);
    const res = spawnSync("bash", ["-c", cmd], {
      cwd: REPO_ROOT,
      stdio: "inherit",
      env: process.env,
    });
    got = res.status ?? 1;
    if (got === want) return { ok: true };
    if (i < attempts) await sleep(intervalMs);
  }
  return { ok: false, detail: `exit ${got}, expected ${want}` };
}

async function runApi(step) {
  const url = /^https?:\/\//.test(step.url) ? step.url : `${API_BASE}${step.url}`;
  const m = (step.expect || "status:200").match(/^status:(\d+)$/);
  const wantStatus = m ? Number(m[1]) : 200;
  const { attempts, intervalMs } = parseRetry(step.retry);
  let last = "no attempt";
  for (let i = 1; i <= attempts; i++) {
    try {
      const res = await fetch(url, { method: "GET" });
      if (res.status === wantStatus) {
        console.log(`  GET ${url} → ${res.status} (attempt ${i}/${attempts})`);
        return { ok: true };
      }
      last = `status ${res.status}, expected ${wantStatus}`;
    } catch (e) {
      last = `fetch error: ${e.message}`;
    }
    if (i < attempts) {
      process.stdout.write(`  GET ${url} → ${last}; retry ${i}/${attempts}\r`);
      await sleep(intervalMs);
    }
  }
  return { ok: false, detail: last };
}

function runSql(step) {
  // ctx=postgres → psql inside the postgres container. expect=rows:>N | rows:N
  const sql = step.code.trim().replace(/"/g, '\\"');
  const cmd =
    `docker-compose -f docker-compose.dev.yml exec -T postgres ` +
    `psql -U trustedoss -d trustedoss -tAc "${sql}"`;
  console.log(`  $ ${cmd}`);
  const res = spawnSync("bash", ["-c", cmd], { cwd: REPO_ROOT, encoding: "utf8" });
  if ((res.status ?? 1) !== 0) return { ok: false, detail: res.stderr || "psql failed" };
  const out = (res.stdout || "").trim();
  const m = (step.expect || "rows:>0").match(/^rows:(>=|>|=)?(\d+)$/);
  if (!m) return { ok: true };
  const op = m[1] || "=";
  const n = Number(m[2]);
  const value = Number(out.split("\n")[0]);
  const ok = op === ">" ? value > n : op === ">=" ? value >= n : value === n;
  return ok ? { ok: true } : { ok: false, detail: `rows=${out} not ${op}${n}` };
}

/** Hand the ui steps to Playwright in one batch (one login, ordered verbs). */
function runUiBatch(args) {
  console.log(`  → Playwright (playwright.docs-uat.config.ts)`);
  const res = spawnSync(
    "npx",
    ["playwright", "test", "--config=playwright.docs-uat.config.ts"],
    {
      cwd: FRONTEND_DIR,
      stdio: "inherit",
      env: {
        ...process.env,
        DOCS_UAT_MANIFEST: MANIFEST_PATH,
        DOCS_UAT_DOC: args.doc,
        DOCS_UAT_TIER: args.tier,
        DOCS_UAT_LANG: args.lang,
        PLAYWRIGHT_BASE_URL:
          process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173",
      },
    },
  );
  return (res.status ?? 1) === 0 ? { ok: true } : { ok: false, detail: "playwright failed" };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.doc) {
    console.error("usage: run.mjs --doc=<file.md> [--tier=gate] [--lang=en]");
    process.exit(2);
  }

  const manifest = buildManifest();
  // Persist the manifest to disk: the ui-step Playwright spec reads it via
  // DOCS_UAT_MANIFEST (fs.readFileSync). Without this write the spec hits
  // ENOENT and reports "No tests found" — building it only in memory was a
  // bug masked locally by a prior `extract.mjs` run having left the file.
  fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n");

  const steps = manifest.steps
    .filter((s) => s.doc === args.doc && s.lang === args.lang && s.tier === args.tier)
    .sort((a, b) => a.line - b.line);

  if (steps.length === 0) {
    console.error(`docs-uat run: no ${args.tier}-tier steps for ${args.doc}`);
    process.exit(2);
  }

  console.log(
    `docs-uat run: ${steps.length} step(s) — doc=${args.doc} tier=${args.tier} lang=${args.lang}` +
      (args.dryRun ? " [DRY RUN]" : ""),
  );

  const results = [];
  let uiBatchDone = false;
  for (const step of steps) {
    const label = `[${step.id}] kind=${step.kind}${step.ctx ? ` ctx=${step.ctx}` : ""}`;
    if (step.waiver) {
      console.log(`SKIP ${label} — waiver=${step.waiver}`);
      results.push({ id: step.id, status: "waived" });
      continue;
    }
    console.log(`RUN  ${label}`);
    if (args.dryRun) {
      results.push({ id: step.id, status: "dry-run" });
      continue;
    }

    let r;
    if (step.kind === "shell") r = await runShell(step);
    else if (step.kind === "api") r = await runApi(step);
    else if (step.kind === "sql") r = runSql(step);
    else if (step.kind === "ui") {
      if (uiBatchDone) {
        results.push({ id: step.id, status: "ok (ui batch)" });
        continue;
      }
      r = runUiBatch(args); // covers ALL ui steps for this doc/tier at once
      uiBatchDone = true;
    } else if (step.kind === "manual") {
      console.log(`     manual step — transcribed only, not executed`);
      results.push({ id: step.id, status: "manual" });
      continue;
    } else {
      r = { ok: false, detail: `unsupported kind '${step.kind}'` };
    }

    if (r.ok) {
      console.log(`PASS ${label}`);
      results.push({ id: step.id, status: "pass" });
    } else {
      console.error(`::error::docs-uat ${step.id} FAILED — ${r.detail}`);
      results.push({ id: step.id, status: "fail", detail: r.detail });
    }
  }

  const failed = results.filter((r) => r.status === "fail");
  console.log(
    `\ndocs-uat run summary: ${results.length} step(s) — ` +
      `${results.filter((r) => r.status === "pass" || r.status.startsWith("ok")).length} pass, ` +
      `${failed.length} fail, ` +
      `${results.filter((r) => ["waived", "manual", "dry-run"].includes(r.status)).length} skipped.`,
  );
  if (failed.length) process.exit(1);
}

main().catch((e) => {
  console.error(`::error::docs-uat run crashed: ${e.stack || e}`);
  process.exit(1);
});
