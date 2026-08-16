// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Where the user guide lives (C1).
 *
 * The product ships a documentation site and the application never linked to
 * it. Someone who wants to know what a build gate does has to already know
 * the site exists, and then find it.
 *
 * Read from the optional build-time `VITE_DOCS_URL` so a self-hosted deploy
 * can point at its own mirror, with the public site as the default. Read
 * inside the function rather than at module scope, per CLAUDE.md rule #11.
 */

const PUBLIC_DOCS_URL = "https://trustedoss.github.io/trusca/";

export function docsUrl(): string {
  const raw = import.meta.env.VITE_DOCS_URL as string | undefined;
  const trimmed = typeof raw === "string" ? raw.trim() : "";
  return trimmed.length > 0 ? trimmed : PUBLIC_DOCS_URL;
}
