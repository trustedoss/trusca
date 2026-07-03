import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useDependencyGraph,
  type GraphEdge,
  type GraphNode,
  type GraphSeverity,
} from "@/features/projects/api/useDependencyGraph";
import { DependencyTree } from "@/features/projects/components/DependencyTree";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import {
  buildCytoscapeElements,
  buildDependencyTree,
  nodeLabel,
  renderableEdgeCount,
} from "@/features/projects/lib/sbomGraph";
import { ProblemError } from "@/lib/problem";

/**
 * DependencyGraph — BomLens parity Phase H-1.
 *
 * Node-link dependency graph rendered with Cytoscape.js (+ dagre hierarchical
 * layout). Cytoscape is a browser-only, canvas-based library, so it is loaded
 * with a dynamic import inside `useEffect` — it stays out of the initial bundle
 * and never runs in an SSR / vitest (jsdom) context.
 *
 * Fallbacks (in priority order):
 *   1. `truncated` — the backend refused to materialize the graph (node_count >
 *      node_cap); nodes/edges are empty. We can't draw or tree it, so we show
 *      guidance pointing the user at the Table view.
 *   2. node count over the CLIENT cap — dagre layout would be too slow / the
 *      canvas unreadable, so we render the collapsible tree instead.
 *   3. no dependency edges — a node-only graph is unreadable overlapping dots,
 *      so we render the flat tree (every node at depth 0).
 *   4. otherwise → the Cytoscape canvas.
 *
 * Severity always pairs a color with a label (legend + node detail badge), so
 * color is never the only signal (CLAUDE.md a11y rule).
 */

// Above this the dagre layout gets slow and the canvas unreadable — smaller
// than the backend's 5000 node_cap so the browser stays responsive.
const CLIENT_NODE_CAP = 1500;

const SEVERITY_TOKENS: Record<GraphSeverity, string> = {
  critical: "--risk-critical",
  high: "--risk-high",
  medium: "--risk-medium",
  low: "--risk-low",
  info: "--risk-info",
  unknown: "--risk-info",
  none: "",
};

let dagreRegistered = false;

/** Resolve themed canvas colors from CSS custom properties.
 *
 * `--foreground` / `--muted-foreground` / `--border` / `--card` store HSL
 * channels space-separated ("240 6% 10%"); Cytoscape's color parser only takes
 * the comma form, so convert. Risk tokens are stored as hex — read raw. */
function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const hsl = (name: string) => {
    const channels = css.getPropertyValue(name).trim().replace(/\s+/g, ", ");
    return `hsl(${channels})`;
  };
  const raw = (name: string) => css.getPropertyValue(name).trim();
  const risk = {} as Record<GraphSeverity, string>;
  for (const sev of Object.keys(SEVERITY_TOKENS) as GraphSeverity[]) {
    const token = SEVERITY_TOKENS[sev];
    risk[sev] = token ? raw(token) : "";
  }
  return {
    node: hsl("--muted-foreground"),
    text: hsl("--foreground"),
    edge: hsl("--border"),
    bg: hsl("--card"),
    // Direct-dependency accent — TRUSCA primary token (warm near-black).
    direct: hsl("--primary"),
    risk,
  };
}

export interface DependencyGraphProps {
  projectId: string;
  /** Pinned snapshot scan id; omit → latest succeeded scan. */
  scanId?: string;
}

