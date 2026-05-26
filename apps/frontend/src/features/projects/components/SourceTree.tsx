import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, File, Folder } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { SourceTreeEntry } from "@/features/projects/api/sourceTreeApi";
import { useSourceTree } from "@/features/projects/api/useSourceTree";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * SourceTree — G3.3 (left pane of the Source tab).
 *
 * Recursive, lazy-disclosure file tree over a scan's preserved source. The
 * root mounts a `SourceTreeLevel` for path "" (the source root); every
 * directory row, once expanded, mounts its own `SourceTreeLevel` keyed by its
 * path so children load on demand — the whole member list is never fetched.
 *
 * Directories sort first (the backend already returns dirs-first). Each file
 * row shows its `license_spdx_ids` as small badges (LicenseKind-style). Large
 * directories virtualize through `react-virtuoso`.
 *
 * The 404 "no preserved source" state bubbles up via `onNoSource` so the tab
 * can render a single empty state instead of an error per level.
 */

const TREE_PAGE_SIZE = 500;
const INDENT_PX = 14;
/** Above this many entries in one directory, virtualize the level. */
const VIRTUALIZE_THRESHOLD = 200;

export interface SourceTreeProps {
  projectId: string;
  scanId?: string;
  /** Currently-selected file path (mirrored to `?path=`). */
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  /** Fired the first time the root level returns a 404 (no preserved source). */
  onNoSource?: () => void;
}

export function SourceTree({
  projectId,
  scanId,
  selectedPath,
  onSelectFile,
  onNoSource,
}: SourceTreeProps) {
  return (
    <div
      className="h-full overflow-auto py-1"
      data-testid="source-tree"
      role="tree"
    >
      <SourceTreeLevel
        projectId={projectId}
        scanId={scanId}
        dirPath=""
        depth={0}
        selectedPath={selectedPath}
        onSelectFile={onSelectFile}
        onNoSource={onNoSource}
        isRoot
      />
    </div>
  );
}

interface SourceTreeLevelProps {
  projectId: string;
  scanId?: string;
  dirPath: string;
  depth: number;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  onNoSource?: () => void;
  isRoot?: boolean;
}

