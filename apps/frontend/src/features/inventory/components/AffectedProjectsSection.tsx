// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useVulnerabilityImpact } from "@/features/inventory/api/useInventory";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";

export interface AffectedProjectsSectionProps {
  /** The CVE id, e.g. `CVE-2021-23337`. Omit to render nothing. */
  externalId: string | null | undefined;
  /** The project already on screen — its own row is labelled, not hidden. */
  currentProjectId?: string;
}

/**
 * "Which other projects does this CVE reach" — the question a triager asks
 * immediately after reading a finding, and which the per-project page could not
 * answer before S2.
 *
 * Renders nothing when the CVE reaches only the project already on screen:
 * a section whose entire content is "just this one" is noise on a page that
 * already says which project it belongs to. It also renders nothing on error —
 * a 404 here means the CVE reaches none of the caller's projects, which given
 * the surrounding page cannot be interesting, and a transient failure should
 * not put an error box next to a finding that loaded fine.
 */
export function AffectedProjectsSection({
  externalId,
  currentProjectId,
}: AffectedProjectsSectionProps) {
  const { t } = useTranslation("inventory");
  const impact = useVulnerabilityImpact(externalId);

  if (!externalId) return null;

  if (impact.isLoading) {
    return (
      <Card data-testid="affected-projects-loading">
        <CardHeader>
          <CardTitle className="text-base">{t("affected.title")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {Array.from({ length: 2 }).map((_, index) => (
            <Skeleton key={index} className="h-8 w-full" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (impact.isError || !impact.data) return null;

  const rows = impact.data.items;
  const otherProjects = new Set(
    rows
      .map((row) => row.project_id)
      .filter((projectId) => projectId !== currentProjectId),
  );
  if (otherProjects.size === 0) return null;

  return (
    <Card data-testid="affected-projects">
      <CardHeader>
        <CardTitle className="flex items-baseline gap-2 text-base">
          <span>{t("affected.title")}</span>
          <span
            className="text-sm font-normal text-muted-foreground"
            data-testid="affected-projects-count"
            data-count={otherProjects.size}
          >
            {t("affected.also_in", { count: otherProjects.size })}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col divide-y" data-total={impact.data.total}>
          {rows.map((row) => (
            <li
              key={row.finding_id}
              className="flex items-center justify-between gap-3 py-2 text-sm"
              data-testid="affected-projects-row"
              data-project-id={row.project_id}
            >
              <span className="flex min-w-0 flex-col">
                <Link
                  to={`/projects/${row.project_id}/vulnerabilities/${row.finding_id}`}
                  className="truncate font-medium underline-offset-4 hover:underline"
                >
                  {row.project_name}
                </Link>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {row.component_name} {row.version}
                </span>
              </span>
              <span className="flex items-center gap-2">
                {row.project_id === currentProjectId ? (
                  <Badge>{t("affected.this_project")}</Badge>
                ) : null}
                <SeverityBadge severity={row.severity} />
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
