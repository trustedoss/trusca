/**
 * OAuth identity REST surface — chore G ("Connected Accounts" UI).
 *
 * Thin typed wrapper around the existing axios `api` instance. Backend
 * contract (frozen for chore G):
 *
 *   GET    /v1/users/me/oauth-identities         → 200 { items: [...] }
 *   DELETE /v1/users/me/oauth-identities/{id}    → 204
 *     - 404 when the identity does not belong to the caller (existence-hide).
 *     - 409 RFC 7807 with type
 *       ``urn:trustedoss:problem:oauth_unlink_blocks_login`` when the row
 *       being unlinked would leave the account with no way to sign in.
 *
 * Errors propagate as ProblemError via the response interceptor in
 * `lib/api.ts`; callers branch on `err.problem.type` (URN comparison) to
 * distinguish the blocks-login case from generic failures.
 */
import { api } from "@/lib/api";

export type OAuthProvider = "github" | "google";

export interface OAuthIdentity {
  id: string;
  provider: OAuthProvider;
  provider_user_id: string;
  provider_email: string | null;
  created_at: string;
}

export interface OAuthIdentitiesPage {
  items: OAuthIdentity[];
}

/** RFC 7807 problem URN signaling that unlinking would lock the user out. */
export const OAUTH_UNLINK_BLOCKS_LOGIN_TYPE =
  "urn:trustedoss:problem:oauth_unlink_blocks_login";

export async function listIdentities(): Promise<OAuthIdentitiesPage> {
  const { data } = await api.get<OAuthIdentitiesPage>(
    "/v1/users/me/oauth-identities",
  );
  return data;
}

export async function unlinkIdentity(identityId: string): Promise<void> {
  await api.delete(`/v1/users/me/oauth-identities/${identityId}`);
}
