/**
 * useVexExport — v2.1 Track A (A3).
 *
 * Imperative download helper for the project's VEX document (OpenVEX or
 * CycloneDX VEX). Mirrors {@link useVulnReport} / {@link useNotice}: the user
 * picks a format and expects a single fetch + blob download, not a
 * focus-refetching background query — so this is deliberately NOT a `useQuery`.
 *
 * Returns `{ download(format), busyFormat, error }` so the toolbar can disable
 * only the format being fetched and surface an inline error per attempt.
 */
import { useCallback, useState } from "react";

import {
  downloadVex,
  type VexExportDownload,
  type VexFormat,
} from "@/features/projects/api/vexApi";
import { triggerBlobDownload } from "@/lib/download";

export interface UseVexExportReturn {
  download: (format: VexFormat) => Promise<VexExportDownload>;
  /** The format currently being fetched, or `null` when idle. */
  busyFormat: VexFormat | null;
  error: Error | null;
}

export function useVexExport(
  projectId: string | undefined,
  projectName?: string | null,
): UseVexExportReturn {
  const [busyFormat, setBusyFormat] = useState<VexFormat | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const download = useCallback(
    async (format: VexFormat) => {
      if (!projectId) {
        throw new Error("VEX export requires a project id");
      }
      setBusyFormat(format);
      setError(null);
      try {
        const result = await downloadVex(projectId, format, projectName);
        triggerBlobDownload(result.blob, result.filename);
        return result;
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        throw err;
      } finally {
        setBusyFormat(null);
      }
    },
    [projectId, projectName],
  );

  return { download, busyFormat, error };
}
