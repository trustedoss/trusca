/**
 * g7Conformance helpers — unit tests (feat/g7-conformance).
 *
 * The helpers are pure (vendored from BomLens), so we assert their semantics
 * directly: `splitChecks` partitions by the "g7-" id prefix, `groupG7ByCluster`
 * follows the canonical registry order (dropping empty clusters, appending
 * unknown ones), and `g7Tally` derives every count from statuses/sources —
 * review = source "na", autoTotal = total − review, advisory = warn with an
 * automated source.
 *
 * The last test feeds all 51 elements of the backend's `g7_registry.json`
 * through the helpers (as pass-status checks) so the FE tally semantics stay
 * pinned to the same registry the backend evaluates — same latent-drift class
 * as the catalog-mirror contracts (cluster ORDER itself is pinned in
 * `tests/unit/contracts/catalogMirrors.test.ts`).
 */
import { describe, expect, it } from "vitest";

import {
  G7_CLUSTER_ORDER,
  clusterOf,
  g7Tally,
  groupG7ByCluster,
  isG7,
  splitChecks,
} from "@/features/scan/lib/g7Conformance";
import type { SbomConformanceCheck } from "@/lib/projectsApi";

// Backend registry — single source of truth for the 51 G7 elements.
import g7Registry from "../../../../../backend/services/g7_registry.json";

function check(
  overrides: Partial<SbomConformanceCheck> = {},
): SbomConformanceCheck {
  return {
    id: "g7-model-name",
    label: "Model name",
    required: false,
    status: "pass",
    detail: "",
    missing: [],
    cluster: "models",
    source: "auto",
    role: "model-producer",
    evidence: null,
    ...overrides,
  };
}

describe("splitChecks", () => {
  it("partitions by the g7- id prefix, preserving order", () => {
    const base1 = check({ id: "purl", cluster: null, source: null });
    const g71 = check({ id: "g7-meta-author", cluster: "metadata" });
    const base2 = check({ id: "hash", cluster: null, source: null });
    const g72 = check({ id: "g7-model-name" });

    const { base, g7 } = splitChecks([base1, g71, base2, g72]);
    expect(base.map((c) => c.id)).toEqual(["purl", "hash"]);
    expect(g7.map((c) => c.id)).toEqual(["g7-meta-author", "g7-model-name"]);
  });

  it("returns an empty g7 list for a core-only verdict", () => {
    const { base, g7 } = splitChecks([
      check({ id: "timestamp", cluster: null, source: null }),
    ]);
    expect(base).toHaveLength(1);
    expect(g7).toHaveLength(0);
  });

  it("isG7 / clusterOf handle null and empty cluster values", () => {
    expect(isG7(check({ id: "g7-slp-name" }))).toBe(true);
    expect(isG7(check({ id: "license" }))).toBe(false);
    expect(clusterOf(check({ cluster: null }))).toBe("base");
    expect(clusterOf(check({ cluster: "" }))).toBe("base");
    expect(clusterOf(check({ cluster: "kpi" }))).toBe("kpi");
  });
});

describe("groupG7ByCluster", () => {
  it("orders groups canonically and drops empty clusters", () => {
    const groups = groupG7ByCluster([
      check({ id: "g7-kpi-x", cluster: "kpi" }),
      check({ id: "g7-meta-author", cluster: "metadata" }),
      check({ id: "g7-model-name", cluster: "models" }),
      check({ id: "g7-meta-version", cluster: "metadata" }),
    ]);
    expect(groups.map((g) => g.cluster)).toEqual([
      "metadata",
      "models",
      "kpi",
    ]);
    expect(groups[0].checks.map((c) => c.id)).toEqual([
      "g7-meta-author",
      "g7-meta-version",
    ]);
  });

  it("appends an unexpected cluster value instead of losing its checks", () => {
    const groups = groupG7ByCluster([
      check({ id: "g7-future-x", cluster: "future" }),
      check({ id: "g7-model-name", cluster: "models" }),
    ]);
    expect(groups.map((g) => g.cluster)).toEqual(["models", "future"]);
  });
});

describe("g7Tally", () => {
  it("derives every count from statuses/sources (never hardcoded)", () => {
    const tally = g7Tally([
      check({ id: "g7-a", status: "pass", source: "auto" }),
      check({ id: "g7-b", status: "pass", source: "declared" }),
      check({ id: "g7-c", status: "warn", source: "inferred" }),
      check({ id: "g7-d", status: "warn", source: "na" }),
      check({ id: "g7-e", status: "warn", source: "na" }),
    ]);
    expect(tally).toEqual({
      present: 2,
      advisory: 1, // warn with an automated source only
      review: 2, // source na, regardless of status
      total: 5,
      autoTotal: 3, // total − review
      failed: 0,
    });
  });

  it("counts a fail status (G7 is advisory, so normally 0)", () => {
    expect(g7Tally([check({ status: "fail" })]).failed).toBe(1);
  });

  it("keeps the backend registry's 51-element semantics", () => {
    // Materialize the whole registry as pass-status checks, exactly as the
    // backend emits them (source/cluster copied from the registry rows).
    const clusters = g7Registry.clusters as Array<{
      id: string;
      elements: Array<{ id: string; label: string; source: string }>;
    }>;
    const all = clusters.flatMap((cl) =>
      cl.elements.map((el) =>
        check({
          id: el.id,
          label: el.label,
          cluster: cl.id,
          source: el.source,
        }),
      ),
    );
    const naCount = clusters
      .flatMap((cl) => cl.elements)
      .filter((el) => el.source === "na").length;

    const tally = g7Tally(all);
    expect(tally.total).toBe(51);
    expect(tally.review).toBe(naCount);
    expect(tally.autoTotal).toBe(51 - naCount);
    expect(tally.present).toBe(51); // all pass in this fixture

    // Grouping the full registry yields every canonical cluster, in order.
    const groups = groupG7ByCluster(all);
    expect(groups.map((g) => g.cluster)).toEqual([...G7_CLUSTER_ORDER]);
    expect(groups.flatMap((g) => g.checks)).toHaveLength(51);
  });
});
