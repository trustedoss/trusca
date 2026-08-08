// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 SK Telecom Co., Ltd.
// Copyright 2026 TRUSCA contributors
//
// Dual copyright: this file is TypeScript vendored from BomLens TypeScript, so
// upstream expression survives here — unlike the jq/shell ports elsewhere, where
// a change of language makes the expression ours. See THIRD_PARTY_NOTICES.md §1.
/**
 * G7 AI SBOM conformance helpers — split base format checks from the G7
 * minimum-element checks (ids prefixed "g7-"), group G7 checks per cluster,
 * and compute the coverage tallies. Pure and unit tested — no invented
 * numbers, every count comes from the check statuses/sources.
 *
 * Vendored from BomLens (SK Telecom, Apache-2.0) —
 * `sbom-tools/docker/web/frontend/src/lib/conformance.ts` — and adapted to
 * TRUSCA's `SbomConformanceCheck` type (optional `cluster`/`source` fields
 * that the 9 core format checks omit). Semantics are unchanged: the cluster
 * order mirrors the backend's `services/g7_registry.json` cluster id order
 * (pinned by `tests/unit/contracts/catalogMirrors.test.ts`).
 */
import type { SbomConformanceCheck } from "@/lib/projectsApi";

export function isG7(check: SbomConformanceCheck): boolean {
  return check.id.startsWith("g7-");
}

/** The 2026 SBOM minimum elements — measured on every CycloneDX document. */
export function isCisa(check: SbomConformanceCheck): boolean {
  return check.id.startsWith("cisa-");
}

/**
 * A row belonging to a declarative baseline rather than to the core checks.
 *
 * What decides is what the check IS — an element a registry declared, which is
 * what carries a cluster — not which id prefix it happens to have. A third
 * baseline lands in the right place without touching this.
 */
export function isBaselineCheck(check: SbomConformanceCheck): boolean {
  return Boolean(check.cluster && check.cluster.length > 0);
}

/** Canonical cluster order for the 2026 baseline (mirrors cisa_registry.json). */
export const CISA_CLUSTER_ORDER = [
  "cisa-metadata",
  "cisa-component",
  "cisa-practices",
] as const;

/** Canonical cluster order for the G7 sub-groups (mirrors g7_registry.json). */
export const G7_CLUSTER_ORDER = [
  "metadata",
  "slp",
  "models",
  "dp",
  "infrastructure",
  "sp",
  "kpi",
] as const;

export type G7Cluster = (typeof G7_CLUSTER_ORDER)[number];

/** The cluster a check belongs to; base format checks (no cluster) are "base". */
export function clusterOf(check: SbomConformanceCheck): string {
  return check.cluster && check.cluster.length > 0 ? check.cluster : "base";
}

export interface SplitChecks {
  base: SbomConformanceCheck[];
  g7: SbomConformanceCheck[];
}

/** Partition checks into base format checks and G7 AI checks (stable order). */
export function splitChecks(checks: SbomConformanceCheck[]): SplitChecks {
  return {
    base: checks.filter((c) => !isG7(c)),
    g7: checks.filter(isG7),
  };
}

export interface BaselineSplit {
  /** The core format checks — the only ones that decide pass / warn / fail. */
  core: SbomConformanceCheck[];
  /** 2026 SBOM minimum elements (every CycloneDX document). */
  cisa: SbomConformanceCheck[];
  /** G7 AI minimum elements (ML-BOMs only). */
  g7: SbomConformanceCheck[];
}

/**
 * Split a verdict into the core checks and each declarative baseline.
 *
 * The core set is what a reader needs first: it is the only part that moves the
 * badge. Anything a registry declared goes to its own section below, so a
 * baseline landing later cannot push the mandatory rows off the top of the
 * panel — which is exactly what happened when 23 advisory rows arrived in the
 * middle of the core table.
 */
export function splitByBaseline(
  checks: SbomConformanceCheck[],
): BaselineSplit {
  return {
    core: checks.filter((c) => !isBaselineCheck(c)),
    cisa: checks.filter((c) => isBaselineCheck(c) && isCisa(c)),
    g7: checks.filter((c) => isBaselineCheck(c) && isG7(c)),
  };
}

