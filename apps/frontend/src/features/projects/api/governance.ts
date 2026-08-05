// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * `GET /v1/projects/{id}/governance` — the band above the project tabs.
 *
 * Mirrors `schemas/project_governance.py`. Nothing here is measured by this
 * endpoint; it composes what the Overview tab, CI's gate, the vulnerability
 * list and the approvals page already own, so the band cannot disagree with
 * the tab below it.
 *
 * `scanned` is the field that carries meaning beyond its own value: every
 * number is zero on a project nobody has scanned, and zero there does not
 * mean clean.
 */

export interface GovernanceGate {
  status: "pass" | "fail" | null;
  critical_cve_count: number;
  forbidden_license_count: number;
  epss_gate_count: number;
  malicious_component_count: number;
  scan_id: string | null;
}

export interface GovernanceTrendPoint {
  scan_id: string;
  scanned_at: string;
  critical: number;
}

export interface ProjectGovernance {
  project_id: string;
  scanned: boolean;
  risk_score: number;
  gate: GovernanceGate;
  kev_sla: { overdue: number; due_soon: number };
  pending_approvals: number;
  trend: GovernanceTrendPoint[];
}

export async function getProjectGovernance(
  projectId: string,
): Promise<ProjectGovernance> {
  const { data } = await api.get<ProjectGovernance>(
    `/v1/projects/${projectId}/governance`,
  );
  return data;
}

export const GOVERNANCE_QUERY_KEY = ["project", "governance"] as const;

export function useProjectGovernance(projectId: string | null) {
  return useQuery({
    queryKey: [...GOVERNANCE_QUERY_KEY, projectId],
    queryFn: () => getProjectGovernance(projectId as string),
    enabled: projectId != null,
    // The gate and the approval count move when someone triages or reviews,
    // neither of which this page causes — a minute of staleness on a summary
    // strip is a better trade than polling five aggregates.
    staleTime: 60_000,
  });
}
