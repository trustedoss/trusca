/**
 * Type surface for the error-copy ratchet.
 *
 * The linter itself stays plain `.mjs` so CI can run it with bare node, with
 * no build step. These declarations exist so its unit test can import it under
 * `tsc --noEmit`.
 */

export interface ProblemDetailFinding {
  /** Path relative to `apps/frontend`, POSIX separators. */
  file: string;
  /** 1-indexed line in the original source. */
  line: number;
  /** The offending expression, e.g. `err.detail`. */
  text: string;
}

export interface ProblemDetailScanResult {
  /** Bypass count per file. Files with zero bypasses are absent. */
  counts: Record<string, number>;
  findings: ProblemDetailFinding[];
}

export interface ProblemDetailBudgetBreach {
  file: string;
  count: number;
  budget: number;
}

export interface ProblemDetailDiffResult {
  /** True only when every file sits exactly on its recorded budget. */
  ok: boolean;
  /** Bypasses in files the baseline does not cover — new debt. */
  added: Array<{ file: string; count: number }>;
  /** Files whose count rose above the baseline. */
  grew: ProblemDetailBudgetBreach[];
  /** Files whose count fell — the lowered baseline must be committed. */
  shrank: ProblemDetailBudgetBreach[];
  total: number;
  baselineTotal: number;
}

/** Blank `//` and block comments in place, preserving line numbers. */
export function stripComments(source: string): string;

/** Walk `root`, counting bypasses per file relative to `frontendRoot`. */
export function scan(
  root?: string,
  frontendRoot?: string,
): ProblemDetailScanResult;

/** Compare a fresh scan against the recorded baseline. */
export function diff(
  counts: Record<string, number>,
  baseline: Record<string, number>,
): ProblemDetailDiffResult;
