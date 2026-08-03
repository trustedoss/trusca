/**
 * sbomGraph adapters — unit tests (BomLens parity H-1).
 *
 * Locks the pure data transforms that feed the Cytoscape canvas and the tree
 * fallback: root selection, cycle guarding, orphan surfacing, element building,
 * and dangling/self-loop pruning. The Cytoscape component itself is
 * browser-only (canvas), so these tests deliberately stay on the pure helpers —
 * they run cleanly under jsdom with no canvas import.
 */
import { describe, expect, it } from "vitest";

import type {
  GraphEdge,
  GraphNode,
} from "@/features/projects/api/useDependencyGraph";
import {
  buildCytoscapeElements,
  buildDependencyTree,
  findRootIds,
  nodeLabel,
  renderableEdgeCount,
} from "@/features/projects/lib/sbomGraph";

function node(id: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    name: id,
    namespace: null,
    version: "1.0.0",
    purl: `pkg:npm/${id}@1.0.0`,
    direct: false,
    depth: null,
    vulnerability_count: 0,
    max_severity: "none",
    ...overrides,
  };
}

function edge(source: string, target: string): GraphEdge {
  return { source, target };
}

describe("nodeLabel", () => {
  it("formats namespace/name@version", () => {
    expect(
      nodeLabel({ name: "core", namespace: "@scope", version: "2.1.0" }),
    ).toBe("@scope/core@2.1.0");
  });

  it("omits an absent namespace and version", () => {
    expect(nodeLabel({ name: "lib", namespace: null, version: "" })).toBe(
      "lib",
    );
  });

  it("falls back to (unknown) with no name", () => {
    expect(nodeLabel({ name: "", namespace: null, version: "" })).toBe(
      "(unknown)",
    );
  });
});

describe("findRootIds", () => {
  it("prefers backend-flagged direct nodes", () => {
    const nodes = [
      node("a", { direct: true }),
      node("b", { direct: true }),
      node("c"),
    ];
    expect(findRootIds(nodes, new Set(["c"])).sort()).toEqual(["a", "b"]);
  });

  it("falls back to nodes with no incoming edge when none are flagged direct", () => {
    const nodes = [node("a"), node("b"), node("c")];
    // b and c have incoming edges → only a is a root.
    expect(findRootIds(nodes, new Set(["b", "c"]))).toEqual(["a"]);
  });

  it("treats every node as a root in a fully cyclic graph", () => {
    const nodes = [node("a"), node("b")];
    // Both have incoming edges (a→b→a) → no natural root, so keep all.
    expect(findRootIds(nodes, new Set(["a", "b"])).sort()).toEqual(["a", "b"]);
  });
});

describe("buildDependencyTree", () => {
  it("nests transitive dependencies under their direct root", () => {
    const nodes = [
      node("root", { direct: true }),
      node("child"),
      node("grandchild"),
    ];
    const edges = [edge("root", "child"), edge("child", "grandchild")];

    const tree = buildDependencyTree(nodes, edges);
    expect(tree).toHaveLength(1);
    expect(tree[0].id).toBe("root");
    expect(tree[0].depth).toBe(0);
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children[0].id).toBe("child");
    expect(tree[0].children[0].depth).toBe(1);
    expect(tree[0].children[0].children[0].id).toBe("grandchild");
    expect(tree[0].children[0].children[0].depth).toBe(2);
  });

  it("marks a cycle instead of recursing forever", () => {
    const nodes = [node("a", { direct: true }), node("b")];
    const edges = [edge("a", "b"), edge("b", "a")];

    const tree = buildDependencyTree(nodes, edges);
    // a → b → (a again, cut as a cycle leaf)
    const a = tree.find((n) => n.id === "a")!;
    expect(a).toBeDefined();
    const b = a.children.find((n) => n.id === "b")!;
    expect(b).toBeDefined();
    const cyclic = b.children.find((n) => n.id === "a")!;
    expect(cyclic.cycle).toBe(true);
    expect(cyclic.children).toHaveLength(0);
  });

  it("surfaces an orphaned node (only edges dangling) at depth 0", () => {
    const nodes = [node("root", { direct: true }), node("orphan")];
    // orphan's only edge points at a node not in the payload → dropped, so the
    // orphan is never reached from root; it must still appear.
    const edges = [edge("orphan", "ghost")];

    const tree = buildDependencyTree(nodes, edges);
    const ids = tree.map((n) => n.id).sort();
    expect(ids).toEqual(["orphan", "root"]);
    expect(tree.find((n) => n.id === "orphan")!.depth).toBe(0);
  });

  it("carries severity and direct flags onto tree nodes", () => {
    const nodes = [
      node("root", { direct: true, max_severity: "critical", vulnerability_count: 3 }),
    ];
    const tree = buildDependencyTree(nodes, []);
    expect(tree[0].maxSeverity).toBe("critical");
    expect(tree[0].direct).toBe(true);
    expect(tree[0].vulnerabilityCount).toBe(3);
  });
});

describe("buildCytoscapeElements", () => {
  it("emits node elements with label, direct and severity data attributes", () => {
    const nodes = [
      node("a", { direct: true, max_severity: "high", namespace: "@x", version: "2.0.0" }),
    ];
    const elements = buildCytoscapeElements(nodes, []);
    expect(elements).toHaveLength(1);
    expect(elements[0].data).toMatchObject({
      id: "a",
      label: "@x/a@2.0.0",
      direct: "1",
      severity: "high",
    });
  });

  it("keeps only edges whose endpoints exist and drops self-loops + duplicates", () => {
    const nodes = [node("a"), node("b")];
    const edges = [
      edge("a", "b"),
      edge("a", "b"), // duplicate
      edge("a", "a"), // self-loop
      edge("a", "ghost"), // dangling target
      edge("ghost", "b"), // dangling source
    ];
    const elements = buildCytoscapeElements(nodes, edges);
    const edgeEls = elements.filter((e) => "source" in e.data);
    expect(edgeEls).toHaveLength(1);
    expect(edgeEls[0].data).toMatchObject({ source: "a", target: "b" });
  });
});

describe("renderableEdgeCount", () => {
  it("counts only unique, non-dangling, non-self edges", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const edges = [
      edge("a", "b"),
      edge("a", "b"),
      edge("b", "c"),
      edge("c", "c"),
      edge("a", "ghost"),
    ];
    expect(renderableEdgeCount(nodes, edges)).toBe(2);
  });

  it("returns 0 for a node set with no usable edges", () => {
    expect(renderableEdgeCount([node("a")], [edge("a", "a")])).toBe(0);
  });
});
