/**
 * Browser download helpers — shared by the authenticated blob/text download
 * surfaces (NOTICE, SBOM, vulnerability PDF report).
 *
 * Authenticated downloads fetch the body through the axios `api` instance so
 * the bearer token rides the `Authorization` header instead of leaking onto
 * the URL / browser history / reverse-proxy logs. The body then becomes a
 * `Blob`, and these helpers turn it into a transient `<a download>` click via
 * an object URL.
 *
 * Previously each call site (`useNotice`, `SbomTab`) carried its own copy of
 * `triggerBrowserDownload` / `safeFilenameToken`; the vulnerability PDF button
 * (G2) folds them here so there is a single source of truth.
 */

/**
 * Normalize an arbitrary project (or other entity) name into a filename token
 * safe for the ASCII `Content-Disposition` fallback. Mirrors the backend's
 * `_safe_filename_token` so client-built fallbacks match server filenames.
 */
export function safeFilenameToken(name: string): string {
  const cleaned = name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return cleaned || "project";
}

/**
 * Trigger a browser download of `blob` as `filename`. No-ops in non-DOM
 * environments (SSR / certain test contexts) so callers can invoke it
 * unconditionally.
 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  if (typeof document === "undefined" || typeof URL === "undefined") return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  // Keep the click synchronous + the anchor hidden — Safari requires the
  // click handler to run inside a user-event task.
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Defer revocation so the browser has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

/**
 * Parse the `filename` out of an RFC 6266 `Content-Disposition` value.
 * Handles both `filename="x.pdf"` and bare `filename=x.pdf`. Returns `null`
 * when no filename is present so the caller can fall back to a built name.
 */
export function parseContentDispositionFilename(
  disposition: string | null | undefined,
): string | null {
  if (!disposition) return null;
  const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
  return match?.[1] ?? null;
}
