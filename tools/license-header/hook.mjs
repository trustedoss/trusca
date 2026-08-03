#!/usr/bin/env node
/**
 * license-header Claude Code PostToolUse hook.
 *
 * Wired in .claude/settings.json on Edit|Write|MultiEdit, beside the ko-style
 * hook. Reads the hook payload from stdin; if the edited file is in scope and
 * has no SPDX header, inserts one and exits 2 so the agent is told what changed.
 *
 * It FIXES rather than complains. The header is mechanical — there is no
 * judgement for the agent to apply, and a hook that only nags produces a file
 * that fails CI later for a reason a single write could have settled. The
 * exit-2 message still names the file, so the change is never silent.
 *
 * Out-of-scope paths (tests, vendored files, ungated extensions) exit 0
 * untouched: `isTargeted` is the single source of truth for scope, shared with
 * the CI gate, so the hook cannot drift into stamping something the gate would
 * reject — or into skipping something the gate demands.
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { REPO_ROOT, hasHeader, isTargeted, withHeader } from "./lint.mjs";

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (data += c));
    process.stdin.on("end", () => resolve(data));
    // If nothing is piped, don't hang.
    if (process.stdin.isTTY) resolve("");
  });
}

function extractFilePath(payload) {
  try {
    const obj = JSON.parse(payload);
    const ti = obj.tool_input || obj.toolInput || {};
    return ti.file_path || ti.filePath || ti.path || null;
  } catch {
    return null;
  }
}

async function main() {
  const filePath = extractFilePath(await readStdin());
  if (!filePath) process.exit(0);

  const abs = path.resolve(filePath);
  const rel = path.relative(REPO_ROOT, abs);
  // Outside the repo entirely (rel starts with ..) — nothing to do.
  if (rel.startsWith("..")) process.exit(0);
  if (!isTargeted(rel)) process.exit(0);

  let text;
  try {
    text = fs.readFileSync(abs, "utf8");
  } catch {
    process.exit(0);
  }
  if (hasHeader(text)) process.exit(0);

  try {
    fs.writeFileSync(abs, withHeader(text, rel), "utf8");
  } catch (err) {
    console.error(`license-header: could not write ${rel}: ${err.message}`);
    process.exit(0);
  }

  console.error(
    `license-header: inserted the SPDX header into ${rel}.\n` +
      "Every first-party source file carries it (CI gate: `lint (frontend)`). " +
      "If this file is actually third-party, revert the header and add the path " +
      "to tools/license-header/excluded.json with a reason.",
  );
  process.exit(2);
}

main();
