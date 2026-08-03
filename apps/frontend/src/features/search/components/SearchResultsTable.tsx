// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import {
  COMPONENTS_SEARCH_PARAM,
  VULNERABILITIES_SEARCH_PARAM,
} from "@/features/projects/components/tabSearchParam";
import type { SearchResultsPage } from "@/features/search/api/searchResultsApi";
import type { ComponentSeverity } from "@/features/projects/api/projectDetailApi";
import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

/**
 * The result table for whichever kind the page is showing.
 *
 * Every row is a link out. A search result the user cannot act on is a dead
 * end, so each kind lands somewhere specific: a project on its detail page, a
 * component or CVE on the owning project's tab already filtered to it, a
 * licence on the project's compliance surface.
 */

export interface SearchResultsTableProps {
  page: SearchResultsPage;
}

function HeaderRow({ labels }: { labels: string[] }) {
  return (
    <div
      role="row"
      className="flex items-center gap-3 border-b px-6 py-2 text-xs font-medium text-muted-foreground"
    >
      {labels.map((label) => (
        <span key={label} role="columnheader" className="flex-1">
          {label}
        </span>
      ))}
    </div>
  );
}

export function SearchResultsTable({ page }: SearchResultsTableProps) {
  const { t } = useTranslation("search");

  return (
    <div role="table" data-testid="search-results" data-kind={page.kind}>
      {page.kind === "projects" ? (
        <>
          <HeaderRow labels={[t("col.project"), t("col.git_url")]} />
          {page.items_projects.map((row) => (
            <div
              key={row.project_id}
              role="row"
              data-testid="search-result-row"
              data-project-id={row.project_id}
              className="flex items-center gap-3 border-b px-6 py-2 text-sm"
            >
              <span role="cell" className="flex flex-1 items-center gap-2">
                <Link
                  to={`/projects/${row.project_id}`}
                  className="font-medium underline-offset-4 hover:underline"
                >
                  {row.project_name}
                </Link>
                {row.archived ? <Badge>{t("badge.archived")}</Badge> : null}
              </span>
              <span
                role="cell"
                className="flex-1 truncate font-mono text-xs text-muted-foreground"
              >
                {row.git_url ?? "—"}
              </span>
            </div>
          ))}
        </>
      ) : null}

      {page.kind === "components" ? (
        <>
          <HeaderRow
            labels={[t("col.package"), t("col.version"), t("col.project")]}
          />
          {page.items_components.map((row) => (
            <div
              key={`${row.project_id}-${row.component_id}-${row.version}`}
              role="row"
              data-testid="search-result-row"
              data-project-id={row.project_id}
              className="flex items-center gap-3 border-b px-6 py-2 text-sm"
            >
              <span role="cell" className="flex min-w-0 flex-1 flex-col">
                <Link
                  to={`/projects/${row.project_id}?tab=components&${COMPONENTS_SEARCH_PARAM}=${encodeURIComponent(row.component_name)}`}
                  className="truncate font-medium underline-offset-4 hover:underline"
                >
                  {row.component_name}
                </Link>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {row.purl}
                </span>
              </span>
              <span role="cell" className="flex-1 font-mono text-xs">
                {row.version}
              </span>
              <span role="cell" className="flex-1 truncate">
                {row.project_name}
              </span>
            </div>
          ))}
        </>
      ) : null}

      {page.kind === "vulnerabilities" ? (
        <>
          <HeaderRow
            labels={[
              t("col.cve"),
              t("col.severity"),
              t("col.package"),
              t("col.project"),
            ]}
          />
          {page.items_vulnerabilities.map((row) => (
            <div
              key={row.finding_id}
              role="row"
              data-testid="search-result-row"
              data-project-id={row.project_id}
              data-severity={row.severity}
              className="flex items-center gap-3 border-b px-6 py-2 text-sm"
            >
              <span role="cell" className="flex-1">
                <Link
                  to={`/projects/${row.project_id}/vulnerabilities/${row.finding_id}`}
                  className="font-mono font-medium underline-offset-4 hover:underline"
                >
                  {row.cve_id}
                </Link>
              </span>
              <span role="cell" className="flex flex-1 items-center gap-2">
                <SeverityBadge severity={row.severity as ComponentSeverity} />
                <Badge>{row.status}</Badge>
              </span>
              <span role="cell" className="flex-1 truncate">
                {row.component_name} {row.version}
              </span>
              <span role="cell" className="flex-1 truncate">
                <Link
                  to={`/projects/${row.project_id}?tab=vulnerabilities&${VULNERABILITIES_SEARCH_PARAM}=${encodeURIComponent(row.cve_id)}`}
                  className="underline-offset-4 hover:underline"
                >
                  {row.project_name}
                </Link>
              </span>
            </div>
          ))}
        </>
      ) : null}

      {page.kind === "licenses" ? (
        <>
          <HeaderRow
            labels={[t("col.license"), t("col.package"), t("col.project")]}
          />
          {page.items_licenses.map((row) => (
            <div
              key={`${row.project_id}-${row.license_id}-${row.component_name}-${row.version}`}
              role="row"
              data-testid="search-result-row"
              data-project-id={row.project_id}
              className="flex items-center gap-3 border-b px-6 py-2 text-sm"
            >
              <span role="cell" className="flex flex-1 items-center gap-2">
                <span className="font-mono text-xs">
                  {row.spdx_id ?? row.license_name}
                </span>
                <LicenseCategoryBadge
                  category={row.category as LicenseCategoryName}
                />
              </span>
              <span role="cell" className="flex-1 truncate">
                {row.component_name} {row.version}
              </span>
              <span role="cell" className="flex-1 truncate">
                <Link
                  to={`/projects/${row.project_id}?tab=compliance`}
                  className="underline-offset-4 hover:underline"
                >
                  {row.project_name}
                </Link>
              </span>
            </div>
          ))}
        </>
      ) : null}
    </div>
  );
}
