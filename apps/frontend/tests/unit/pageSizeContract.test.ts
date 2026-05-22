/**
 * Frontend↔backend page-size contract (Tier N).
 *
 * The 2026-05-22 session's KNOWN_PAGE_SIZE_BUG: the project list hardcoded
 * `size=200` while the backend caps list `size` at 100 (FastAPI `Query(le=100)`)
 * → every request 422'd and the page showed the destructive load-error alert.
 *
 * This static guard fails if ANY frontend page-size constant / `?size=` literal
 * exceeds the backend list cap, so the regression can't ship again. The backend
 * side of the same contract is guarded by the OpenAPI snapshot
 * (apps/backend/tests/unit/test_openapi_contract.py), which captures the `size`
 * parameter; if the backend cap itself changes, update BACKEND_LIST_SIZE_CAP here.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Backend list `size` cap — FastAPI Query(le=100) on the standard paginated
// list endpoints (projects / components / vulnerabilities / scans / obligations).
const BACKEND_LIST_SIZE_CAP = 100;

// Files whose page size legitimately targets an endpoint with a HIGHER cap.
// The source-tree directory listing caps at 500 (Query(le=500)), so SourceTree's
// PAGE_SIZE=500 is valid. Keep this map in sync with the backend's per-endpoint
// `size` maxima (the OpenAPI snapshot guards those caps).
const HIGHER_CAP_FILES: Record<string, number> = {
  "features/projects/components/SourceTree.tsx": 500,
};

// vitest runs from the package root (apps/frontend); src lives directly under it.
const SRC = join(process.cwd(), "src");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".ts") || p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

describe("frontend↔backend page-size contract", () => {
  it("no frontend page-size exceeds the backend list cap", () => {
    const offenders: string[] = [];
    for (const file of walk(SRC)) {
      const txt = readFileSync(file, "utf8");
      const rel = file.slice(SRC.length + 1).split("\\").join("/");
      const cap = HIGHER_CAP_FILES[rel] ?? BACKEND_LIST_SIZE_CAP;
      // Pagination constants: `const PROJECT_PAGE_SIZE = 200`, `PAGE_SIZE = 50`.
      for (const m of txt.matchAll(/\b[A-Z_]*PAGE_SIZE\b\s*=\s*(\d+)/g)) {
        if (Number(m[1]) > cap) offenders.push(`${rel}: PAGE_SIZE = ${m[1]} (> ${cap})`);
      }
      // Inline URL query literals: `?size=200`, `&size=150`.
      for (const m of txt.matchAll(/[?&]size=(\d+)/g)) {
        if (Number(m[1]) > cap) offenders.push(`${rel}: ?size=${m[1]} (> ${cap})`);
      }
    }
    expect(offenders, `frontend page-size exceeds backend cap:\n${offenders.join("\n")}`).toEqual(
      [],
    );
  });
});
