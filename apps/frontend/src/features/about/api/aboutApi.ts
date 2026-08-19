// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * About surface REST client — product identity and license notices.
 *
 * Mirrors the backend contract:
 *   GET /v1/about                       → AboutResponse (JSON)
 *   GET /v1/about/notices/{documentId}  → the document's text (text/plain)
 *
 * The notice bodies are plain text on purpose, so `responseType: "text"` is
 * explicit here: axios is configured to negotiate JSON globally, and without the
 * override it would try to parse a license text as JSON and hand back something
 * unusable.
 */
import { api } from "@/lib/api";

export interface NoticeDocument {
  id: string;
  title: string;
  filename: string;
  description: string;
  /** Size on disk, or null when the file is missing from this deployment. */
  size_bytes: number | null;
}

export interface About {
  product: string;
  version: string;
  license_spdx_id: string;
  license_name: string;
  license_url: string;
  copyright: string;
  source_url: string;
  documents: NoticeDocument[];
  /**
   * Optional surfaces this deployment turned on, by key.
   *
   * A key absent or false means the surface does not exist: its routes answer
   * 404 and nothing should draw an entry point for it. Carried on this
   * response because the shell already reads it, and probing a route to
   * decide whether to draw a menu entry makes the menu flicker.
   */
  features?: Record<string, boolean>;
}

export async function getAbout(): Promise<About> {
  const { data } = await api.get<About>("/v1/about");
  return data;
}

export async function getNotice(documentId: string): Promise<string> {
  const { data } = await api.get<string>(
    `/v1/about/notices/${encodeURIComponent(documentId)}`,
    { responseType: "text", headers: { Accept: "text/plain" } },
  );
  return data;
}
