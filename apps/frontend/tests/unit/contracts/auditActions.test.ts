// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Audit action vocabulary contract.
 *
 * The audit log's Action filter is a select over `AUDIT_ACTIONS`. The backend
 * keeps no enum for `audit_logs.action`: the flush listener in `core/audit.py`
 * writes the generic verbs, and individual routes, tasks and scripts write
 * their own names. So the backend half of the vocabulary is read from source,
 * the same technique as `catalogMirrors.test.ts`:
 *
 *   - `action=` keyword arguments in every file that builds an `AuditLog`
 *     row (directly or through a local `_emit_audit` helper), including the
 *     `"a" if cond else "b"` form and a module constant passed by name;
 *   - the verbs `build_audit_action` and `_soft_delete_action` return;
 *   - the action literal in a hand-written `INSERT INTO audit_logs`.
 *
 * An action written on the backend and missing here would be a row the filter
 * cannot select. One listed here and written nowhere would be an option that
 * always returns nothing. Both directions fail.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { AUDIT_ACTIONS } from "@/features/admin/audit/api/adminAuditApi";

const BACKEND = resolve(process.cwd(), "../backend");
// Application code only. Tests insert arbitrary actions on purpose, and
// migrations never write audit rows.
const SOURCE_DIRS = ["api", "core", "integrations", "notifications", "scripts", "services", "tasks"];

function pythonFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === "__pycache__" || name === "tests") continue;
      out.push(...pythonFiles(full));
    } else if (name.endsWith(".py")) {
      out.push(full);
    }
  }
  return out;
}

/** The text of the parenthesised expression that opens at `start`. */
function balancedParen(src: string, start: number): string {
  let depth = 0;
  for (let i = start; i < src.length; i += 1) {
    if (src[i] === "(") depth += 1;
    else if (src[i] === ")") {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced parenthesis at offset ${start}`);
}

const STRING_LITERAL = /"([^"\n]+)"|'([^'\n]+)'/g;

function literals(expr: string): string[] {
  return [...expr.matchAll(STRING_LITERAL)].map((m) => m[1] ?? m[2]);
}

// The calls whose `action=` keyword becomes `audit_logs.action`. Anything
// else with an `action=` keyword (argparse, structlog fields, approval
// transitions) is not an audit row.
const AUDIT_WRITERS = new Set(["AuditLog", "_emit_audit", "_emit_admin_audit"]);

/** Name of the call whose argument list contains offset `at`. */
function enclosingCall(src: string, at: number): string | null {
  let depth = 0;
  for (let i = at - 1; i >= 0; i -= 1) {
    if (src[i] === ")") depth += 1;
    else if (src[i] === "(") {
      if (depth === 0) {
        return /([A-Za-z_][A-Za-z0-9_]*)\s*$/.exec(src.slice(0, i))?.[1] ?? null;
      }
      depth -= 1;
    }
  }
  return null;
}

/** The values of a `"a" if cond else "b"` expression, not its condition. */
function conditionalValues(expr: string): string[] {
  const inner = expr.slice(1, -1);
  const parts = inner.split(/\bif\b[\s\S]*?\belse\b/);
  return parts.flatMap((part) => literals(part));
}

/** Actions passed as `action=...` to a call that writes an audit row. */
function keywordActions(src: string): string[] {
  const found: string[] = [];
  for (const match of src.matchAll(/\baction\s*=\s*/g)) {
    const index = match.index ?? 0;
    const call = enclosingCall(src, index);
    if (call === null || !AUDIT_WRITERS.has(call)) continue;
    const at = index + match[0].length;
    const rest = src.slice(at);
    if (rest.startsWith('"') || rest.startsWith("'")) {
      const lit = literals(rest.split("\n", 1)[0])[0];
      if (lit) found.push(lit);
    } else if (rest.startsWith("(")) {
      found.push(...conditionalValues(balancedParen(src, at)));
    } else {
      const name = /^[A-Za-z_][A-Za-z0-9_]*/.exec(rest)?.[0];
      if (!name) continue;
      const constant = new RegExp(`^${name}\\s*=\\s*["']([^"'\\n]+)["']`, "m").exec(src);
      // A parameter (`action=action`) or a field read (`spec["action"]`) is
      // resolved where its caller passes the literal, which this scan sees.
      if (constant) found.push(constant[1]);
    }
  }
  return found;
}

function functionBody(src: string, name: string): string {
  const start = src.indexOf(`def ${name}(`);
  if (start < 0) throw new Error(`core/audit.py no longer defines ${name}`);
  const next = src.indexOf("\ndef ", start + 1);
  return src.slice(start, next < 0 ? undefined : next);
}

function backendAuditActions(): { actions: Set<string>; files: string[] } {
  const actions = new Set<string>();
  const files: string[] = [];

  for (const dir of SOURCE_DIRS) {
    for (const file of pythonFiles(join(BACKEND, dir))) {
      const src = readFileSync(file, "utf-8");
      const writesRows =
        /\bAuditLog\(/.test(src) || /\b_emit_(admin_)?audit\(/.test(src);
      if (writesRows) {
        const found = keywordActions(src);
        if (found.length > 0) files.push(relative(BACKEND, file));
        found.forEach((a) => actions.add(a));
      }
      if (/INSERT INTO audit_logs/.test(src)) {
        for (const m of src.matchAll(/VALUES\s*\([^,]*,\s*'([^']+)'/g)) {
          actions.add(m[1]);
          files.push(relative(BACKEND, file));
        }
      }
    }
  }

  const audit = readFileSync(join(BACKEND, "core/audit.py"), "utf-8");
  for (const m of functionBody(audit, "build_audit_action").matchAll(
    /"\w+":\s*"(\w+)"/g,
  )) {
    actions.add(m[1]);
  }
  for (const m of functionBody(audit, "_soft_delete_action").matchAll(
    /return\s+"(\w+)"/g,
  )) {
    actions.add(m[1]);
  }

  return { actions, files };
}

describe("audit action vocabulary", () => {
  const { actions, files } = backendAuditActions();

  it("reads the backend from the places that actually write rows", () => {
    // Guards the scan itself: if a refactor moved the writes out of reach,
    // an empty set would agree with nothing and the failure would point at
    // the list instead of at the scan.
    expect(files).toEqual(
      expect.arrayContaining([
        "api/v1/admin/backup.py",
        "tasks/backup.py",
        "api/v1/policy_gate.py",
        "tasks/scan_source.py",
        "scripts/anonymise_user.py",
      ]),
    );
    for (const verb of ["create", "update", "delete", "archive", "unarchive"]) {
      expect(actions.has(verb), verb).toBe(true);
    }
  });

  it("lists every action the backend writes", () => {
    const missing = [...actions].filter(
      (a) => !(AUDIT_ACTIONS as readonly string[]).includes(a),
    );
    expect(missing).toEqual([]);
  });

  it("lists no action the backend no longer writes", () => {
    const stale = AUDIT_ACTIONS.filter((a) => !actions.has(a));
    expect(stale).toEqual([]);
  });

  it("has no duplicates", () => {
    expect(new Set(AUDIT_ACTIONS).size).toBe(AUDIT_ACTIONS.length);
  });
});
