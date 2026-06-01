#!/usr/bin/env node
/**
 * ko-style — Korean translation-ese (번역투) linter for the documentation.
 *
 * Applies the regex catalog in `patterns.json` to the Korean docs mirror,
 * line by line, AFTER masking the regions where prose rules must not fire:
 *
 *   - fenced code blocks (``` … ```)
 *   - inline code spans (`like this`)
 *   - markdown link / image targets ([text](URL) → keeps text, masks URL)
 *   - bare URLs and <autolinks>
 *   - HTML comments (incl. the <!-- docs-uat: … --> annotations)
 *   - YAML front matter (--- … --- at the top of the file)
 *
 * severity: S1 = clear error · S2 = strong recommendation · S3 = advisory.
 * `--fail-on` sets the minimum severity that makes the process exit non-zero
 * (default S2, so S1+S2 fail and S3 is advisory). The Claude hook and the
 * /ko-style command both consume this exit code.
 *
 * Pure Node ESM, no third-party deps — mirrors tools/docs-uat/extract.mjs so
 * it runs directly with `node`.
 *
 * Usage:
 *   node tools/ko-style/lint.mjs [--all|--changed|--files <p> …]
 *                                [--format text|json] [--fail-on S1|S2|S3]
 *                                [--no-baseline] [--write-baseline]
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(__dirname, "..", "..");
export const KO_DOCS_ROOT = path.join(
  REPO_ROOT,
  "docs-site",
  "i18n",
  "ko",
  "docusaurus-plugin-content-docs",
  "current",
);
const PATTERNS_PATH = path.join(__dirname, "patterns.json");
const BASELINE_PATH = path.join(__dirname, "baseline.json");

const SEVERITY_RANK = { S1: 3, S2: 2, S3: 1 };

/** Compile the catalog once into { id, category, severity, re, … } entries. */
export function loadRules() {
  const raw = JSON.parse(fs.readFileSync(PATTERNS_PATH, "utf8"));
  return raw.rules.map((r) => {
    if (!SEVERITY_RANK[r.severity]) {
      throw new Error(`rule '${r.id}': invalid severity '${r.severity}'`);
    }
    return { ...r, re: new RegExp(r.pattern, "gu") };
  });
}

/**
 * Replace every match of `re` in `line` with same-length spaces, so columns
 * stay aligned and masked regions can't trigger prose rules.
 */
function maskOut(line, re) {
  return line.replace(re, (m) => " ".repeat(m.length));
}

/**
 * Blank every `open`…`close` span (inclusive) via a single left-to-right
 * string scan — no regex, so the HTML-comment delimiters don't trip CodeQL's
 * js/bad-tag-filter heuristic (which can't see that `<!--` … `-->` here only
 * masks prose and is not a security-relevant HTML sanitizer). Unbalanced
 * trailing `open` is left intact.
 */
function maskDelimited(line, open, close) {
  let out = "";
  let i = 0;
  for (;;) {
    const start = line.indexOf(open, i);
    if (start === -1) return out + line.slice(i);
    const end = line.indexOf(close, start + open.length);
    if (end === -1) return out + line.slice(i);
    const stop = end + close.length;
    out += line.slice(i, start) + " ".repeat(stop - start);
    i = stop;
  }
}

/** Strip the parts of a single (non-fenced) line where rules must not fire. */
function maskLine(line) {
  let out = line;
  out = maskDelimited(out, "<!--", "-->"); // inline HTML comments
  out = maskOut(out, /`[^`]*`/g); // inline code spans
  // markdown link / image targets: keep the visible text, mask the URL.
  out = out.replace(/(\]\()([^)]*)(\))/g, (_m, a, b, c) => a + " ".repeat(b.length) + c);
  out = maskOut(out, /https?:\/\/\S+/g); // bare URLs
  out = maskOut(out, /<https?:\/\/[^>]*>/g); // autolinks
  return out;
}

/** Lint already-read text. Returns an array of findings. */
export function lintText(text, relPath, rules) {
  const findings = [];
  const lines = text.split("\n");
  let inFence = false;
  let inComment = false; // multi-line HTML comment
  let inFrontMatter = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // YAML front matter: only when --- is the very first line.
    if (i === 0 && line.trim() === "---") {
      inFrontMatter = true;
      continue;
    }
    if (inFrontMatter) {
      if (line.trim() === "---") inFrontMatter = false;
      continue;
    }

    // Fenced code blocks (``` or ~~~).
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;

    // Multi-line HTML comments.
    if (inComment) {
      if (line.includes("-->")) inComment = false;
      continue;
    }
    if (line.includes("<!--") && !line.includes("-->")) {
      inComment = true;
      continue;
    }

    const masked = maskLine(line);
    if (masked.trim() === "") continue;

    for (const rule of rules) {
      rule.re.lastIndex = 0;
      let m;
      while ((m = rule.re.exec(masked)) !== null) {
        findings.push({
          doc: relPath,
          line: i + 1,
          col: m.index + 1,
          id: rule.id,
          category: rule.category,
          severity: rule.severity,
          message: rule.message,
          suggestion: rule.suggestion,
          text: line.slice(m.index, m.index + m[0].length),
        });
        if (m[0].length === 0) rule.re.lastIndex++; // guard zero-width
      }
    }
  }
  return findings;
}

