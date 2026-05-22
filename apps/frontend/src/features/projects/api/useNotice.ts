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
import { safeFilenameToken, triggerBlobDownload } from "@/lib/download";

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

function triggerNoticeDownload(
  body: string,
  filename: string,
  format: NoticeFormat,
) {
  const mime =
    format === "markdown"
      ? "text/markdown;charset=utf-8"
      : format === "html"
        ? "text/html;charset=utf-8"
        : "text/plain;charset=utf-8";
  triggerBlobDownload(new Blob([body], { type: mime }), filename);
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
        triggerNoticeDownload(result.body, opts.filename ?? fallbackName, fmt);
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
