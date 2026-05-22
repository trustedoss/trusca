/**
 * useVulnReport — G2 frontend.
 *
 * Imperative download helper for the project's vulnerability PDF report.
 * Mirrors {@link useNotice}: the user clicks the button and expects a single
 * fetch + blob download, not a focus-refetching background query — so this is
 * deliberately NOT a `useQuery`.
 *
 * Returns `{ download(), isLoading, error }` so the toolbar can drive a
 * "Generating…" indicator and surface an inline error.
 */
import { useCallback, useState } from "react";

import {
  fetchVulnerabilityReportPdf,
  type VulnReportDownload,
} from "@/features/projects/api/vulnReportApi";
import { triggerBlobDownload } from "@/lib/download";

export interface UseVulnReportReturn {
  download: () => Promise<VulnReportDownload>;
  isLoading: boolean;
  error: Error | null;
}

export function useVulnReport(
  projectId: string | undefined,
  projectName?: string | null,
): UseVulnReportReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const download = useCallback(async () => {
    if (!projectId) {
      throw new Error("vulnerability report download requires a project id");
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = await fetchVulnerabilityReportPdf(projectId, projectName);
      triggerBlobDownload(result.blob, result.filename);
      return result;
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setError(err);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId, projectName]);

  return { download, isLoading, error };
}