export interface VerdictHeadline {
  /** Mandatory core checks that failed — the only count that decides the badge. */
  mandatoryFailed: number;
  /** Advisory rows, core or baseline, that fell short. */
  advisoryShort: number;
  /** Rows no scan can settle; a person has to. */
  needsReview: number;
}

/**
 * The three numbers a reader wants before reading any row.
 *
 * Ordered by severity on purpose. Without this the panel opened with whatever
 * came first in the array, and on an AI SBOM the mandatory checks sat below
 * twelve thousand pixels of advisory elements.
 */
export function verdictHeadline(
  checks: SbomConformanceCheck[],
): VerdictHeadline {
  const isReview = (c: SbomConformanceCheck) =>
    c.status === "warn" && c.source === "na";
  return {
    mandatoryFailed: checks.filter((c) => c.required && c.status === "fail")
      .length,
    advisoryShort: checks.filter(
      (c) => !c.required && c.status === "warn" && !isReview(c),
    ).length,
    needsReview: checks.filter(isReview).length,
  };
}

/**
 * Sort rows by what needs attention: failures, then shortfalls, then review
 * items, then passes. Registry order is preserved within each band, so a reader
 * who knows the baseline still finds its elements grouped as the guidance
 * groups them.
 */
export function byAttention(
  checks: SbomConformanceCheck[],
): SbomConformanceCheck[] {
  const rank = (c: SbomConformanceCheck): number => {
    if (c.status === "fail") return 0;
    if (c.status === "warn" && c.source === "na") return 2;
    if (c.status === "warn") return 1;
    return 3;
  };
  return checks
    .map((check, index) => ({ check, index }))
    .sort((a, b) => rank(a.check) - rank(b.check) || a.index - b.index)
    .map(({ check }) => check);
}

/**
 * Collapse a repeated name in a missing list into "name (xN)", and say how many
 * the caller left out. A list that prints the same name twice reads as two
 * problems.
 */
export function collapseMissing(
  missing: string[],
  limit = 8,
): { items: string[]; omitted: number } {
  const counts = new Map<string, number>();
  for (const name of missing) counts.set(name, (counts.get(name) ?? 0) + 1);
  const collapsed = [...counts.entries()].map(([name, n]) =>
    n > 1 ? `${name} (x${n})` : name,
  );
  return {
    items: collapsed.slice(0, limit),
    omitted: Math.max(0, collapsed.length - limit),
  };
}

export interface G7Group {
  cluster: string;
  checks: SbomConformanceCheck[];
}

/**
 * Group the G7 checks by cluster in the canonical registry order. Clusters with
 * no checks are dropped; any unexpected cluster value is appended (in insertion
 * order) so nothing is silently lost.
 */
export function groupG7ByCluster(g7: SbomConformanceCheck[]): G7Group[] {
  const byCluster = new Map<string, SbomConformanceCheck[]>();
  for (const c of g7) {
    const key = clusterOf(c);
    const arr = byCluster.get(key);
    if (arr) arr.push(c);
    else byCluster.set(key, [c]);
  }
  const groups: G7Group[] = [];
  for (const cl of G7_CLUSTER_ORDER) {
    const checks = byCluster.get(cl);
    if (checks && checks.length > 0) {
      groups.push({ cluster: cl, checks });
      byCluster.delete(cl);
    }
  }
  for (const [cluster, checks] of byCluster) groups.push({ cluster, checks });
  return groups;
}

export interface G7Tally {
  /** Checks whose element is present (status pass). */
  present: number;
  /** Not-present advisory checks (status warn) that have an automated source. */
  advisory: number;
  /** Checks with no automated source (source "na") — need human review. */
  review: number;
  /** Total G7 checks (computed, never hardcoded). */
  total: number;
  /** Checks with an automated source (total minus review) — the coverage base. */
  autoTotal: number;
  /** Mandatory failures among G7 (G7 is advisory, so normally 0). */
  failed: number;
}

export function g7Tally(g7: SbomConformanceCheck[]): G7Tally {
  const review = g7.filter((c) => c.source === "na").length;
  return {
    present: g7.filter((c) => c.status === "pass").length,
    advisory: g7.filter((c) => c.status === "warn" && c.source !== "na").length,
    review,
    total: g7.length,
    autoTotal: g7.length - review,
    failed: g7.filter((c) => c.status === "fail").length,
  };
}