export function DependencyGraph({ projectId, scanId }: DependencyGraphProps) {
  const { t } = useTranslation("project_detail");
  const query = useDependencyGraph(projectId, scanId);

  const data = query.data;
  // Stabilise the array identities so the memos below don't recompute on every
  // render (the `?? []` default would otherwise be a fresh array each time).
  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);

  const edgeCount = useMemo(
    () => renderableEdgeCount(nodes, edges),
    [nodes, edges],
  );
  const tree = useMemo(() => buildDependencyTree(nodes, edges), [nodes, edges]);

  if (query.isLoading) {
    return (
      <div
        className="flex flex-col gap-2 px-6 py-4"
        data-testid="dependency-graph-loading"
      >
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-[28rem] w-full" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="px-6 py-6">
        <Alert variant="destructive" data-testid="dependency-graph-error">
          <AlertDescription>
            {query.error instanceof ProblemError
              ? query.error.detail
              : t("dependency_graph.errors.load_failed")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  // (1) Backend truncated the graph — no nodes to draw or tree. Guide to Table.
  if (data?.truncated) {
    return (
      <div className="px-6 py-6" data-testid="dependency-graph-fallback">
        <Alert>
          <AlertDescription>
            {t("dependency_graph.truncated", {
              count: data.node_count,
              cap: data.node_cap,
            })}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  // (2) No nodes at all — nothing was resolved for this scan.
  if (nodes.length === 0) {
    return (
      <p
        className="px-6 py-6 text-sm text-muted-foreground"
        data-testid="dependency-graph-empty"
      >
        {t("dependency_graph.empty")}
      </p>
    );
  }

  // (3) Over the client cap — draw the tree instead of a slow, unreadable canvas.
  if (nodes.length > CLIENT_NODE_CAP) {
    return (
      <div data-testid="dependency-graph-fallback">
        <DependencyTree
          tree={tree}
          note={t("dependency_graph.tooLarge", {
            count: nodes.length,
            cap: CLIENT_NODE_CAP,
          })}
        />
      </div>
    );
  }

  // (4) No dependency relationships — a node-only canvas is unreadable dots, so
  // fall back to the flat tree (every node at depth 0).
  if (edgeCount === 0) {
    return (
      <div data-testid="dependency-graph-fallback">
        <DependencyTree
          tree={tree}
          note={t("dependency_graph.noEdges")}
        />
      </div>
    );
  }

  return <GraphCanvas nodes={nodes} edges={edges} />;
}

/**
 * The Cytoscape canvas, split out so the dynamic-import effect only mounts once
 * the caller has decided the graph is drawable (nodes present, under cap, has
 * edges). Keeps the fallback branches free of the browser-only lifecycle.
 */
function GraphCanvas({
  nodes,
  edges,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
}) {
  const { t } = useTranslation("project_detail");
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cyRef = useRef<any>(null);
  const [error, setError] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<GraphNode | null>(null);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  const elements = useMemo(
    () => buildCytoscapeElements(nodes, edges),
    [nodes, edges],
  );

  // Latest search, readable from long-lived Cytoscape handlers without rebind.
  const searchRef = useRef("");
  useEffect(() => {
    searchRef.current = search;
  }, [search]);

  useEffect(() => {
    let destroyed = false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let cy: any;

    void (async () => {
      try {
        const [{ default: cytoscape }, { default: dagre }] = await Promise.all([
          import("cytoscape"),
          import("cytoscape-dagre"),
        ]);
        if (!dagreRegistered) {
          cytoscape.use(dagre);
          dagreRegistered = true;
        }
        if (destroyed || !containerRef.current) return;

        const c = themeColors();
        const severities: GraphSeverity[] = [
          "critical",
          "high",
          "medium",
          "low",
          "info",
          "unknown",
        ];
        // dagre layout options aren't in cytoscape's base LayoutOptions type.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const config: any = {
          container: containerRef.current,
          elements,
          style: [
            {
              selector: "node",
              style: {
                label: "data(label)",
                "font-size": "10px",
                "background-color": c.node,
                color: c.text,
                "text-valign": "center",
                "text-halign": "right",
                "text-margin-x": 6,
                "min-zoomed-font-size": 7,
                "text-background-color": c.bg,
                "text-background-opacity": 0.85,
                "text-background-padding": 2,
                "text-background-shape": "roundrectangle",
                width: 10,
                height: 10,
              },
            },
            {
              selector: 'node[direct = "1"]',
              style: { "background-color": c.direct, width: 14, height: 14 },
            },
            // Vulnerable nodes get a severity-coloured ring (color + the node
            // detail badge label pair, so color isn't the only signal).
            ...severities.map((sev) => ({
              selector: `node[severity = "${sev}"]`,
              style: { "border-width": 3, "border-color": c.risk[sev] },
            })),
            {
              selector: "node.match",
              style: { "border-width": 2, "border-color": c.text },
            },
            {
              selector: "node:selected",
              style: { "border-width": 2, "border-color": c.text },
            },
            { selector: ".dim", style: { opacity: 0.2 } },
            {
              selector: "node.focus",
              style: { "border-width": 3, "border-color": c.direct },
            },
            {
              selector: "edge.trace",
              style: {
                width: 2,
                "line-color": c.text,
                "target-arrow-color": c.text,
                "z-index": 10,
              },
            },
            {
              selector: "edge",
              style: {
                width: 1,
                "line-color": c.edge,
                "target-arrow-color": c.edge,
                "target-arrow-shape": "triangle",
                "arrow-scale": 0.7,
                "curve-style": "bezier",
              },
            },
          ],
          layout: {
            name: "dagre",
            rankDir: "LR",
            nodeSep: 36,
            rankSep: 160,
            fit: true,
            padding: 24,
          },
          minZoom: 0.2,
          maxZoom: 1.4,
          wheelSensitivity: 0.2,
        };
        cy = cytoscape(config);
        cyRef.current = cy;
        // A big graph fits to unreadable dots; when fit zoomed far out, snap to
        // zoom 1 (legible labels) and anchor top-left. Small graphs keep fit.
        cy.one("layoutstop", () => {
          if (cy.zoom() < 0.9) {
            cy.zoom(1);
            const bb = cy.elements().boundingBox();
            cy.pan({ x: 24 - bb.x1, y: 24 - bb.y1 });
          }
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cy.on("tap", "node", (evt: any) => {
          setSelected(nodeById.get(evt.target.id()) ?? null);
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cy.on("tap", (evt: any) => {
          if (evt.target === cy) setSelected(null);
        });
        // Hover to trace a package's neighbourhood; dim the rest. Stands down
        // while a search owns the dim/match classes so the two never fight.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        cy.on("mouseover", "node", (evt: any) => {
          if (searchRef.current.trim()) return;
          const node = evt.target;
          const nb = node.closedNeighborhood();
          cy.elements().difference(nb).addClass("dim");
          nb.edges().addClass("trace");
          node.addClass("focus");
        });
        cy.on("mouseout", "node", () => {
          if (searchRef.current.trim()) return;
          cy.elements().removeClass("dim trace focus");
        });
      } catch {
        if (!destroyed) setError(true);
      }
    })();

    return () => {
      destroyed = true;
      cyRef.current = null;
      if (cy) cy.destroy();
    };
  }, [elements, nodeById]);

  // Highlight nodes matching the search; dim the rest (and all edges).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const q = search.trim().toLowerCase();
    cy.batch(() => {
      if (!q) {
        cy.elements().removeClass("dim match");
        return;
      }
      cy.nodes().forEach(
        (n: {
          data: (k: string) => string;
          toggleClass: (c: string, on: boolean) => void;
        }) => {
          const hit = (n.data("label") || "").toLowerCase().includes(q);
          n.toggleClass("match", hit);
          n.toggleClass("dim", !hit);
        },
      );
      cy.edges().addClass("dim");
    });
  }, [search]);

  if (error) {
    return (
      <p
        className="px-6 py-6 text-sm text-muted-foreground"
        data-testid="dependency-graph-error"
      >
        {t("dependency_graph.errors.render_failed")}
      </p>
    );
  }

  return (
    <div
      className="space-y-2 px-6 py-4"
      data-testid="dependency-graph"
      data-node-count={nodes.length}
      data-edge-count={edges.length}
    >
      <div className="relative max-w-xs">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("dependency_graph.search")}
          aria-label={t("dependency_graph.search")}
          className="h-8 pl-8"
          data-testid="dependency-graph-search"
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full bg-primary"
            aria-hidden
          />
          {t("dependency_graph.legend.direct")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full bg-muted-foreground"
            aria-hidden
          />
          {t("dependency_graph.legend.transitive")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full border-2 border-risk-high bg-muted-foreground"
            aria-hidden
          />
          {t("dependency_graph.legend.vulnerable")}
        </span>
        <span className="text-muted-foreground/80">
          {t("dependency_graph.legend.hint")}
        </span>
      </div>

      {/* Accessible node roster: the canvas is opaque to assistive tech, so we
          mirror the node labels into a visually-hidden list (also a stable
          hook for the graph's node testid). */}
      <ul className="sr-only" aria-label={t("dependency_graph.nodeListAria")}>
        {nodes.map((n) => (
          <li key={n.id} data-testid="dependency-graph-node" data-component-id={n.id}>
            {nodeLabel(n)}
            {n.max_severity !== "none"
              ? ` — ${t(`severity.${n.max_severity}`)}`
              : ""}
          </li>
        ))}
      </ul>

      <div
        ref={containerRef}
        role="img"
        aria-label={t("dependency_graph.canvasAria")}
        data-testid="dependency-graph-canvas"
        className="h-[28rem] w-full rounded-md border bg-card"
      />

      {selected ? (
        <div
          className="space-y-2 rounded-md border bg-muted/30 p-3 text-xs"
          data-testid="dependency-graph-selection"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono font-medium">{nodeLabel(selected)}</span>
            {selected.max_severity !== "none" ? (
              <SeverityBadge severity={selected.max_severity} />
            ) : null}
            {selected.direct ? (
              <Badge tone="info">{t("dependency_graph.legend.direct")}</Badge>
            ) : null}
          </div>
          {selected.purl ? (
            <div className="break-all font-mono text-muted-foreground">
              {selected.purl}
            </div>
          ) : null}
          <div className="text-muted-foreground">
            {t("dependency_graph.selection.vulnCount", {
              count: selected.vulnerability_count,
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
