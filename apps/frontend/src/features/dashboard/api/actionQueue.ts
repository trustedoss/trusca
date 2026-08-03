// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * `GET /v1/dashboard/action-queue` — the work waiting on a person.
 *
 * Mirrors `schemas/action_queue.py`. The counts are computed live rather than
 * read from a stored verdict, so this is one of the few dashboard reads worth
 * refetching when the tab regains focus: a colleague triaging a finding or an
 * admin editing a licence policy changes the answer without anything on this
 * page having happened.
 */

export interface GateBlockedProject {
  project_id: string;
  project_name: string;
  scan_id: string;
  critical_cve_count: number;
  forbidden_license_count: number;
  epss_gate_count: number;
}

export interface KevSlaBucket {
  overdue: number;
  due_soon: number;
}

export interface StaleProject {
  project_id: string;
  project_name: string;
  last_succeeded_at: string | null;
}

export interface ActionQueue {
  pending_approvals: number;
  kev_sla: KevSlaBucket;
  gate_blocked: GateBlockedProject[];
  stale_projects: StaleProject[];
}

export async function getActionQueue(): Promise<ActionQueue> {
  const { data } = await api.get<ActionQueue>("/v1/dashboard/action-queue");
  return data;
}

export const ACTION_QUEUE_QUERY_KEY = ["dashboard", "action-queue"] as const;

export function useActionQueue() {
  return useQuery({
    queryKey: ACTION_QUEUE_QUERY_KEY,
    queryFn: getActionQueue,
    // The endpoint is rate limited per actor and its aggregates read every
    // open finding in scope, so this deliberately does not poll. A minute of
    // staleness on a work queue is not worth holding the connection pool.
    staleTime: 60_000,
  });
}
