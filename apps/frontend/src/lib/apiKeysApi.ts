/**
 * API Key REST surface — chore C (`/integrations` UI).
 *
 * Thin typed wrapper around the existing axios `api` instance. Backend
 * contract: apps/backend/api/v1/api_keys.py + apps/backend/schemas/api_key.py.
 *
 *   - POST /v1/api-keys           → 201 APIKeyCreateOut (raw_key returned ONCE)
 *   - GET  /v1/api-keys           → 200 APIKeyListPage
 *   - DELETE /v1/api-keys/{id}    → 204
 *
 * All endpoints require at least the `developer` role; the service layer
 * further enforces scope-specific RBAC. Errors propagate as ProblemError
 * via the response interceptor in `lib/api.ts`.
 */
import { api } from "@/lib/api";
import type {
  APIKeyCreateOut,
  APIKeyCreatePayload,
  APIKeyListPage,
  ListAPIKeysParams,
} from "@/types/apiKey";

export async function listApiKeys(
  params: ListAPIKeysParams = {},
): Promise<APIKeyListPage> {
  const { data } = await api.get<APIKeyListPage>("/v1/api-keys", {
    params: {
      scope: params.scope,
      team_id: params.team_id,
      project_id: params.project_id,
      include_revoked: params.include_revoked,
      page: params.page,
      page_size: params.page_size,
    },
  });
  return data;
}

export async function createApiKey(
  payload: APIKeyCreatePayload,
): Promise<APIKeyCreateOut> {
  const { data } = await api.post<APIKeyCreateOut>("/v1/api-keys", {
    name: payload.name,
    scope: payload.scope,
    team_id: payload.team_id ?? null,
    project_id: payload.project_id ?? null,
  });
  return data;
}

export async function revokeApiKey(apiKeyId: string): Promise<void> {
  await api.delete(`/v1/api-keys/${apiKeyId}`);
}
