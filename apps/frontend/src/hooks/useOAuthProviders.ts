/**
 * useOAuthProviders — M-15 (hide unconfigured OAuth provider buttons).
 *
 * TanStack Query surface for the PUBLIC `GET /auth/oauth/providers`
 * endpoint. Returns the list of providers that are actually configured on
 * this deployment so auth screens render only working sign-in buttons.
 *
 * Fail-closed by design: while the query is loading, and when it errors,
 * `configured` is `[]` — a button must never be shown unless the backend
 * positively confirmed the provider works (a wrongly-shown button 503s on
 * click). No skeleton either: the login page stays simple and the buttons
 * appear once (and only if) the response lands.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchOAuthProviders, type OAuthProviderName } from "@/lib/api";

export const OAUTH_PROVIDERS_QUERY_KEY = ["oauth-providers"] as const;

export interface UseOAuthProvidersResult {
  /** Providers with `configured: true`, in the backend's stable order. */
  configured: OAuthProviderName[];
}

export function useOAuthProviders(): UseOAuthProvidersResult {
  const query = useQuery({
    queryKey: OAUTH_PROVIDERS_QUERY_KEY,
    queryFn: () => fetchOAuthProviders(),
    // Provider configuration changes only on redeploy — cache generously.
    staleTime: 5 * 60_000,
  });

  const configured =
    query.data?.providers
      .filter((p) => p.configured)
      .map((p) => p.provider) ?? [];

  return { configured };
}
