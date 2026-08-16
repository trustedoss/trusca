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
 * problem the backend sends when an export is too large. The interceptor
 * that normally turns those into a `ProblemError` sees an opaque `Blob`,
 * finds no `title` and no extensions, and produces an error that says
 * nothing. The result was that a reader who asked for a 200k-row export got
 * "something went wrong" instead of "narrow the filter". Unwrapping the blob
 * here is what makes the specific message reachable at all.
 */
import { AxiosError } from "axios";

import { api } from "@/lib/api";
import { parseContentDispositionFilename, triggerBlobDownload } from "@/lib/download";
import { ProblemError, parseProblemBody } from "@/lib/problem";

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
  try {
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
  } catch (err) {
    throw await problemFromBlobError(err);
  }
}

/**
 * Read a blob as text.
 *
 * `Blob.text()` is what every browser this ships to provides, and it is what
 * runs in production. jsdom does not implement it, so without the
 * `FileReader` path the error-unwrapping below would be untestable, and an
 * untested unwrap is how this defect survived in the audit export.
 */
async function readBlobText(blob: Blob): Promise<string> {
  if (typeof blob.text === "function") return blob.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

/**
 * Recover the RFC 7807 body an error response carried inside a blob.
 *
 * Returns the original error untouched when there is nothing to recover, so
 * a network failure still reads as a network failure.
 */
export async function problemFromBlobError(err: unknown): Promise<unknown> {
  if (err instanceof ProblemError) return err;
  if (!(err instanceof AxiosError)) return err;

  const data = err.response?.data;
  if (!(data instanceof Blob)) return err;

  let parsed: unknown;
  try {
    parsed = JSON.parse(await readBlobText(data));
  } catch {
    // Not JSON: a proxy error page, a truncated body, an empty 502. The
    // original error is the more honest answer.
    return err;
  }

  const status = err.response?.status ?? 0;
  const { problem, title, detail } = parseProblemBody(parsed, {
    status,
    statusText: err.response?.statusText,
  });
  return new ProblemError(detail || title, { status, title, detail, problem });
}
