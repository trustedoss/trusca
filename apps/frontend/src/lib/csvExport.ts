// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Downloading a filtered table as CSV (B5).
 *
 * Four tables offer this now, and they all hit the same two problems.
 *
 * The first is authentication. The file lives behind a bearer token, so it
 * cannot be an ordinary link: the request goes through the shared axios
 * instance, which attaches the token, and the response arrives as a blob
 * that a synthetic anchor hands to the browser.
 *
 * The second is what happens when the server refuses. Asking axios for a
 * blob means EVERY response body arrives as a blob, including the RFC 7807
 * problem the backend sends when an export is too large. The response
 * interceptor in `lib/api.ts` reads that blob before building the
 * `ProblemError` (see `unwrapBlobBody`), so the "narrow the filter" detail
 * and the `*_export_too_large` extension reach the caller here and in every
 * other blob download. An earlier version unwrapped it here instead, after
 * the interceptor had already replaced the body with an empty problem, so
 * the specific message never arrived in production.
 */
import { api } from "@/lib/api";
import { parseContentDispositionFilename, triggerBlobDownload } from "@/lib/download";

/**
 * Fetch a CSV export and hand it to the browser as a download.
 *
 * @param path Absolute API path ending in `export.csv`.
 * @param params Query parameters, already in the shape the endpoint expects.
 * @param fallbackFilename Used when a proxy strips `Content-Disposition`.
 */
export async function downloadCsvExport(
  path: string,
  params: Record<string, unknown>,
  fallbackFilename: string,
): Promise<void> {
  const response = await api.get<Blob>(path, {
    params,
    responseType: "blob",
    // Repeat-key style, matching every list call in the product. Without
    // it a multi-value filter serialises as `severity[]=a&severity[]=b`,
    // FastAPI sees no `severity` at all, and the file quietly carries rows
    // the screen was filtering out.
    paramsSerializer: { indexes: null },
  });
  const disposition = (response.headers as Record<string, string>)[
    "content-disposition"
  ];
  const filename =
    parseContentDispositionFilename(disposition) ?? fallbackFilename;
  triggerBlobDownload(response.data as Blob, filename);
}
