#!/usr/bin/env node
/**
 * docs-uat extractor — "the docs ARE the tests".
 *
 * Walks the EN canonical Docusaurus docs (and the KO mirror), parses
 * `<!-- docs-uat: ... -->` HTML comments, binds each to the code block or
 * prose step that immediately follows, and emits a normalized execution
 * manifest at `docs-uat/manifest.json`. With `--lint` it additionally
 * enforces:
 *
 *   1. id uniqueness (kebab, globally unique)
 *   2. per-kind required-field schema
 *   3. coverage — every executable fence (bash/sh/http/sql) and every
 *      "Verify it worked" step in an *enrolled* doc must be annotated or
 *      carry an explicit `waiver=`. Silent omission fails the lint.
 *   4. KO mirror structure parity — same id set + same kind per id
 *      (command-text equivalence is out of scope; design §9 decision 1).
 *
 * Scope note (Phase A): a doc is *enrolled* when it carries ≥1 docs-uat
 * annotation. Coverage + parity apply only to enrolled docs, so the ~196
 * still-unannotated blocks across install/admin/user guides don't fail the
 * lint until a later Phase annotates them. This is the incremental on-ramp.
 *
 * Pure Node ESM — no third-party deps, no TS toolchain — so the
 * `extract-and-lint` CI job runs it directly with `node`.
 *
 * Annotation grammar (see tools/docs-uat/README.md for the full spec):
 *   <!-- docs-uat: id=<kebab> kind=<shell|api|ui|sql|lint|manual> \
 *        [ctx=host|backend|worker|postgres|kind] \
 *        [expect=exit:N|status:N|match:/re/|rows:>N] \
 *        [url=/path] [retry=NxMs] [harness=verb(a,b)] \
 *        [fixture=...] tier=<gate|nightly|weekly|manual> [waiver=<reason>] -->
 *
 * Tokens are whitespace-separated `key=value`; values carry no spaces.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(__dirname, "..", "..");

const EN_DOCS_ROOT = path.join(REPO_ROOT, "docs-site", "docs");
const KO_DOCS_ROOT = path.join(
  REPO_ROOT,
  "docs-site",
  "i18n",
  "ko",
  "docusaurus-plugin-content-docs",
  "current",
);
export const MANIFEST_PATH = path.join(REPO_ROOT, "docs-uat", "manifest.json");

const VALID_KINDS = new Set(["shell", "api", "ui", "sql", "lint", "manual"]);
const VALID_TIERS = new Set(["gate", "nightly", "weekly", "manual"]);
const EXECUTABLE_FENCE_LANGS = new Set(["bash", "sh", "http", "sql"]);

const ANNOTATION_RE = /<!--\s*docs-uat:\s*(.*?)\s*-->/;
const FENCE_OPEN_RE = /^```(\w+)?/;
const HEADING_RE = /^#{1,6}\s+(.*)$/;
const LIST_ITEM_RE = /^\s*(?:\d+\.|[-*])\s+/;

/** Parse one `key=value key=value` annotation body into a fields object. */
function parseAnnotation(body, line) {
  const fields = { line };
  for (const tok of body.split(/\s+/).filter(Boolean)) {
    const eq = tok.indexOf("=");
    if (eq === -1) {
      throw new Error(`malformed token "${tok}" (expected key=value) @ line ${line}`);
    }
    fields[tok.slice(0, eq)] = tok.slice(eq + 1);
  }
  return fields;
}

/**
 * Parse a single markdown file into { steps, executableBlocks, hasAnnotations }.
 * `steps` are annotated entries bound to a block or prose line. `executableBlocks`
 * is every bash/sh/http/sql fence + every "Verify it worked" list item (the
 * coverage universe), each flagged with whether an annotation bound to it.
 */
