/**
 * Dependency-graph adapters — BomLens parity Phase H-1.
 *
 * The TRUSCA backend already resolves the dependency graph server-side and
 * ships a flat `{ nodes, edges }` payload (see `useDependencyGraph`). These
 * pure helpers turn that payload into the two shapes the views need:
 *
 *   - `buildCytoscapeElements` → the node/edge element list the Cytoscape
 *     canvas renders (labels + severity/direct data attributes drive styling).
 *   - `buildDependencyTree` → a collapsible direct → transitive hierarchy for
 *     the tree fallback (large / truncated graphs and no-edge SBOMs).
 *
 * Ported from `sbom-tools .../lib/sbomGraph.ts` but rewired to the TRUSCA
 * contract: the input is already normalized (uuids, resolved severity), so the
 * adapter only has to build adjacency, pick roots, guard cycles, and surface
 * orphans. Everything here is a pure function — no DOM, no Cytoscape import —
 * so it runs cleanly under vitest/jsdom without the browser-only canvas.
 */
import type {
  GraphEdge,
  GraphNode,
  GraphSeverity,
} from "@/features/projects/api/useDependencyGraph";

/** A node in the collapsible hierarchy tree (direct at depth 0). */
export interface TreeNode {
  id: string;
  name: string;
  namespace: string | null;
  version: string;
  purl: string;
  /** Depth in THIS rendered tree (0 = a root row), not the backend depth. */
  depth: number;
  direct: boolean;
  vulnerabilityCount: number;
  maxSeverity: GraphSeverity;
  children: TreeNode[];
  /** set when expanding would revisit an ancestor (cycle guard). */
  cycle?: boolean;
}

/** Minimal Cytoscape element shape (avoids importing the browser-only lib). */
export interface CyElement {
  data: Record<string, string>;
}

/** Human label for a node: `namespace/name@version` (version/namespace optional). */
export function nodeLabel(node: {
  name: string;
  namespace: string | null;
  version: string;
}): string {
  const base = node.namespace ? `${node.namespace}/${node.name}` : node.name;
  const label = node.version ? `${base}@${node.version}` : base;
  return label || "(unknown)";
}

/** source → [target, …] adjacency, skipping edges whose endpoints are unknown. */
function buildAdjacency(
  nodeIds: Set<string>,
  edges: GraphEdge[],
): { adjacency: Map<string, string[]>; hasIncoming: Set<string> } {
  const adjacency = new Map<string, string[]>();
  const hasIncoming = new Set<string>();
  for (const e of edges) {
    // Guard dangling endpoints (a truncated / inconsistent edge set) so the
    // tree walk never dereferences a node that isn't in the payload.
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    if (e.source === e.target) continue; // drop self-loops outright
    const list = adjacency.get(e.source);
    if (list) list.push(e.target);
    else adjacency.set(e.source, [e.target]);
    hasIncoming.add(e.target);
  }
  return { adjacency, hasIncoming };
}

/**
 * Root selection: prefer the nodes flagged `direct` by the backend. When the
 * payload marks none direct (some scanners omit the flag), fall back to every
 * node nothing depends on — the same rule BomLens used so the tree always has
 * a top level even on a partial graph.
 */
export function findRootIds(
  nodes: GraphNode[],
  hasIncoming: Set<string>,
): string[] {
  const direct = nodes.filter((n) => n.direct).map((n) => n.id);
  if (direct.length > 0) return direct;
  const noIncoming = nodes.filter((n) => !hasIncoming.has(n.id)).map((n) => n.id);
  // Last resort (a fully-cyclic graph with incoming edges everywhere): treat
  // every node as a root so nothing is silently dropped from the tree.
  return noIncoming.length > 0 ? noIncoming : nodes.map((n) => n.id);
}

/**
 * Build the collapsible hierarchy. Direct dependencies sit at depth 0; each
 * expands into its transitive children. A cycle is cut with a `cycle` marker
 * (the node renders but does not recurse). Any node the walk never reaches
 * (orphaned by a broken edge set) is appended as a depth-0 row so the tree is
 * exhaustive.
 */
export function buildDependencyTree(
  nodes: GraphNode[],
  edges: GraphEdge[],
): TreeNode[] {
  const byId = new Map<string, GraphNode>();
  for (const n of nodes) byId.set(n.id, n);
  const { adjacency, hasIncoming } = buildAdjacency(new Set(byId.keys()), edges);

  const make = (node: GraphNode, depth: number): TreeNode => ({
    id: node.id,
    name: node.name,
    namespace: node.namespace,
    version: node.version,
    purl: node.purl,
    depth,
    direct: node.direct,
    vulnerabilityCount: node.vulnerability_count,
    maxSeverity: node.max_severity,
    children: [],
  });

  const visited = new Set<string>();
  const build = (id: string, depth: number, ancestors: Set<string>): TreeNode => {
    const node = byId.get(id)!;
    const tree = make(node, depth);
    if (ancestors.has(id)) {
      tree.cycle = true;
      return tree;
    }
    visited.add(id);
    const next = new Set(ancestors);
    next.add(id);
    for (const childId of adjacency.get(id) ?? []) {
      tree.children.push(build(childId, depth + 1, next));
    }
    return tree;
  };

  const rootIds = findRootIds(nodes, hasIncoming);
  const seenRoot = new Set<string>();
  const roots: TreeNode[] = [];
  for (const id of rootIds) {
    if (seenRoot.has(id) || !byId.has(id)) continue;
    seenRoot.add(id);
    roots.push(build(id, 0, new Set()));
  }

  // Orphans: nodes never reached from any root (their only edges were dropped
  // as dangling / cyclic). Surface them at depth 0 so the tree stays complete.
  for (const node of nodes) {
    if (!visited.has(node.id) && !seenRoot.has(node.id)) {
      roots.push(make(node, 0));
    }
  }

  return roots;
}

/**
 * Cytoscape element list. Severity + direct ride along as string data
 * attributes so the canvas stylesheet (which cannot use Tailwind classes) can
 * key colors off them. Node ids are already unique uuids; edge ids are
 * synthesized from the endpoint pair and de-duplicated.
 */
export function buildCytoscapeElements(
  nodes: GraphNode[],
  edges: GraphEdge[],
): CyElement[] {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const elements: CyElement[] = nodes.map((n) => ({
    data: {
      id: n.id,
      label: nodeLabel(n),
      direct: n.direct ? "1" : "0",
      severity: n.max_severity,
      vulnCount: String(n.vulnerability_count),
    },
  }));

  const seenEdge = new Set<string>();
  for (const e of edges) {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    if (e.source === e.target) continue;
    const id = `${e.source}->${e.target}`;
    if (seenEdge.has(id)) continue;
    seenEdge.add(id);
    elements.push({ data: { id, source: e.source, target: e.target } });
  }
  return elements;
}

/** Count of real (renderable) edges after dangling / self-loop pruning. */
export function renderableEdgeCount(
  nodes: GraphNode[],
  edges: GraphEdge[],
): number {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const seen = new Set<string>();
  for (const e of edges) {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    if (e.source === e.target) continue;
    seen.add(`${e.source}->${e.target}`);
  }
  return seen.size;
}