/** Lint one file by path. */
export function lintFile(absPath, relPath, rules = loadRules()) {
  return lintText(fs.readFileSync(absPath, "utf8"), relPath, rules);
}

/** Recursively collect *.md / *.mdx under a root (paths relative to it). */
function walkMarkdown(root) {
  const out = [];
  if (!fs.existsSync(root)) return out;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const abs = path.join(root, entry.name);
    if (entry.isDirectory()) {
      for (const rel of walkMarkdown(abs)) out.push(path.join(entry.name, rel));
    } else if (entry.name.endsWith(".md") || entry.name.endsWith(".mdx")) {
      out.push(entry.name);
    }
  }
  return out;
}

/** A stable signature for baseline matching (line-number independent). */
export function signature(f) {
  return `${f.doc}|${f.id}|${f.text.trim()}`;
}

function loadBaseline() {
  if (!fs.existsSync(BASELINE_PATH)) return new Set();
  const arr = JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8"));
  return new Set(Array.isArray(arr) ? arr : arr.signatures || []);
}

// ───── CLI ──────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const opts = { mode: "all", files: [], format: "text", failOn: "S2", baseline: true, writeBaseline: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--all") opts.mode = "all";
    else if (a === "--changed") opts.mode = "changed";
    else if (a === "--files") opts.mode = "files";
    else if (a === "--format") opts.format = argv[++i];
    else if (a === "--fail-on") opts.failOn = argv[++i];
    else if (a === "--no-baseline") opts.baseline = false;
    else if (a === "--write-baseline") opts.writeBaseline = true;
    else if (a.startsWith("--")) throw new Error(`unknown flag '${a}'`);
    else opts.files.push(a);
  }
  return opts;
}

/** Resolve the set of {abs, rel} KO doc files to lint for the given options. */
function resolveTargets(opts) {
  if (opts.mode === "all") {
    return walkMarkdown(KO_DOCS_ROOT).map((rel) => ({
      abs: path.join(KO_DOCS_ROOT, rel),
      rel: path.join(path.relative(REPO_ROOT, KO_DOCS_ROOT), rel),
    }));
  }
  let paths;
  if (opts.mode === "changed") {
    const base = process.env.KO_STYLE_DIFF_BASE || "origin/main";
    let out = "";
    try {
      out = execFileSync("git", ["diff", "--name-only", `${base}...HEAD`], {
        cwd: REPO_ROOT,
        encoding: "utf8",
      });
    } catch {
      out = execFileSync("git", ["diff", "--name-only", "HEAD"], { cwd: REPO_ROOT, encoding: "utf8" });
    }
    paths = out.split("\n").filter(Boolean);
  } else {
    paths = opts.files;
  }
  const koPrefix = path.relative(REPO_ROOT, KO_DOCS_ROOT);
  return paths
    .map((p) => (path.isAbsolute(p) ? path.relative(REPO_ROOT, p) : p))
    .filter((rel) => rel.startsWith(koPrefix) && (rel.endsWith(".md") || rel.endsWith(".mdx")))
    .filter((rel) => fs.existsSync(path.join(REPO_ROOT, rel)))
    .map((rel) => ({ abs: path.join(REPO_ROOT, rel), rel }));
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const rules = loadRules();
  const targets = resolveTargets(opts);

  let findings = [];
  for (const t of targets) findings.push(...lintFile(t.abs, t.rel, rules));

  if (opts.writeBaseline) {
    const sigs = [...new Set(findings.map(signature))].sort();
    fs.writeFileSync(BASELINE_PATH, JSON.stringify(sigs, null, 2) + "\n");
    console.log(`ko-style: wrote baseline with ${sigs.length} signature(s) to ${path.relative(REPO_ROOT, BASELINE_PATH)}`);
    return;
  }

  if (opts.baseline) {
    const base = loadBaseline();
    findings = findings.filter((f) => !base.has(signature(f)));
  }

  if (opts.format === "json") {
    console.log(JSON.stringify({ files: targets.length, findings }, null, 2));
  } else {
    for (const f of findings) {
      console.log(
        `${f.doc}:${f.line}:${f.col}  [${f.severity} ${f.category}] ${f.message}\n` +
          `    “${f.text}”  → ${f.suggestion}`,
      );
    }
    const counts = { S1: 0, S2: 0, S3: 0 };
    for (const f of findings) counts[f.severity]++;
    console.log(
      `\nko-style: ${targets.length} file(s), ${findings.length} finding(s) ` +
        `(S1 ${counts.S1} · S2 ${counts.S2} · S3 ${counts.S3}).`,
    );
  }

  const threshold = SEVERITY_RANK[opts.failOn] || SEVERITY_RANK.S2;
  const blocking = findings.some((f) => SEVERITY_RANK[f.severity] >= threshold);
  process.exit(blocking ? 1 : 0);
}

if (import.meta.url === `file://${process.argv[1]}`) main();
