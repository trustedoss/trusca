// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * API Key wire types — chore C (`/integrations` UI).
 *
 * Mirrors apps/backend/schemas/api_key.py. Snake_case is preserved for the
 * wire shape so the JSON round-trip is verbatim; the React layer uses
 * these types directly without renaming because every other typed wrapper
 * in @/lib/projectsApi follows the same convention.
 */

export type APIKeyScope = "org" | "team" | "project";

/**
 * What a key may do, as distinct from the scope above, which says what it may
 * reach. A read-only key is refused every request that changes something, so
 * a pipeline that just reads results cannot start a scan.
 *
 * Keys issued before this existed are read-write and stay that way: narrowing
 * them on upgrade would have stopped whatever was already using them.
 */
export type APIKeyPermissionBreadth = "read_write" | "read_only";

export const API_KEY_PERMISSION_BREADTHS = [
  "read_only",
  "read_write",
] as const;

/** Response from POST /v1/api-keys — `raw_key` is shown ONCE. */
export interface APIKeyCreateOut {
  id: string;
  key_prefix: string;
  name: string;
  scope: APIKeyScope;
  permission_breadth: APIKeyPermissionBreadth;
  team_id: string | null;
  project_id: string | null;
  created_by_user_id: string | null;
  created_at: string;
  /** ISO timestamp when the key expires, or null when it never expires. */
  expires_at: string | null;
  /**
   * Plaintext bearer key (`tos_<prefix>_<secret>`). The backend returns this
   * exactly once at issuance; the SPA shows it in a "copy now" dialog and
   * never persists it. Subsequent reads return :class:`APIKeyListItem`.
   */
  raw_key: string;
}

/** Row shape returned by GET /v1/api-keys. NEVER includes the plaintext. */
export interface APIKeyListItem {
  id: string;
  key_prefix: string;
  name: string;
  scope: APIKeyScope;
  permission_breadth: APIKeyPermissionBreadth;
  team_id: string | null;
  project_id: string | null;
  created_by_user_id: string | null;
  /**
   * Email of the issuing user (L-17). Null when the issuer account was
   * deleted (created_by_user_id was SET NULL) — render an em-dash.
   */
  created_by_email: string | null;
  created_at: string;
  /** ISO timestamp when the key expires, or null when it never expires. */
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface APIKeyListPage {
  items: APIKeyListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface APIKeyCreatePayload {
  name: string;
  scope: APIKeyScope;
  /** Omitted means read-only, which is what the backend defaults new keys to. */
  permission_breadth?: APIKeyPermissionBreadth;
  /**
   * Issue the key to an automation identity rather than to the caller. The
   * key then lives as long as that identity does instead of stopping when the
   * person who issued it is deactivated. Omitted means a personal key.
   */
  service_account_id?: string | null;
  team_id?: string | null;
  project_id?: string | null;
  /**
   * Optional TTL in days (backend caps at 1–1825). Omitted / null → the key
   * never expires.
   */
  expires_in_days?: number | null;
}

export interface ListAPIKeysParams {
  scope?: APIKeyScope;
  team_id?: string;
  project_id?: string;
  include_revoked?: boolean;
  page?: number;
  page_size?: number;
}
