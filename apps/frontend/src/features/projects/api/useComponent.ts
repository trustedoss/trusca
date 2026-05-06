/**
 * useComponent — Phase 3 PR #10.
 *
 * Lazy fetch for the component detail drawer. Only enabled while the drawer
 * is open and a component id is selected.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getComponent,
  type ComponentDetailResponse,
} from "@/features/projects/api/projectDetailApi";

export function componentKey(componentId: string) {
  return ["components", componentId] as const;
}

export function useComponent(
  componentId: string | null | undefined,
): UseQueryResult<ComponentDetailResponse> {
  return useQuery({
    queryKey: componentKey(componentId ?? ""),
    queryFn: () => getComponent(componentId as string),
    enabled: typeof componentId === "string" && componentId.length > 0,
  });
}
