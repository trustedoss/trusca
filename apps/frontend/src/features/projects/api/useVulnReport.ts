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
  fetchVulnerabilityReportXlsx,
  type VulnReportDownload,
} from "@/features/projects/api/vulnReportApi";
import { triggerBlobDownload } from "@/lib/download";

export interface UseVulnReportReturn {
  download: () => Promise<VulnReportDownload>;
  isLoading: boolean;
  error: Error | null;
}

type ReportFetcher = (
  projectId: string,
  projectName?: string | null,
) => Promise<VulnReportDownload>;

/**
 * Shared imperative-download hook for a vulnerability report artefact. The
 * PDF (`useVulnReport`) and Excel (`useVulnReportXlsx`) hooks differ only in
 * the fetcher, so both delegate here — one fetch + blob download, its own
 * loading / error state (deliberately NOT a `useQuery`).
 */
function useReportDownload(
  fetcher: ReportFetcher,
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
      const result = await fetcher(projectId, projectName);
      triggerBlobDownload(result.blob, result.filename);
      return result;
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setError(err);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [fetcher, projectId, projectName]);

  return { download, isLoading, error };
}

export function useVulnReport(
  projectId: string | undefined,
  projectName?: string | null,
): UseVulnReportReturn {
  return useReportDownload(fetchVulnerabilityReportPdf, projectId, projectName);
}

/** Phase G — Excel (.xlsx) sibling of {@link useVulnReport}. */
export function useVulnReportXlsx(
  projectId: string | undefined,
  projectName?: string | null,
): UseVulnReportReturn {
  return useReportDownload(fetchVulnerabilityReportXlsx, projectId, projectName);
}
