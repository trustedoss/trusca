// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import {
  DISTRIBUTION_MODELS,
  UNSET_DISTRIBUTION_MODEL,
} from "@/lib/projectConstants";
import { cn } from "@/lib/utils";

/**
 * ProjectListToolbar — Phase 2 PR #9 task 2.11.
 *
 * Inline filter row above the virtualized list (CLAUDE.md "디자인 시스템" —
 * filters appear inline at the top of lists, no modal filter dialogs). The
 * toolbar is purely controlled: it exposes the current filter values and
 * dispatches change events upward; the parent owns all filter state.
 */

export type ProjectStatusFilter =
  | "all"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "idle";

export type ProjectSortKey = "name" | "latest_scan" | "risk";

const STATUS_OPTIONS: ProjectStatusFilter[] = [
  "all",
  "queued",
  "running",
  "succeeded",
  "failed",
  "idle",
];

const SORT_OPTIONS: ProjectSortKey[] = ["name", "latest_scan", "risk"];

export interface ProjectListToolbarProps {
  query: string;
  onQueryChange: (value: string) => void;
  status: ProjectStatusFilter;
  onStatusChange: (value: ProjectStatusFilter) => void;
  sort: ProjectSortKey;
  onSortChange: (value: ProjectSortKey) => void;
  /** Null means no filter, which keeps every project including the unset ones. */
  distribution: string | null;
  onDistributionChange: (value: string | null) => void;
  /**
   * The teams represented in the currently loaded page, `{id, name}`. Fewer
   * than two entries means every visible row already shares one team (the
   * common case for a team-scoped Developer/Team Admin), so the control adds
   * nothing there: the toolbar renders no team filter at all rather than a
   * useless one-option dropdown.
   */
  teamOptions: { id: string; name: string }[];
  /** Null means no filter, which keeps every team's projects in. */
  team: string | null;
  onTeamChange: (value: string | null) => void;
  className?: string;
}

export function ProjectListToolbar({
  query,
  onQueryChange,
  status,
  onStatusChange,
  sort,
  onSortChange,
  distribution,
  onDistributionChange,
  teamOptions,
  team,
  onTeamChange,
  className,
}: ProjectListToolbarProps) {
  const { t } = useTranslation("projects");
  return (
    <div
      className={cn(
        // W11-B polish — Vercel-style horizontal toolbar: bg shifts to the
        // off-white canvas so the surrounding white cards visually pop, and
        // the inner spacing standardises on the 12 / 16 / 24 px scale (gap-3,
        // px-6, py-3). Inputs get `shadow-sm` so the row reads as raised.
        "flex flex-col gap-3 border-b bg-background px-6 py-3 md:flex-row md:items-center md:gap-4",
        className,
      )}
      data-testid="project-list-toolbar"
    >
      <div className="flex-1">
        <label htmlFor="project-search" className="sr-only">
          {t("toolbar.search_placeholder")}
        </label>
        <Input
          id="project-search"
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={t("toolbar.search_placeholder")}
          data-testid="project-search"
          className="h-9 shadow-sm"
        />
      </div>

      <div className="flex items-center gap-2">
        <label
          htmlFor="project-status-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("toolbar.filter_status_label")}
        </label>
        <select
          id="project-status-filter"
          value={status}
          onChange={(event) =>
            onStatusChange(event.target.value as ProjectStatusFilter)
          }
          className="h-9 rounded-md border border-input bg-background px-2 text-sm shadow-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="project-status-filter"
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt === "all"
                ? t("toolbar.filter_status_all")
                : t(`status.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2">
        <label
          htmlFor="project-distribution-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("toolbar.filter_distribution_label")}
        </label>
        <select
          id="project-distribution-filter"
          value={distribution ?? ""}
          onChange={(event) =>
            // Empty back to null rather than an empty string: the two mean the
            // same thing to the server, but null is what the URL and the query
            // key read as "no filter", and mixing them would make two cache
            // entries for one view.
            onDistributionChange(event.target.value || null)
          }
          className="h-9 rounded-md border border-input bg-background px-2 text-sm shadow-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="project-distribution-filter"
        >
          <option value="">{t("toolbar.filter_distribution_all")}</option>
          {DISTRIBUTION_MODELS.map((model) => (
            <option key={model} value={model}>
              {t(`toolbar.filter_distribution_option.${model}`)}
            </option>
          ))}
          <option value={UNSET_DISTRIBUTION_MODEL}>
            {t("toolbar.filter_distribution_unset")}
          </option>
        </select>
      </div>

      {teamOptions.length > 1 ? (
        <div className="flex items-center gap-2">
          <label
            htmlFor="project-team-filter"
            className="text-xs font-medium text-muted-foreground"
          >
            {t("toolbar.filter_team_label")}
          </label>
          <select
            id="project-team-filter"
            value={team ?? ""}
            onChange={(event) => onTeamChange(event.target.value || null)}
            className="h-9 rounded-md border border-input bg-background px-2 text-sm shadow-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="project-team-filter"
          >
            <option value="">{t("toolbar.filter_team_all")}</option>
            {teamOptions.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <label
          htmlFor="project-sort"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("toolbar.sort_label")}
        </label>
        <select
          id="project-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as ProjectSortKey)
          }
          className="h-9 rounded-md border border-input bg-background px-2 text-sm shadow-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="project-sort"
        >
          {SORT_OPTIONS.map((key) => (
            <option key={key} value={key}>
              {t(`toolbar.sort_by_${key}`)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