export function parseMarkdown(absPath, relPath, lang) {
  const lines = fs.readFileSync(absPath, "utf8").split("\n");
  const steps = [];
  const executableBlocks = [];
  let pending = null;
  let inVerifySection = false;
  let hasAnnotations = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const heading = line.match(HEADING_RE);
    if (heading) {
      inVerifySection = /verify it worked/i.test(heading[1]);
      // A heading clears a dangling annotation that never found its target.
      continue;
    }

    const ann = line.match(ANNOTATION_RE);
    if (ann) {
      hasAnnotations = true;
      pending = parseAnnotation(ann[1], i + 1);
      continue;
    }

    const fence = line.match(FENCE_OPEN_RE);
    if (fence) {
      const fenceLang = fence[1] || "";
      let j = i + 1;
      while (j < lines.length && !/^```/.test(lines[j])) j++;
      const code = lines.slice(i + 1, j).join("\n");
      const executable = EXECUTABLE_FENCE_LANGS.has(fenceLang);
      const block = {
        kind: "fence",
        fenceLang,
        line: i + 1,
        executable,
        annotated: false,
        waived: false,
      };
      if (pending) {
        const step = {
          ...pending,
          doc: relPath,
          lang,
          fenceLang,
          code,
          target: "block",
        };
        steps.push(step);
        block.annotated = true;
        block.waived = Boolean(pending.waiver);
        block.id = pending.id;
        pending = null;
      }
      executableBlocks.push(block);
      i = j; // skip to closing fence line
      continue;
    }

    // Prose binding — a pending annotation latches onto the next non-blank
    // text line (a Verify step or a claim like "services are healthy").
    if (pending && line.trim() !== "") {
      steps.push({
        ...pending,
        doc: relPath,
        lang,
        text: line.trim(),
        target: "prose",
      });
      pending = null;
      continue;
    }

    // A "Verify it worked" list item is part of the coverage universe even
    // before it's annotated (so an unannotated verify step fails coverage).
    if (inVerifySection && LIST_ITEM_RE.test(line)) {
      executableBlocks.push({
        kind: "verify-step",
        line: i + 1,
        executable: true,
        annotated: false,
        waived: false,
        text: line.trim(),
      });
    }
  }

  return { steps, executableBlocks, hasAnnotations };
}

/** Recursively collect *.md under a root, returning paths relative to it. */
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

/** Build the full manifest from the EN docs tree + KO mirror. */
export function buildManifest() {
  const enFiles = walkMarkdown(EN_DOCS_ROOT);
  const docs = [];
  const allSteps = [];
  const ko = {}; // relPath -> { steps }

  // Parse KO mirror once (only files that exist).
  for (const rel of walkMarkdown(KO_DOCS_ROOT)) {
    const parsed = parseMarkdown(path.join(KO_DOCS_ROOT, rel), rel, "ko");
    if (parsed.hasAnnotations) ko[rel] = parsed;
  }

  for (const rel of enFiles) {
    const parsed = parseMarkdown(path.join(EN_DOCS_ROOT, rel), rel, "en");
    if (!parsed.hasAnnotations) continue; // not enrolled
    for (const s of parsed.steps) allSteps.push(s);
    docs.push({
      doc: rel,
      enrolled: true,
      steps: parsed.steps,
      executableBlocks: parsed.executableBlocks,
      ko: ko[rel] ? { steps: ko[rel].steps } : null,
    });
  }

  return {
    generated: "docs-uat extractor (Phase A)",
    en_root: path.relative(REPO_ROOT, EN_DOCS_ROOT),
    ko_root: path.relative(REPO_ROOT, KO_DOCS_ROOT),
    docs,
    steps: allSteps,
  };
}

/** Validate a manifest. Returns { errors: [], warnings: [] }. */
export function lint(manifest) {
  const errors = [];
  const warnings = [];

  // 1. id uniqueness + 2. per-kind schema (EN steps only).
  const seen = new Map();
  for (const s of manifest.steps) {
    const where = `${s.doc}:${s.line}`;
    if (!s.id) errors.push(`${where}: annotation missing required field 'id'`);
    else if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(s.id))
      errors.push(`${where}: id '${s.id}' is not kebab-case`);
    else if (seen.has(s.id))
      errors.push(`${where}: duplicate id '${s.id}' (first seen at ${seen.get(s.id)})`);
    else seen.set(s.id, where);

    if (!s.kind) errors.push(`${where} (${s.id}): missing required field 'kind'`);
    else if (!VALID_KINDS.has(s.kind))
      errors.push(`${where} (${s.id}): invalid kind '${s.kind}'`);
    if (!s.tier) errors.push(`${where} (${s.id}): missing required field 'tier'`);
    else if (!VALID_TIERS.has(s.tier))
      errors.push(`${where} (${s.id}): invalid tier '${s.tier}'`);

    if (s.waiver) continue; // waived steps skip kind-specific field checks

    if ((s.kind === "shell" || s.kind === "sql") && !s.ctx)
      errors.push(`${where} (${s.id}): kind=${s.kind} requires 'ctx'`);
    if (s.kind === "api" && !s.url)
      errors.push(`${where} (${s.id}): kind=api requires 'url'`);
    if (s.kind === "ui" && !s.harness)
      errors.push(`${where} (${s.id}): kind=ui requires 'harness'`);
  }

  // 3. coverage — enrolled docs only.
  for (const doc of manifest.docs) {
    const uncovered = doc.executableBlocks.filter(
      (b) => b.executable && !b.annotated && !b.waived,
    );
    for (const b of uncovered) {
      const what = b.kind === "verify-step" ? `verify step "${b.text}"` : `${b.fenceLang} fence`;
      errors.push(`${doc.doc}:${b.line}: uncovered ${what} (annotate it or add waiver=)`);
    }
  }

  // 4. KO mirror structure parity (enrolled docs).
  for (const doc of manifest.docs) {
    if (!doc.ko) {
      warnings.push(`${doc.doc}: no KO mirror found (parity not checked)`);
      continue;
    }
    const enById = new Map(doc.steps.map((s) => [s.id, s]));
    const koById = new Map(doc.ko.steps.map((s) => [s.id, s]));
    for (const [id, en] of enById) {
      const koStep = koById.get(id);
      if (!koStep) {
        errors.push(`${doc.doc}: KO mirror missing id '${id}'`);
      } else if (koStep.kind !== en.kind) {
        errors.push(
          `${doc.doc}: KO id '${id}' kind '${koStep.kind}' != EN kind '${en.kind}'`,
        );
      }
    }
    for (const id of koById.keys()) {
      if (!enById.has(id)) errors.push(`${doc.doc}: KO mirror has extra id '${id}' (not in EN)`);
    }
  }

  return { errors, warnings };
}

function summarize(manifest) {
  const byTier = {};
  const byKind = {};
  for (const s of manifest.steps) {
    byTier[s.tier] = (byTier[s.tier] || 0) + 1;
    byKind[s.kind] = (byKind[s.kind] || 0) + 1;
  }
  return { docs: manifest.docs.length, steps: manifest.steps.length, byTier, byKind };
}

// ───── CLI ────────────────────────────────────────────────────────────────
function main() {
  const argv = process.argv.slice(2);
  const doLint = argv.includes("--lint");
  const manifest = buildManifest();

  fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n");

  const sum = summarize(manifest);
  console.log(
    `docs-uat: wrote ${path.relative(REPO_ROOT, MANIFEST_PATH)} — ` +
      `${sum.docs} enrolled doc(s), ${sum.steps} step(s) ` +
      `[tier ${JSON.stringify(sum.byTier)} · kind ${JSON.stringify(sum.byKind)}]`,
  );

  if (!doLint) return;

  const { errors, warnings } = lint(manifest);
  for (const w of warnings) console.warn(`::warning::docs-uat lint: ${w}`);
  if (errors.length) {
    for (const e of errors) console.error(`::error::docs-uat lint: ${e}`);
    console.error(`\ndocs-uat lint FAILED with ${errors.length} error(s).`);
    process.exit(1);
  }
  console.log(`docs-uat lint OK (0 errors, ${warnings.length} warning(s)).`);
}

if (import.meta.url === `file://${process.argv[1]}`) main();
