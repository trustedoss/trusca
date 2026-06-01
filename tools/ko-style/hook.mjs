#!/usr/bin/env node
/**
 * ko-style Claude Code PostToolUse hook.
 *
 * Wired in .claude/settings.json on Edit|Write|MultiEdit. Reads the hook
 * payload from stdin, and if the edited file is a Korean docs-mirror .md /
 * .mdx, lints just that file:
 *
 *   - S1 / S2 finding(s) → prints them to stderr and exits 2, so Claude
 *     receives the feedback and self-corrects the 번역투.
 *   - only S3 finding(s) → silent, exit 0 (advisory; don't nag).
 *   - anything else (non-KO path, non-md, no findings, parse error) → exit 0.
 *
 * Never blocks edits outside the Korean docs tree.
 */
import * as path from "node:path";
import { REPO_ROOT, KO_DOCS_ROOT, loadRules, lintFile } from "./lint.mjs";

const SEVERITY_RANK = { S1: 3, S2: 2, S3: 1 };

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

  const rel = path.isAbsolute(filePath) ? path.relative(REPO_ROOT, filePath) : filePath;
  const koPrefix = path.relative(REPO_ROOT, KO_DOCS_ROOT);
  if (!rel.startsWith(koPrefix) || !(rel.endsWith(".md") || rel.endsWith(".mdx"))) {
    process.exit(0);
  }

  let findings;
  try {
    findings = lintFile(path.join(REPO_ROOT, rel), rel, loadRules());
  } catch {
    process.exit(0); // never break the edit flow on a lint error
  }

  const blocking = findings.filter((f) => SEVERITY_RANK[f.severity] >= SEVERITY_RANK.S2);
  if (blocking.length === 0) process.exit(0);

  process.stderr.write(
    `ko-style: ${blocking.length}건의 번역투를 발견했습니다 (${rel}). 자연스러운 한국어로 고쳐 주세요:\n`,
  );
  for (const f of blocking) {
    process.stderr.write(`  - ${rel}:${f.line} [${f.severity} ${f.category}] “${f.text}” → ${f.suggestion}\n`);
  }
  process.exit(2);
}

main();
