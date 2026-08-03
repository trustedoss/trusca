// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  type UseInfiniteQueryResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  listComponentUsage,
  listInventoryComponents,
  listVulnerabilityImpact,
  type InventoryComponentListResponse,
  type InventoryProjectUsageListResponse,
  type InventoryVulnerabilityImpactResponse,
  type ListInventoryParams,
} from "@/features/inventory/api/inventoryApi";

/** Rows fetched per page. Matches the virtualized table's appetite. */
export const INVENTORY_PAGE_SIZE = 50;

/**
 * Cache key for an inventory query.
 *
 * Array filters are sorted so two selections with the same members but
 * different click order share one cache entry; optionals normalise to `null`
 * so `undefined` and "absent" cannot mint separate keys.
 */
export function inventoryQueryKey(params: ListInventoryParams) {
  return [
    "inventory",
    "components",
    {
      q: params.q ?? null,
      packageType: [...(params.packageType ?? [])].sort(),
      severity: [...(params.severity ?? [])].sort(),
      licenseCategory: [...(params.licenseCategory ?? [])].sort(),
      eol: params.eol ?? null,
      outdated: params.outdated ?? null,
      sort: params.sort ?? "project_count",
      order: params.order ?? "desc",
    },
  ] as const;
}

/**
 * The inventory list, paged by infinite scroll.
 *
 * `getNextPageParam` reads `offset + items.length` against `total`, the same
 * contract the per-project Components tab uses — the response carries
 * limit/offset for exactly this reason.
 */
export function useInventoryComponents(
  params: ListInventoryParams = {},
): UseInfiniteQueryResult<
  { pages: InventoryComponentListResponse[]; pageParams: unknown[] },
  Error
> {
  return useInfiniteQuery({
    queryKey: inventoryQueryKey(params),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listInventoryComponents({
        ...params,
        limit: INVENTORY_PAGE_SIZE,
        offset: pageParam as number,
      }),
    getNextPageParam: (lastPage) => {
      const consumed = lastPage.offset + lastPage.items.length;
      return consumed < lastPage.total ? consumed : undefined;
    },
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}

/** Which projects use a package. Disabled until a component is selected. */
export function useComponentUsage(
  componentId: string | null,
): UseQueryResult<InventoryProjectUsageListResponse, Error> {
  return useQuery({
    queryKey: ["inventory", "component-usage", componentId],
    queryFn: () => listComponentUsage(componentId as string),
    enabled: typeof componentId === "string" && componentId.length > 0,
    staleTime: 30_000,
  });
}

/** Which projects a CVE currently reaches. */
export function useVulnerabilityImpact(
  externalId: string | null | undefined,
): UseQueryResult<InventoryVulnerabilityImpactResponse, Error> {
  return useQuery({
    queryKey: ["inventory", "vulnerability-impact", externalId],
    queryFn: () => listVulnerabilityImpact(externalId as string),
    enabled: typeof externalId === "string" && externalId.length > 0,
    staleTime: 30_000,
    // A CVE the actor's projects do not carry answers 404 by design; that is
    // an answer, not a fault, so do not burn retries discovering it again.
    retry: false,
  });
}
