import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { SourceFileResponse } from "@/features/projects/api/sourceTreeApi";
import {
  buildSourceLines,
  formatMatchTooltip,
  type SourceLine,
} from "@/features/projects/lib/sourceHighlight";
import { safeFilenameToken, triggerBlobDownload } from "@/lib/download";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * SourceFileViewer — G3.3 (right pane of the Source tab).
 *
 * Renders one preserved-source file line-by-line with line numbers and a left
 * gutter chip / background tint on every line covered by a `license_matches`
 * range. Each highlighted line carries a `title` tooltip ("MIT 99% · …").
 *
 * Four terminal states are handled distinctly:
 *   - no file selected   → a quiet prompt to pick a file.
 *   - 404 / no source    → empty state "re-scan to enable" (caught upstream
 *     too, but a directory / missing path also lands here).
 *   - encoding=binary    → a message; we never try to render bytes.
 *   - truncated=true      → a banner above the content + a download button for
 *     the bytes we DID receive.
 *
 * Long files virtualize through `react-virtuoso` (same as the other tabs) so a
 * 10k-line file scrolls at 60fps.
 */

export interface SourceFileViewerProps {
  /** Project for the download filename + nothing else (data comes via props). */
  projectName?: string | null;
  /** Selected file path; null when nothing is selected. */
  selectedPath: string | null;
  data: SourceFileResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

export function SourceFileViewer({
  projectName,
  selectedPath,
  data,
  isLoading,
  isError,
  error,
}: SourceFileViewerProps) {
  const { t } = useTranslation("project_detail");

  const lines = useMemo<SourceLine[]>(
    () =>
      data && data.encoding === "utf-8"
        ? buildSourceLines(data.content, data.license_matches)
        : [],
    [data],
  );

  // A 404 means the file isn't in the preserved source (old scan, directory,
  // or missing path) — show the same "re-scan" empty state as the tree.
  const isNotFound = error instanceof ProblemError && error.status === 404;

  function onDownloadTruncated() {
    if (!data || data.content == null) return;
    const base = safeFilenameToken(selectedPath ?? projectName ?? "source");
    const blob = new Blob([data.content], {
      type: "text/plain;charset=utf-8",
    });
    triggerBlobDownload(blob, base);
  }

  if (!selectedPath) {
    return (
      <div
        className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground"
        data-testid="source-file-empty-selection"
      >
        {t("source.viewer.no_selection")}
      </div>
    );
  }

  if (isError && isNotFound) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center"
        data-testid="source-file-not-found"
      >
        <p className="text-sm font-medium">{t("source.empty.title")}</p>
        <p className="max-w-sm text-xs text-muted-foreground">
          {t("source.empty.description")}
        </p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-6">
        <Alert variant="destructive" data-testid="source-file-error">
          <AlertDescription>
            {error instanceof ProblemError
              ? error.detail
              : t("source.viewer.errors.load")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div
        className="flex flex-col gap-2 p-4"
        data-testid="source-file-loading"
      >
        {Array.from({ length: 12 }).map((_, i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div
      className="flex h-full flex-col"
      data-testid="source-file-viewer"
      data-path={data.path}
      data-encoding={data.encoding}
      data-truncated={data.truncated ? "true" : "false"}
    >
      <SourceFileHeader path={data.path} byteSize={data.byte_size} />

      {data.truncated ? (
        <div
          className="flex items-center justify-between gap-3 border-b bg-risk-medium/10 px-4 py-2 text-xs text-risk-medium"
          data-testid="source-file-truncated-banner"
        >
          <span>{t("source.viewer.truncated")}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onDownloadTruncated}
            data-testid="source-file-download"
          >
            {t("source.viewer.download")}
          </Button>
        </div>
      ) : null}

      {data.encoding === "binary" ? (
        <div
          className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground"
          data-testid="source-file-binary"
        >
          {t("source.viewer.binary")}
        </div>
      ) : (
        <div className="min-h-0 flex-1" data-testid="source-file-content">
          <Virtuoso
            data={lines}
            style={{
              height: "calc(100vh - var(--layout-header) - 220px)",
            }}
            itemContent={(_, line) => <SourceLineRow line={line} />}
          />
        </div>
      )}
    </div>
  );
}

interface SourceFileHeaderProps {
  path: string;
  byteSize: number;
}

function SourceFileHeader({ path, byteSize }: SourceFileHeaderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center justify-between gap-3 border-b px-4 py-2"
      data-testid="source-file-header"
    >
      <span
        className="truncate font-mono text-xs"
        title={path}
        data-testid="source-file-header-path"
      >
        {path}
      </span>
      <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
        {t("source.viewer.byte_size", { bytes: byteSize.toLocaleString() })}
      </span>
    </div>
  );
}

interface SourceLineRowProps {
  line: SourceLine;
}

function SourceLineRow({ line }: SourceLineRowProps) {
  const { t } = useTranslation("project_detail");
  const highlighted = line.matches.length > 0;
  const spdxIds = line.matches.map((m) => m.spdx_id).join(", ");
  const tooltip = highlighted
    ? t("source.viewer.license_match", {
        matches: formatMatchTooltip(line.matches),
      })
    : undefined;
  return (
    <div
      className={cn(
        "group flex items-start gap-2 px-2 font-mono text-xs leading-5",
        highlighted && "bg-risk-low/10",
      )}
      data-testid="source-line"
      data-line={line.number}
      data-highlighted={highlighted ? "true" : "false"}
      title={tooltip}
    >
      <span
        aria-hidden
        className={cn(
          "w-12 shrink-0 select-none border-r pr-2 text-right tabular-nums text-muted-foreground",
          highlighted && "border-risk-low/40 text-risk-low",
        )}
      >
        {line.number}
      </span>
      {highlighted ? (
        <span
          className="mt-0.5 shrink-0 rounded-sm bg-risk-low/20 px-1 text-[9px] font-medium uppercase tracking-wide text-risk-low"
          data-testid="source-line-license-chip"
          data-spdx-ids={spdxIds}
        >
          {line.matches[0].spdx_id}
        </span>
      ) : null}
      <code className="whitespace-pre-wrap break-all">
        {line.text.length > 0 ? line.text : " "}
      </code>
    </div>
  );
}
