/**
 * Vulnerability PDF report wire surface — G2 frontend.
 *
 * Backs the "Download PDF report" button on the project Vulnerabilities tab:
 *
 *   - GET /v1/projects/{id}/vulnerability-report.pdf → application/pdf
 *
 * The request goes through the shared axios `api` instance with
 * `responseType: "blob"`, so the bearer token rides the `Authorization`
 * header (request interceptor in `@/lib/api`) rather than the URL — the same
 * wiring `downloadSbom` / `fetchProjectNotice` rely on. The backend sets a
 * `Content-Disposition: attachment; filename="vulnerability-report-<name>.pdf"`
 * header which we parse for the suggested filename, falling back to a
 * client-built name when the header is absent.
 *
 * Hard rules (CLAUDE.md):
 *   - All 4xx/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor. Note: when
 *     `responseType` is "blob" axios delivers the error body as a Blob, but
 *     the interceptor still maps non-2xx into a ProblemError (with a generic
 *     detail) so call sites have one error type to catch.
 */
import { api } from "@/lib/api";
import { parseContentDispositionFilename, safeFilenameToken } from "@/lib/download";

export interface VulnReportDownload {
  blob: Blob;
  filename: string;
}

/**
 * Fetch the vulnerability report PDF for a project as a Blob and resolve the
 * suggested download filename.
 */
export async function fetchVulnerabilityReportPdf(
  projectId: string,
  projectName?: string | null,
): Promise<VulnReportDownload> {
  const response = await api.get<Blob>(
    `/v1/projects/${projectId}/vulnerability-report.pdf`,
    { responseType: "blob" },
  );
  const headers = (response.headers ?? {}) as Record<string, string>;
  const disposition =
    headers["content-disposition"] ?? headers["Content-Disposition"] ?? "";
  const headerFilename = parseContentDispositionFilename(disposition);
  const fallback = `vulnerability-report-${safeFilenameToken(
    projectName ?? projectId,
  )}.pdf`;
  // Always materialize a PDF Blob so the download is typed correctly even if
  // the transport delivered an untyped Blob.
  const blob = new Blob([response.data as Blob], { type: "application/pdf" });
  return { blob, filename: headerFilename ?? fallback };
}

const XLSX_MIME =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/**
 * Fetch the vulnerability report as an Excel (.xlsx) workbook Blob (Phase G).
 * Mirrors {@link fetchVulnerabilityReportPdf} — same axios blob wiring and
 * Content-Disposition filename parsing, different endpoint + MIME type.
 */
export async function fetchVulnerabilityReportXlsx(
  projectId: string,
  projectName?: string | null,
): Promise<VulnReportDownload> {
  const response = await api.get<Blob>(
    `/v1/projects/${projectId}/vulnerability-report.xlsx`,
    { responseType: "blob" },
  );
  const headers = (response.headers ?? {}) as Record<string, string>;
  const disposition =
    headers["content-disposition"] ?? headers["Content-Disposition"] ?? "";
  const headerFilename = parseContentDispositionFilename(disposition);
  const fallback = `vulnerability-report-${safeFilenameToken(
    projectName ?? projectId,
  )}.xlsx`;
  const blob = new Blob([response.data as Blob], { type: XLSX_MIME });
  return { blob, filename: headerFilename ?? fallback };
}
