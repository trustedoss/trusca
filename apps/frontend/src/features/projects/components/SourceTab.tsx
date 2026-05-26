import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSourceFile } from "@/features/projects/api/useSourceFile";
import { SourceFileViewer } from "@/features/projects/components/SourceFileViewer";
import { SourceTree } from "@/features/projects/components/SourceTree";

/**
 * SourceTab — G3.3 (Protex-style source file-tree viewer).
 *
 * Two-pane layout: a lazy file tree (left) over a scan's preserved source and
 * a line-numbered file viewer (right) with per-line license highlighting. The
 * selected file path mirrors to `?path=` so a deep link / hard reload restores
 * the open file (same convention as the drawer params on the other tabs).
 *
 * Old scans have no preserved source — the tree's root level returns a 404 and
 * the tab swaps to a single "re-scan to enable" empty state instead of an
 * error toast. The scan defaults to the project's latest on the server; we
 * don't pin a scan_id here so the viewer always tracks the latest scan.
 */

export interface SourceTabProps {
  projectId: string;
  projectName?: string | null;
  /**
   * Pinned snapshot scan id (feature #28). When set, the file tree + viewer
   * read that historical scan's preserved source instead of the latest one.
   * Omit → latest succeeded scan (the server default).
   */
  scanId?: string;
}

export function SourceTab({ projectId, projectName, scanId }: SourceTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();
  const [noSource, setNoSource] = useState(false);

  const selectedPath = searchParams.get("path");

  const setSelectedPath = useCallback(
    (path: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (path) next.set("path", path);
          else next.delete("path");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const file = useSourceFile(projectId, selectedPath, { scanId });

  if (noSource) {
    return (
      <div className="p-6" data-testid="source-tab">
        <Card data-testid="source-no-preserved">
          <CardHeader>
            <CardTitle className="text-base">
              {t("source.empty.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {t("source.empty.description")}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div
      className="grid min-h-0 flex-1 grid-cols-[280px_1fr] divide-x"
      data-testid="source-tab"
      style={{ height: "calc(100vh - var(--layout-header) - 120px)" }}
    >
      <div className="min-h-0 overflow-hidden" data-testid="source-tab-tree-pane">
        <SourceTree
          projectId={projectId}
          scanId={scanId}
          selectedPath={selectedPath}
          onSelectFile={setSelectedPath}
          onNoSource={() => setNoSource(true)}
        />
      </div>
      <div className="min-h-0 overflow-hidden" data-testid="source-tab-viewer-pane">
        <SourceFileViewer
          projectId={projectId}
          projectName={projectName}
          selectedPath={selectedPath}
          data={file.data}
          isLoading={file.isLoading}
          isError={file.isError}
          error={file.error}
        />
      </div>
    </div>
  );
}
