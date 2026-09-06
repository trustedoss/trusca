/**
 * Type surface for the full-module mock ratchet (issue #390).
 *
 * The linter itself stays plain `.mjs` so CI can run it with bare node, with
 * no build step. This declaration exists so tooling that imports it can be
 * type-checked under `tsc --noEmit`.
 */

export interface MockAuditFinding {
  /** Path relative to `apps/frontend`, POSIX separators. */
  file: string;
  /** 1-indexed line of the `vi.mock(...)` call. */
  line: number;
  /** The mocked module specifier, e.g. `@/lib/api`. */
  module: string;
  /** Whether the specifier resolved to a first-party source file. */
  resolved: boolean;
  /**
   * Real value exports the mock factory does not declare, or `null` when the
   * module could not be resolved, or the factory's shape could not be read
   * statically (a computed key, or a spread of something other than the real
   * module).
   */
  missing: string[] | null;
}

export interface MockAuditScanResult {
  /** Full-module mock count per file. Files with none are absent. */
  counts: Record<string, number>;
  findings: MockAuditFinding[];
}

export interface MockAuditBudgetBreach {
  file: string;
  count: number;
  budget: number;
}

export interface MockAuditDiffResult {
  /** True only when every file sits exactly on its recorded budget. */
  ok: boolean;
  /** Full mocks in files the baseline does not cover: new debt. */
  added: Array<{ file: string; count: number }>;
  /** Files whose count rose above the baseline. */
  grew: MockAuditBudgetBreach[];
  /** Files whose count fell; the lowered baseline must be committed. */
  shrank: MockAuditBudgetBreach[];
  total: number;
  baselineTotal: number;
}

/** Walk `testsRoot`, counting full-module `vi.mock` calls per file relative
 * to `frontendRoot`. */
export function scan(
  testsRoot?: string,
  frontendRoot?: string,
): MockAuditScanResult;

/** Compare a fresh scan against the recorded baseline. */
export function diff(
  counts: Record<string, number>,
  baseline: Record<string, number>,
): MockAuditDiffResult;
