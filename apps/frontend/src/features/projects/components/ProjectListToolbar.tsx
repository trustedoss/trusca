import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
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
  className?: string;
}

export function ProjectListToolbar({
  query,
  onQueryChange,
  status,
  onStatusChange,
  sort,
  onSortChange,
  className,
}: ProjectListToolbarProps) {
  const { t } = useTranslation("projects");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 md:flex-row md:items-center md:gap-4",
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
          className="h-9"
        />
      </div>

      <div className="flex items-center gap-2">
        <label
          htmlFor="project-status-filter"
          className="text-xs text-muted-foreground"
        >
          {t("toolbar.filter_status_label")}
        </label>
        <select
          id="project-status-filter"
          value={status}
          onChange={(event) =>
            onStatusChange(event.target.value as ProjectStatusFilter)
          }
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
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
        <label htmlFor="project-sort" className="text-xs text-muted-foreground">
          {t("toolbar.sort_label")}
        </label>
        <select
          id="project-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as ProjectSortKey)
          }
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
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
