// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useRefreshTicketStatus - #385.
 *
 * POST /v1/vulnerability_findings/{id}/ticket-status/refresh.
 *
 * Not optimistic: there is nothing to predict, since the answer comes from
 * a third-party tracker this client has never talked to. The response is
 * always 200 (an outbound failure is data in `ticket_check_error`, not an
 * HTTP error), so it is written into the detail cache the same way on
 * success, whether or not the check itself succeeded.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { vulnerabilityKey } from "@/features/projects/api/useVulnerability";
import {
  refreshTicketStatus,
  type TicketStatusRefreshResult,
  type VulnerabilityDetail,
} from "@/features/projects/api/vulnerabilitiesApi";

export function useRefreshTicketStatus() {
  const queryClient = useQueryClient();

  return useMutation<TicketStatusRefreshResult, unknown, string>({
    mutationFn: (findingId) => refreshTicketStatus(findingId),
    onSuccess: (result, findingId) => {
      queryClient.setQueryData<VulnerabilityDetail>(
        vulnerabilityKey(findingId),
        (previous) =>
          previous === undefined
            ? previous
            : {
                ...previous,
                ticket_status: result.ticket_status,
                ticket_resolved: result.ticket_resolved,
                ticket_checked_at: result.ticket_checked_at,
                ticket_check_error: result.ticket_check_error,
              },
      );
    },
  });
}
