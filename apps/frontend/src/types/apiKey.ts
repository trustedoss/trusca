/**
 * API Key wire types — chore C (`/integrations` UI).
 *
 * Mirrors apps/backend/schemas/api_key.py. Snake_case is preserved for the
 * wire shape so the JSON round-trip is verbatim; the React layer uses
 * these types directly without renaming because every other typed wrapper
 * in @/lib/projectsApi follows the same convention.
 */

export type APIKeyScope = "org" | "team" | "project";

/** Response from POST /v1/api-keys — `raw_key` is shown ONCE. */
export interface APIKeyCreateOut {
  id: string;
  key_prefix: string;
  name: string;
  scope: APIKeyScope;
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
  team_id: string | null;
  project_id: string | null;
  created_by_user_id: string | null;
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
