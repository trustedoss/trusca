/**
 * useOAuthIdentities — chore G ("Connected Accounts" UI).
 *
 * TanStack Query surface for the user's linked OAuth providers. Server
 * state only — never mirror the list into Zustand.
 *
 * Usage:
 *
 *   const { data, isLoading, isError } = useOAuthIdentities();
 *   const unlink = useUnlinkIdentity();
 *
 * The mutation invalidates the list query on success so the row disappears
 * once the cache refetches; we deliberately do NOT do an optimistic update
 * because a 409 (urn:trustedoss:problem:oauth_unlink_blocks_login) keeps the
 * row alive — rolling back an optimistic remove would flicker.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  listIdentities,
  unlinkIdentity,
  type OAuthIdentitiesPage,
} from "@/features/profile/api/oauthIdentitiesApi";

export const OAUTH_IDENTITIES_QUERY_KEY = ["oauth-identities", "me"] as const;

export function useOAuthIdentities(): UseQueryResult<
  OAuthIdentitiesPage,
  Error
> {
  return useQuery({
    queryKey: OAUTH_IDENTITIES_QUERY_KEY,
    queryFn: () => listIdentities(),
    // Same staleness window as the rest of the portal (CLAUDE.md "Server
    // state — Stale time defaults to 30 s").
    staleTime: 30_000,
  });
}

export function useUnlinkIdentity(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (identityId: string) => unlinkIdentity(identityId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: OAUTH_IDENTITIES_QUERY_KEY,
      });
    },
  });
}
