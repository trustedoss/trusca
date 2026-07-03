import { ChevronDown, ChevronRight, Package } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import type { TreeNode } from "@/features/projects/lib/sbomGraph";
import { cn } from "@/lib/utils";

/**
 * DependencyTree — BomLens parity Phase H-1 (tree fallback).
 *
 * Collapsible package hierarchy built by `buildDependencyTree`. Direct
 * dependencies (depth 0) sit at the top; expanding a row reveals its
 * transitive dependencies. Serves as the fallback whenever the Cytoscape graph
 * can't render — a truncated graph, a node count over the client cap, or an
 * SBOM with no dependency edges (every node lands at depth 0).
 *
 * Severity pairs the risk dot with a label via `SeverityBadge`, so color is
 * never the only signal (CLAUDE.md a11y rule).
 */
function TreeRow({ node }: { node: TreeNode }) {
  const { t } = useTranslation("project_detail");
  const [open, setOpen] = useState(node.depth === 0);
  const hasChildren = node.children.length > 0;

  return (
    <li>
      <div
        data-testid="dependency-tree-row"
        data-component-id={node.id}
        className="flex items-center gap-2 rounded-sm px-1.5 py-1 hover:bg-accent"
        style={{ paddingLeft: `${node.depth * 16 + 6}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-expanded={open}
            aria-label={
              open
                ? t("dependency_graph.tree.collapse")
                : t("dependency_graph.tree.expand")
            }
          >
            {open ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="inline-block h-4 w-4 shrink-0" aria-hidden />
        )}

        <Package
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
          aria-hidden
        />
        <span className="font-mono text-xs">
          {node.name}
          {node.version ? (
            <span className="text-muted-foreground"> {node.version}</span>
          ) : null}
        </span>

        {node.maxSeverity !== "none" ? (
          <SeverityBadge severity={node.maxSeverity} className="ml-1" />
        ) : null}
        {node.direct ? (
          <Badge tone="info" className="ml-1">
            {t("dependency_graph.legend.direct")}
          </Badge>
        ) : null}
        {node.cycle ? (
          <Badge variant="muted" className="ml-1">
            {t("dependency_graph.tree.cycle")}
          </Badge>
        ) : null}
      </div>

      {hasChildren && open ? (
        <ul>
          {node.children.map((c, i) => (
            <TreeRow key={`${c.id}-${i}`} node={c} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export interface DependencyTreeProps {
  tree: TreeNode[];
  /** Optional note rendered above the tree (e.g. "graph too large" guidance). */
  note?: string;
  className?: string;
}

export function DependencyTree({ tree, note, className }: DependencyTreeProps) {
  const { t } = useTranslation("project_detail");

  if (tree.length === 0) {
    return (
      <p
        className="px-6 py-6 text-sm text-muted-foreground"
        data-testid="dependency-tree-empty"
      >
        {t("dependency_graph.empty")}
      </p>
    );
  }

  return (
    <div className={cn("space-y-2 px-6 py-4", className)} data-testid="dependency-tree">
      {note ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="dependency-tree-note"
        >
          {note}
        </p>
      ) : null}
      <div className="max-h-[44rem] min-h-[16rem] resize-y overflow-auto rounded-md border p-1">
        <ul>
          {tree.map((n, i) => (
            <TreeRow key={`${n.id}-${i}`} node={n} />
          ))}
        </ul>
      </div>
    </div>
  );
}
