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
