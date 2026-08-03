/**
 * Type surface for the G0-1 design-token ratchet lint.
 *
 * The linter itself stays plain `.mjs` so CI can run it with bare node,
 * without a build step. These declarations exist so the unit test (and any
 * future tooling) can import it under `tsc --noEmit`.
 */

export interface TokenFinding {
  /** Path relative to `apps/frontend`, POSIX separators. */
  file: string;
  /** 1-indexed line in the original source. */
  line: number;
  /** The offending literal — a palette class or a hex colour. */
  text: string;
}

export interface TokenScanResult {
  /** Bypass count per file. Files with zero bypasses are absent. */
  counts: Record<string, number>;
  findings: TokenFinding[];
}

export interface TokenBudgetBreach {
  file: string;
  count: number;
  budget: number;
}

export interface TokenDiffResult {
  /** True only when every file sits exactly on its recorded budget. */
  ok: boolean;
  /** Bypasses in files the baseline does not cover — new debt. */
  added: Array<{ file: string; count: number }>;
  /** Files whose count rose above the baseline. */
  grew: TokenBudgetBreach[];
  /** Files whose count fell — the lowered baseline must be committed. */
  shrank: TokenBudgetBreach[];
  total: number;
  baselineTotal: number;
}

/** Blank `//` and block comments in place, preserving line numbers. */
export function stripComments(source: string): string;

/** Walk `root`, counting token bypasses per file relative to `frontendRoot`. */
export function scan(root?: string, frontendRoot?: string): TokenScanResult;

/** Compare a fresh scan against the recorded baseline. */
export function diff(
  counts: Record<string, number>,
  baseline: Record<string, number>,
): TokenDiffResult;
