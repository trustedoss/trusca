import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type { DependencyScopeFilter } from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * ComponentsToolbar — Phase 3 PR #10, updated in W4-B #17.
 *
 * Inline filter row above the virtualized component list. CLAUDE.md
 * "디자인 시스템" — filters appear inline at the top of lists, no modal
 * filter dialogs.
 *
 * W4-B #17 removed two control families:
 *   - Severity + License MultiSelect drops: replaced by the Overview chart
 *     deep-links (#16) + the standalone `ActiveFilterChips` row above the
 *     table. The URL params (`?severity=`, `?license_category=`) are still
 *     honoured by the parent `ComponentsTab`; the toolbar simply no longer
 *     hosts a "type into a dropdown" UI for them.
 *   - Sort + Order <select> drops: replaced by inline `SortableColumnHeader`
 *     primitives on the column heads in the table itself.
 *
 * What stays here: Search, Dependency-type segmented control, Usage multi
 * select. They have no equivalent affordance elsewhere on the page.
 */

/**
 * W2 #31 — BD-style "Usage" facet options. Mirrors the backend's accepted
 * values for ``?dependency_scope=``; ``unspecified`` selects the NULL-scope
 * bucket (cdxgen often produces no scope on edges).
 */
export const USAGE_OPTIONS: DependencyScopeFilter[] = [
  "required",
  "optional",
  "unspecified",
];

/**
 * W2 #31 — Dependency-type segmented control values. ``null`` means "All"
 * (no `?direct=` on the wire), ``true`` keeps only direct, ``false`` only
 * transitive (and the depth-null bucket).
 */
const DEPENDENCY_TYPE_OPTIONS: { value: boolean | null; key: string }[] = [
  { value: null, key: "all" },
  { value: true, key: "direct" },
  { value: false, key: "transitive" },
];

export interface ComponentsToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  /** Dependency-type 3-state (null/true/false). */
  direct: boolean | null;
  onDirectChange: (value: boolean | null) => void;
  /** BD-style "Usage" multi-select. */
  dependencyScope: DependencyScopeFilter[];
  onDependencyScopeChange: (value: DependencyScopeFilter[]) => void;
  className?: string;
}

export function ComponentsToolbar({
  search,
  onSearchChange,
  direct,
  onDirectChange,
  dependencyScope,
  onDependencyScopeChange,
  className,
}: ComponentsToolbarProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="components-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="components-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.search_label")}
        </label>
        <Input
          id="components-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("components.toolbar.search_placeholder")}
          data-testid="components-search"
          className="mt-1 h-9"
        />
      </div>

      <div
        className="flex flex-col"
        data-testid="components-dependency-type-filter"
      >
        <span className="text-xs font-medium text-muted-foreground">
          {t("components.toolbar.dependency_type_label")}
        </span>
        <div
          role="group"
          aria-label={t("components.toolbar.dependency_type_label")}
          className="mt-1 inline-flex h-9 items-stretch overflow-hidden rounded-md border border-input bg-background"
        >
          {DEPENDENCY_TYPE_OPTIONS.map((opt, idx) => {
            const isActive = direct === opt.value;
            return (
              <button
                key={opt.key}
                type="button"
                aria-pressed={isActive}
                data-testid={`components-dependency-type-${opt.key}`}
                data-active={isActive ? "true" : "false"}
                onClick={() => onDirectChange(opt.value)}
                className={cn(
                  "px-3 text-xs font-medium",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  idx > 0 && "border-l border-input",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {t(`components.toolbar.dependency_type.${opt.key}`)}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-usage-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.usage_label")}
        </label>
        <MultiSelect
          id="components-usage-filter"
          testId="components-usage-filter"
          className="w-40"
          label={t("components.toolbar.usage_label")}
          options={USAGE_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`components.toolbar.usage.${opt}`),
          }))}
          selected={dependencyScope}
          onChange={(next) =>
            onDependencyScopeChange(next as DependencyScopeFilter[])
          }
        />
      </div>
    </div>
  );
}