function SourceTreeLevel({
  projectId,
  scanId,
  dirPath,
  depth,
  selectedPath,
  onSelectFile,
  onNoSource,
  isRoot = false,
}: SourceTreeLevelProps) {
  const { t } = useTranslation("project_detail");
  const query = useSourceTree(projectId, dirPath, {
    scanId,
    size: TREE_PAGE_SIZE,
  });

  const isNotFound =
    query.error instanceof ProblemError && query.error.status === 404;

  // The root 404 is the "no preserved source" signal — let the tab own the
  // empty state. Fire the callback from an effect (not during render) so we
  // don't call the parent's setState while React is rendering this child
  // (which triggered a "Cannot update a component while rendering a different
  // component" warning). We still render nothing so the tab's empty state shows.
  useEffect(() => {
    if (isRoot && isNotFound) onNoSource?.();
  }, [isRoot, isNotFound, onNoSource]);

  if (isRoot && isNotFound) {
    return null;
  }

  if (query.isLoading) {
    return (
      <div
        className="flex flex-col gap-1 px-2 py-1"
        data-testid="source-tree-loading"
      >
        {Array.from({ length: isRoot ? 6 : 2 }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-full" />
        ))}
      </div>
    );
  }

  if (query.isError && !isNotFound) {
    return (
      <div className="px-2 py-1">
        <Alert variant="destructive" data-testid="source-tree-error">
          <AlertDescription className="text-xs">
            {query.error instanceof ProblemError
              ? query.error.detail
              : t("source.tree.errors.load")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const entries = query.data?.entries ?? [];
  const total = query.data?.total ?? 0;

  if (entries.length === 0) {
    if (isRoot) return null; // tab owns root empty state
    return (
      <div
        className="px-2 py-1 text-xs text-muted-foreground"
        style={{ paddingLeft: depth * INDENT_PX + 24 }}
        data-testid="source-tree-empty-dir"
      >
        {t("source.tree.empty_dir")}
      </div>
    );
  }

  const renderRow = (entry: SourceTreeEntry) => (
    <SourceTreeRow
      key={entry.path}
      projectId={projectId}
      scanId={scanId}
      entry={entry}
      depth={depth}
      selectedPath={selectedPath}
      onSelectFile={onSelectFile}
    />
  );

  // Virtualize only oversized directories — small levels render plain so nested
  // expansion height is natural.
  if (entries.length > VIRTUALIZE_THRESHOLD) {
    return (
      <div
        data-testid="source-tree-level-virtual"
        data-dir={dirPath}
        data-total={total}
      >
        <Virtuoso
          data={entries}
          style={{ height: "min(60vh, 600px)" }}
          itemContent={(_, entry) => renderRow(entry)}
        />
      </div>
    );
  }

  return (
    <div data-testid="source-tree-level" data-dir={dirPath} data-total={total}>
      {entries.map(renderRow)}
    </div>
  );
}

interface SourceTreeRowProps {
  projectId: string;
  scanId?: string;
  entry: SourceTreeEntry;
  depth: number;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
}

function SourceTreeRow({
  projectId,
  scanId,
  entry,
  depth,
  selectedPath,
  onSelectFile,
}: SourceTreeRowProps) {
  const [expanded, setExpanded] = useState(false);
  const isSelected = !entry.is_dir && entry.path === selectedPath;
  const indentStyle = { paddingLeft: depth * INDENT_PX + 8 };

  function onActivate() {
    if (entry.is_dir) {
      setExpanded((prev) => !prev);
    } else {
      onSelectFile(entry.path);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={onActivate}
        role="treeitem"
        aria-expanded={entry.is_dir ? expanded : undefined}
        aria-selected={isSelected}
        data-testid="source-tree-row"
        data-path={entry.path}
        data-is-dir={entry.is_dir ? "true" : "false"}
        data-depth={depth}
        data-expanded={entry.is_dir ? (expanded ? "true" : "false") : undefined}
        className={cn(
          "flex w-full items-center gap-1.5 py-1 pr-2 text-left text-sm hover:bg-muted/50",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
          isSelected && "bg-muted",
        )}
        style={indentStyle}
      >
        <span className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground">
          {entry.is_dir ? (
            expanded ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            )
          ) : null}
        </span>
        {entry.is_dir ? (
          <Folder className="h-3.5 w-3.5 shrink-0 text-risk-low" aria-hidden />
        ) : (
          <File className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <span className="truncate" title={entry.name}>
          {entry.name}
        </span>
        {!entry.is_dir && entry.license_spdx_ids.length > 0 ? (
          <span
            className="ml-auto flex shrink-0 items-center gap-1"
            data-testid="source-tree-row-licenses"
          >
            {entry.license_spdx_ids.slice(0, 2).map((spdx) => (
              <Badge
                key={spdx}
                tone="low"
                className="px-1 py-0 text-[9px]"
                data-testid="source-tree-license-badge"
                data-spdx-id={spdx}
              >
                {spdx}
              </Badge>
            ))}
            {entry.license_spdx_ids.length > 2 ? (
              <Badge
                tone="info"
                className="px-1 py-0 text-[9px]"
                data-testid="source-tree-license-overflow"
                title={entry.license_spdx_ids.join(", ")}
              >
                +{entry.license_spdx_ids.length - 2}
              </Badge>
            ) : null}
          </span>
        ) : null}
      </button>

      {entry.is_dir && expanded ? (
        <SourceTreeLevel
          projectId={projectId}
          scanId={scanId}
          dirPath={entry.path}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelectFile={onSelectFile}
        />
      ) : null}
    </>
  );
}
