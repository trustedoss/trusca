/**
 * useNotice — Phase 3 PR #13.
 *
 * Imperative download helper for the project's NOTICE attribution body. We
 * intentionally don't fold this into a `useQuery`: the user clicks the
 * download button and expects a single fetch + blob download, not a
 * background-cached query that re-fires on focus.
 *
 * Returns `{ download(opts): Promise<void> }` so the toolbar can await
 * completion to drive a "downloading" indicator.
 */
import { useCallback, useState } from "react";

import {
  fetchProjectNotice,
  type NoticeFormat,
  type NoticeResult,
} from "@/features/projects/api/obligationsApi";

export interface UseNoticeOptions {
  defaultFormat?: NoticeFormat;
}

export interface UseNoticeReturn {
  download: (opts?: {
    format?: NoticeFormat;
    filename?: string;
  }) => Promise<NoticeResult>;
  isLoading: boolean;
  error: Error | null;
  lastResult: NoticeResult | null;
}

function safeFilenameToken(name: string): string {
  const cleaned = name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return cleaned || "project";
}

function triggerBrowserDownload(
  body: string,
  filename: string,
  format: NoticeFormat,
) {
  if (typeof document === "undefined" || typeof URL === "undefined") return;
  const mime =
    format === "markdown"
      ? "text/markdown;charset=utf-8"
      : format === "html"
        ? "text/html;charset=utf-8"
        : "text/plain;charset=utf-8";
  const blob = new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  // Avoid mounting in body — Safari requires the click handler to be in a
  // user-event task; we keep the call synchronous.
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Defer revocation slightly so the browser has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export function useNotice(
  projectId: string | undefined,
  projectName: string | undefined,
  options: UseNoticeOptions = {},
): UseNoticeReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [lastResult, setLastResult] = useState<NoticeResult | null>(null);

  const download = useCallback(
    async (opts: { format?: NoticeFormat; filename?: string } = {}) => {
      if (!projectId) {
        throw new Error("notice download requires a project id");
      }
      const fmt = opts.format ?? options.defaultFormat ?? "text";
      setIsLoading(true);
      setError(null);
      try {
        const result = await fetchProjectNotice(projectId, {
          format: fmt,
          download: true,
        });
        const ext = fmt === "markdown" ? "md" : fmt === "html" ? "html" : "txt";
        const fallbackName = `NOTICE-${safeFilenameToken(projectName ?? projectId)}.${ext}`;
        triggerBrowserDownload(result.body, opts.filename ?? fallbackName, fmt);
        setLastResult(result);
        return result;
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [projectId, projectName, options.defaultFormat],
  );

  return { download, isLoading, error, lastResult };
}
