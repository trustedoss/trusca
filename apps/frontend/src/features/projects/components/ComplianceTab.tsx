// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * ComplianceTab — W9-#58 unified Licenses × Obligations grid.
 *
 * This tab now renders a single read-only grid keyed by license. Each row
 * carries the license inventory plus the obligations attached to that
 * license inline — answering "what licenses am I shipping AND what do they
 * require?" in one surface without the W4-C sub-tab toggle.
 *
 * Why a single grid
 * -----------------
 * The W4-C IA overhaul collapsed Licenses + Obligations into one top-level
 * tab, but the implementation still split the surface in two via
 * ``?cview=licenses|obligations``. Users were paying for two virtualized
 * tables and one extra mental model (which sub-tab am I on?) when in
 * practice every Compliance question — "do we ship GPL?", "what NOTICE
 * entries do we owe?" — needs both axes at once. The new grid removes the
 * toggle and the second fetch.
 *
 * Wire
 * ----
 * One endpoint: ``GET /v1/projects/{id}/compliance``. Returns a
 * license-grained row carrying:
 *
 *   - the license itself (SPDX, name, category)
 *   - affected components (preview + total count)
 *   - obligations (inline, summary capped to 240 chars by the service)
 *   - notice_required (derived from the obligation kinds)
 *
 * The row's ``license_finding_id`` is the same opaque handle the existing
 * LicenseDrawer (``GET /v1/license_findings/{id}``) accepts, so the drawer
 * is reused verbatim — no new endpoint, no drawer fork.
 *
 * URL state
 * ---------
 *   - ``?compliance_search=…``           free-text (SPDX or name)
 *   - ``?compliance_category=a,b,c``     comma-separated category filter
 *   - ``?compliance_has_obligations=true|false`` boolean filter
 *   - ``?compliance_conflict=<verdict>``  outbound-license conflict (gap #27)
 *   - ``?compliance_sort=category|license_name|spdx_id|affected_count``
 *   - ``?compliance_order=asc|desc``     order toggle
 *   - ``?license=<finding_id>``          drawer selection
 *
 * There is no page parameter. There was one, and nothing incremented it.
 *
 * Backward compatibility (W4-C)
 * -----------------------------
 *   - ``?cview=licenses``     → strip the param, no other change. The unified
 *                               grid IS the licenses view.
 *   - ``?cview=obligations``  → strip the param + set
 *                               ``?compliance_has_obligations=true`` so the
 *                               user lands on rows that actually carry
 *                               obligations (the old obligations sub-view
 *                               equivalent). Done once on mount.
 *   - The old per-tab params (``search``, ``license_category``, ``kind``,
 *     ``sort``, ``order``, ``page``) are deliberately NOT consumed. The grid
 *     owns its own namespace (``compliance_*``) so a stale deep-link with a
 *     ``kind=`` value does not collide with another tab's state.
 *
 * Read-only domain — no analyst workflow, no transitions, no audit log.
 */
import { FileCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import { Skeleton } from "@/components/ui/skeleton";
import { VIRTUOSO_TABLE_BODY } from "@/components/ui/virtuoso-table-body";
import { Switch } from "@/components/ui/switch";
import type {
  LicenseCategoryName,
  TeamScopedRole,
} from "@/features/projects/api/projectDetailApi";
import type {
  ComplianceObligation,
  ComplianceRow as ComplianceRowItem,
  ComplianceSortKey,
  ConflictVerdictName,
  SortOrder,
} from "@/features/projects/api/complianceApi";
import { CONFLICT_VERDICT_VALUES } from "@/features/projects/api/licensesApi";
import type { NoticeFormat } from "@/features/projects/api/obligationsApi";
import { useCompliance } from "@/features/projects/api/useCompliance";
import {
  findComponentException,
  useTeamLicensePolicy,
  type LicenseException,
} from "@/features/projects/api/useLicenseWaive";
import { useNotice } from "@/features/projects/api/useNotice";
import { ConflictVerdictBadge } from "@/features/projects/components/ConflictVerdictBadge";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { LicenseDrawer } from "@/features/projects/components/LicenseDrawer";
import { LicenseWaiveAction } from "@/features/projects/components/LicenseWaiveAction";
import { useAdvisoryTranslation } from "@/lib/advisoryTranslation";
import type { LicensePolicyOut } from "@/lib/licensePoliciesApi";
import { problemMessage } from "@/lib/problemMessage";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;

/** M-21 — NOTICE formats offered by the toolbar (mirrors ReportsTab). */
const NOTICE_DOWNLOAD_FORMATS: NoticeFormat[] = ["text", "html"];

const VALID_CATEGORY = new Set<LicenseCategoryName>([
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
]);

const VALID_SORT = new Set<ComplianceSortKey>([
  "category",
  "license_name",
  "spdx_id",
  "affected_count",
]);

const CATEGORY_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

const SORT_OPTIONS: ComplianceSortKey[] = [
  "category",
  "license_name",
  "spdx_id",
  "affected_count",
];

function parseList<T extends string>(raw: string | null, valid: Set<T>): T[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter((v): v is T => valid.has(v as T));
}

function parseSort(raw: string | null): ComplianceSortKey {
  if (raw && VALID_SORT.has(raw as ComplianceSortKey)) {
    return raw as ComplianceSortKey;
  }
  return "category";
}

function parseOrder(raw: string | null): SortOrder {
  return raw === "asc" ? "asc" : "desc";
}

const VALID_CONFLICT = new Set<ConflictVerdictName>(CONFLICT_VERDICT_VALUES);

function parseConflict(raw: string | null): ConflictVerdictName | undefined {
  return raw && VALID_CONFLICT.has(raw as ConflictVerdictName)
    ? (raw as ConflictVerdictName)
    : undefined;
}

function parseHasObligations(raw: string | null): boolean | null {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return null;
}

export interface ComplianceTabProps {
  projectId: string;
  /**
   * Project name — names the NOTICE download produced by the toolbar
   * (M-21 restored the affordance the old obligations sub-view carried;
   * the Reports tab keeps its own card). Falls back to the project id in
   * the filename when omitted.
   */
  projectName?: string | null;
  /**
   * Pinned snapshot scan id (feature #28). When set the grid reflects that
   * historical scan instead of the latest succeeded one.
   */
  scanId?: string;
  /**
   * Owning team of the project. Threads into the per-component license waive
   * action (which targets ``/v1/license-policies/teams/{team_id}/exceptions``).
   * ``null`` until the project summary resolves → waive actions stay disabled.
   */
  teamId?: string | null;
  /**
   * The actor's effective role within the project's owning team. Gates the
   * waive action (team_admin / super_admin only).
   */
  projectRole?: TeamScopedRole;
  /**
   * Read-only historical snapshot (feature #28). When ``true`` the waive
   * affordances are disabled — waiving would mutate the *current* policy while
   * the user is viewing an older scan.
   */
  readOnly?: boolean;
  /**
   * Starts a scan from the empty state. Omitted when the caller cannot scan,
   * and the empty state then explains without offering a button that would
   * fail.
   */
  onScan?: () => void;
}

export function ComplianceTab({
  projectId,
  projectName = null,
  scanId,
  teamId = null,
  projectRole = "developer",
  readOnly = false,
  onScan,
}: ComplianceTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  // ----- backward-compat for W4-C ``?cview=`` ------------------------------
  // Run once on mount: ``cview=obligations`` rewrites to
  // ``compliance_has_obligations=true``, ``cview=licenses`` is a no-op (the
  // unified grid IS the licenses view). Either way we drop the param so the
  // canonical URL matches the new IA.
  useEffect(() => {
    const cview = searchParams.get("cview");
    if (cview == null) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("cview");
        if (cview === "obligations") {
          next.set("compliance_has_obligations", "true");
        }
        // Stale legacy keys from the obligations sub-view are dropped so
        // they do not confuse the unified grid.
        for (const stale of ["obligation"]) {
          next.delete(stale);
        }
        return next;
      },
      { replace: true },
    );
    // We intentionally run this once on mount only — re-running on every
    // searchParams change would fight the user's own filter edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ----- filter state, hydrated from URL on first render -------------------
  const [search, setSearch] = useState(
    () => searchParams.get("compliance_search") ?? "",
  );
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [categories, setCategories] = useState<LicenseCategoryName[]>(() =>
    parseList<LicenseCategoryName>(
      searchParams.get("compliance_category"),
      VALID_CATEGORY,
    ),
  );
  const [hasObligations, setHasObligations] = useState<boolean | null>(() => {
    // Honour the freshly-rewritten cview=obligations on first paint.
    const fromParam = parseHasObligations(
      searchParams.get("compliance_has_obligations"),
    );
    if (fromParam !== null) return fromParam;
    if (searchParams.get("cview") === "obligations") return true;
    return null;
  });
  const [conflict, setConflict] = useState<ConflictVerdictName | undefined>(() =>
    parseConflict(searchParams.get("compliance_conflict")),
  );
  const [sort, setSort] = useState<ComplianceSortKey>(() =>
    parseSort(searchParams.get("compliance_sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("compliance_order")),
  );

  // Drawer selection. The ``?license=<finding_id>`` key predates this tab, so
  // a deep-link from a chart or a recent-finding card still works.
  const drawerId = searchParams.get("license");
  const drawerOpen = drawerId != null && drawerId.length > 0;

  /**
   * Pushed, not replaced, so Back closes the drawer rather than leaving the
   * tab and discarding the filters the user set to find this licence.
   */
  function setDrawerLicense(findingId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (findingId) {
          next.set("license", findingId);
        } else {
          next.delete("license");
        }
        return next;
      },
      { replace: false },
    );
  }

  // Debounce the search input → 300ms before a network call.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(search);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  /** Whether anything is narrowing the grid, which decides the empty state. */
  const hasNarrowingFilters =
    debouncedSearch.trim().length > 0 ||
    categories.length > 0 ||
    hasObligations !== null ||
    conflict !== undefined;

  function clearAllFilters() {
    setSearch("");
    setDebouncedSearch("");
    setCategories([]);
    setHasObligations(null);
    setConflict(undefined);
  }

  // Mirror filter state into URL params. We omit defaults so canonical URLs
  // stay short and the W4-C migration above produces a clean reload.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedSearch) next.set("compliance_search", debouncedSearch);
        else next.delete("compliance_search");
        if (categories.length)
          next.set("compliance_category", categories.join(","));
        else next.delete("compliance_category");
        if (hasObligations === true) {
          next.set("compliance_has_obligations", "true");
        } else if (hasObligations === false) {
          next.set("compliance_has_obligations", "false");
        } else {
          next.delete("compliance_has_obligations");
        }
        if (conflict) next.set("compliance_conflict", conflict);
        else next.delete("compliance_conflict");
        if (sort !== "category") next.set("compliance_sort", sort);
        else next.delete("compliance_sort");
        if (order !== "desc") next.set("compliance_order", order);
        else next.delete("compliance_order");
        // The grid is infinite now. The parameter it used to write described
        // a position nothing could move to, so it is dropped rather than kept
        // in sync with a page index that no longer exists.
        next.delete("compliance_page");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    categories,
    hasObligations,
    conflict,
    sort,
    order,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      categories,
      kinds: [],
      hasObligations,
      conflict,
      sort,
      order,
      limit: PAGE_SIZE,
      scanId,
    }),
    [
      debouncedSearch,
      categories,
      hasObligations,
      conflict,
      sort,
      order,
      scanId,
    ],
  );

  const compliance = useCompliance(projectId, filters);

  // Effective team policy carries the per-component waivers. A 404 ("no team
  // policy, static fallback") resolves to null — not an error — so the grid
  // still renders waive affordances. Only fetched when we know the team.
  const teamPolicy = useTeamLicensePolicy(teamId);
  const policy = teamPolicy.data ?? null;

  const pages = compliance.data?.pages;
  const items: ComplianceRowItem[] = useMemo(
    () => (pages ?? []).flatMap((p) => p.items),
    [pages],
  );
  // Whole-grid facts repeat on every page, so the first one answers for all
  // of them and does not shift as more pages arrive.
  const total = pages?.[0]?.total ?? 0;
  const declaredLicense = pages?.[0]?.declared_license ?? null;
  const conflictSummary = pages?.[0]?.conflict_summary ?? null;
  // Three states, not two. A declared license with no summary means the scan
  // carries more licenses than one request will judge — nothing was assessed,
  // so the verdict column stays off rather than rendering blank cells that
  // would read as "no conflict".
  const conflictAssessed = declaredLicense != null && conflictSummary != null;

  return (
    <div data-testid="compliance-tab" className="flex flex-1 flex-col">
      <ComplianceToolbar
        projectId={projectId}
        projectName={projectName}
        scanId={scanId}
        search={search}
        onSearchChange={setSearch}
        categories={categories}
        onCategoriesChange={(next) => {
          setCategories(next);
        }}
        hasObligations={hasObligations}
        onHasObligationsChange={(next) => {
          setHasObligations(next);
        }}
        sort={sort}
        onSortChange={(next) => {
          setSort(next);
        }}
        conflict={conflict}
        onConflictChange={(next) => {
          setConflict(next);
        }}
        conflictEnabled={conflictAssessed}
        order={order}
        onOrderChange={(next) => {
          setOrder(next);
        }}
      />

      <div
        className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-xs text-muted-foreground"
        data-testid="compliance-summary"
        data-total={total}
        data-loaded={items.length}
        data-declared-license={declaredLicense ?? ""}
      >
        <span>
          {t("compliance.summary", {
            loaded: items.length,
            total,
          })}
        </span>
        {conflictAssessed ? (
          <span
            className="flex flex-wrap items-center gap-2"
            data-testid="compliance-outbound"
          >
            <span>
              {t("licenses.conflict.measured_against", {
                license: declaredLicense,
              })}
            </span>
            {conflictSummary && conflictSummary.incompatible > 0 ? (
              <span
                className="font-medium text-risk-critical-foreground"
                data-testid="compliance-conflict-count-incompatible"
              >
                {t("licenses.conflict.count.incompatible", {
                  count: conflictSummary.incompatible,
                })}
              </span>
            ) : null}
            {conflictSummary && conflictSummary.conditional > 0 ? (
              <span
                className="font-medium text-risk-medium-foreground"
                data-testid="compliance-conflict-count-conditional"
              >
                {t("licenses.conflict.count.conditional", {
                  count: conflictSummary.conditional,
                })}
              </span>
            ) : null}
            <span className="opacity-80">{t("licenses.conflict.advisory")}</span>
          </span>
        ) : declaredLicense ? (
          <span data-testid="compliance-outbound-not-assessed">
            {t("licenses.conflict.not_assessed", { license: declaredLicense })}
          </span>
        ) : (
          // Absent is not clean: a project with no declared outbound license
          // has not been assessed, and an empty column would otherwise read as
          // "no conflicts found".
          <span data-testid="compliance-outbound-undeclared">
            {t("licenses.conflict.undeclared")}
          </span>
        )}
      </div>

      {compliance.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="compliance-error">
            <AlertDescription>
              {problemMessage(compliance.error, t, {
                action: "compliance.errors.load_list",
              })}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      {compliance.isLoading ? (
        <div
          className="flex flex-col gap-2 px-4 py-3"
          data-testid="compliance-loading"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : null}

      {!compliance.isLoading && !compliance.isError && items.length === 0 ? (
        <EmptyState
          data-testid="compliance-empty"
          className="m-6"
          icon={<FileCheck />}
          title={
            hasNarrowingFilters
              ? t("compliance.empty.filtered_title")
              : t("compliance.empty.title")
          }
          description={
            hasNarrowingFilters
              ? t("compliance.empty.filtered_description")
              : t("compliance.empty.description")
          }
          // The copy has told people to run a scan since this tab existed,
          // with no button next to it. Either give them the button or stop
          // telling them — and never tell someone with a filter on, because
          // scanning will not change what the filter excludes.
          action={
            hasNarrowingFilters ? (
              <Button
                variant="outline"
                size="sm"
                onClick={clearAllFilters}
                data-testid="compliance-empty-clear-filters"
              >
                {t("compliance.empty.clear_filters")}
              </Button>
            ) : onScan ? (
              <Button
                size="sm"
                onClick={onScan}
                data-testid="compliance-empty-scan"
              >
                {t("compliance.empty.run_scan")}
              </Button>
            ) : undefined
          }
        />
      ) : null}

      {!compliance.isLoading && !compliance.isError && items.length > 0 ? (
        // G0-5 gave the components and vulnerabilities grids their table
        // semantics and left these two out, so a screen reader met an
        // unlabelled stack of divs where the other tabs announce a table with
        // a row count. The `rowgroup` lands on Virtuoso's scroller for the
        // reason recorded in VulnerabilitiesTab: the scroller is focusable, so
        // it is the one wrapper the accessibility tree will not flatten.
        <div
          className="flex flex-1 flex-col"
          role="table"
          aria-label={t("compliance.table_aria")}
          aria-rowcount={total + 1}
        >
          <ComplianceTableHeader showConflict={conflictAssessed} />
          <div
            className="flex-1"
            data-testid="compliance-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              components={VIRTUOSO_TABLE_BODY}
              data={items}
              endReached={() => {
                if (compliance.hasNextPage && !compliance.isFetchingNextPage) {
                  void compliance.fetchNextPage();
                }
              }}
              style={{
                height: "calc(100vh - var(--layout-header) - 240px)",
              }}
              itemContent={(index, item) => (
                <ComplianceGridRow
                  row={item}
                  rowIndex={index}
                  showConflict={conflictAssessed}
                  onSelect={() => setDrawerLicense(item.license_finding_id)}
                  projectId={projectId}
                  teamId={teamId}
                  projectRole={projectRole}
                  readOnly={readOnly}
                  policy={policy}
                />
              )}
            />
          </div>
        </div>
      ) : null}

      <LicenseDrawer
        open={drawerOpen}
        findingId={drawerId}
        // Carried from the selected row: the drawer's own endpoint is keyed by
        // finding and knows nothing about the project's outbound license.
        conflict={
          items.find((it) => it.license_finding_id === drawerId)?.conflict ??
          null
        }
        onOpenChange={(open) => {
          if (!open) setDrawerLicense(null);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toolbar (inline, no modal — CLAUDE.md "디자인 시스템")
// ---------------------------------------------------------------------------

interface ComplianceToolbarProps {
  /** M-21 — NOTICE download target. */
  projectId: string;
  /** M-21 — names the downloaded NOTICE file (id fallback when null). */
  projectName: string | null;
  /** Release-snapshot pin, so the NOTICE matches the grid below it. */
  scanId?: string;
  search: string;
  onSearchChange: (value: string) => void;
  categories: LicenseCategoryName[];
  onCategoriesChange: (value: LicenseCategoryName[]) => void;
  hasObligations: boolean | null;
  onHasObligationsChange: (value: boolean | null) => void;
  conflict: ConflictVerdictName | undefined;
  onConflictChange: (value: ConflictVerdictName | undefined) => void;
  /**
   * False when the project declares no outbound license. The facet is hidden
   * rather than disabled: a filter that can only ever return nothing is not a
   * control the user should have to try before learning that.
   */
  conflictEnabled: boolean;
  sort: ComplianceSortKey;
  onSortChange: (value: ComplianceSortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
}

function ComplianceToolbar({
  projectId,
  projectName,
  scanId,
  search,
  onSearchChange,
  categories,
  onCategoriesChange,
  hasObligations,
  onHasObligationsChange,
  conflict,
  onConflictChange,
  conflictEnabled,
  sort,
  onSortChange,
  order,
  onOrderChange,
}: ComplianceToolbarProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4"
      data-testid="compliance-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="compliance-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("compliance.filter.search")}
        </label>
        <Input
          id="compliance-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("compliance.filter.search_placeholder")}
          data-testid="compliance-search"
          className="mt-1 h-9"
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="compliance-category-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("compliance.filter.category")}
        </label>
        <MultiSelect
          id="compliance-category-filter"
          testId="compliance-category-filter"
          className="w-40"
          label={t("compliance.filter.category")}
          options={CATEGORY_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`license_category.${opt}`),
          }))}
          selected={categories}
          onChange={(next) =>
            onCategoriesChange(next as LicenseCategoryName[])
          }
        />
      </div>

      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">
          {t("compliance.filter.has_obligations")}
        </span>
        <div className="mt-1 flex h-9 items-center">
          <Switch
            checked={hasObligations === true}
            onCheckedChange={(checked) =>
              onHasObligationsChange(checked ? true : null)
            }
            aria-label={t("compliance.filter.has_obligations")}
            data-testid="compliance-has-obligations"
          />
        </div>
      </div>

      {conflictEnabled ? (
        <div className="flex flex-col">
          <label
            htmlFor="compliance-conflict"
            className="text-xs font-medium text-muted-foreground"
          >
            {t("licenses.toolbar.filter_conflict")}
          </label>
          <select
            id="compliance-conflict"
            value={conflict ?? ""}
            onChange={(event) =>
              onConflictChange(
                event.target.value
                  ? (event.target.value as ConflictVerdictName)
                  : undefined,
              )
            }
            className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="compliance-conflict-filter"
          >
            <option value="">{t("licenses.toolbar.conflict.all")}</option>
            {CONFLICT_VERDICT_VALUES.map((verdict) => (
              <option key={verdict} value={verdict}>
                {t(`licenses.conflict.verdict.${verdict}`)}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="flex flex-col">
        <label
          htmlFor="compliance-sort"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("compliance.filter.sort_label")}
        </label>
        <select
          id="compliance-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as ComplianceSortKey)
          }
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="compliance-sort"
        >
          {SORT_OPTIONS.map((key) => (
            <option key={key} value={key}>
              {t(`compliance.filter.sort.${key}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="compliance-order"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("compliance.filter.order_label")}
        </label>
        <select
          id="compliance-order"
          value={order}
          onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="compliance-order"
        >
          <option value="asc">{t("compliance.filter.order_asc")}</option>
          <option value="desc">{t("compliance.filter.order_desc")}</option>
        </select>
      </div>

      <ComplianceNoticeDownload
        projectId={projectId}
        projectName={projectName}
        scanId={scanId}
      />
    </div>
  );
}

/**
 * M-21 — compact NOTICE download group on the Compliance toolbar.
 *
 * The admin/user guides promise a NOTICE download on the Compliance tab, but
 * the W9-#58 unified grid dropped the old obligations sub-view affordance —
 * only the Reports tab card survived. This restores it in toolbar form, on
 * the same `useNotice` imperative-download hook + text/html format pair as
 * `ReportsTab.NoticeCard` (which stays untouched).
 */
function ComplianceNoticeDownload({
  projectId,
  projectName,
  scanId,
}: {
  projectId: string;
  projectName: string | null;
  scanId?: string;
}) {
  const { t } = useTranslation("project_detail");
  const notice = useNotice(projectId, projectName ?? undefined, {
    defaultFormat: "text",
    scanId,
  });
  const [format, setFormat] = useState<NoticeFormat>("text");
  return (
    <div className="flex flex-col" data-testid="compliance-notice-download">
      <label
        htmlFor="compliance-notice-format"
        className="text-xs font-medium text-muted-foreground"
      >
        {t("compliance.notice_download.format_label")}
      </label>
      <div className="mt-1 flex h-9 items-center gap-2">
        <select
          id="compliance-notice-format"
          value={format}
          onChange={(event) => setFormat(event.target.value as NoticeFormat)}
          disabled={notice.isLoading}
          data-testid="compliance-notice-format"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {NOTICE_DOWNLOAD_FORMATS.map((fmt) => (
            <option key={fmt} value={fmt}>
              {t(`compliance.notice_download.format_${fmt}`)}
            </option>
          ))}
        </select>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            // The hook surfaces failures via `notice.error`; swallow the
            // reject so the click doesn't become an unhandled promise.
            notice.download({ format }).catch(() => {});
          }}
          disabled={notice.isLoading}
          data-testid="compliance-notice-action"
        >
          {notice.isLoading
            ? t("compliance.notice_download.action_generating")
            : t("compliance.notice_download.action")}
        </Button>
      </div>
      {notice.error ? (
        <p
          className="mt-1 text-xs text-destructive"
          data-testid="compliance-notice-error"
        >
          {notice.error.message}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header + row
// ---------------------------------------------------------------------------

function ComplianceTableHeader({ showConflict }: { showConflict: boolean }) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="compliance-header"
      role="row"
      aria-rowindex={1}
    >
      <span className="w-44" role="columnheader">
        {t("compliance.column.license")}
      </span>
      <span className="w-32" role="columnheader">
        {t("compliance.column.category")}
      </span>
      {showConflict ? (
        <span className="w-32" role="columnheader">
          {t("licenses.column.conflict")}
        </span>
      ) : null}
      <span className="flex-1" role="columnheader">
        {t("compliance.column.affected")}
      </span>
      <span className="w-64" role="columnheader">
        {t("compliance.column.obligations")}
      </span>
      <span className="w-28 text-center" role="columnheader">
        {t("compliance.column.notice_required")}
      </span>
      <span className="w-32" role="columnheader">
        {t("compliance.column.override_source")}
      </span>
    </div>
  );
}

interface ComplianceGridRowProps {
  row: ComplianceRowItem;
  rowIndex: number;
  /** Only rendered when the project declares an outbound license. */
  showConflict: boolean;
  onSelect: () => void;
  projectId: string;
  teamId: string | null;
  projectRole: TeamScopedRole;
  readOnly: boolean;
  policy: LicensePolicyOut | null;
}

const AFFECTED_PREVIEW_CAP = 3;
const OBLIGATIONS_PREVIEW_CAP = 3;

/**
 * Exported under a test-only name so `tests/unit/design/gridRowSemantics`
 * can run axe over the real row without mounting the whole tab. The row's
 * table semantics are a contract with the grid around it, and a contract
 * nothing checks is one this file already broke once.
 */
export { ComplianceGridRow as ComplianceGridRowForTest };

function ComplianceGridRow({
  row,
  rowIndex,
  showConflict,
  onSelect,
  projectId,
  teamId,
  projectRole,
  readOnly,
  policy,
}: ComplianceGridRowProps) {
  const { t } = useTranslation("project_detail");

  const affectedPreview = row.affected_components.slice(
    0,
    AFFECTED_PREVIEW_CAP,
  );
  const extraAffected = Math.max(
    0,
    row.affected_component_count - affectedPreview.length,
  );

  const obligationsPreview = row.obligations.slice(0, OBLIGATIONS_PREVIEW_CAP);
  const extraObligations = Math.max(
    0,
    row.obligations.length - obligationsPreview.length,
  );

  // A forbidden license is what actually fails the build gate, so the per-
  // component waive affordance only surfaces for forbidden rows. Conditional /
  // allowed / unknown rows have no gate-blocking semantics to override here.
  const isWaivable = row.category === "forbidden";
  // Only components carrying a purl can be scoped by the exception API.
  const waivableComponents = isWaivable
    ? row.affected_components.filter((c) => c.purl != null)
    : [];

  // The row is a non-button container so the per-component waive controls
  // (which are themselves buttons) can live inside without nesting <button>s.
  //
  // The drawer-open affordance used to be one button wrapping every read-only
  // column. It could not stay once the row became a real `row`: a row owns
  // cells, and a button spanning six of them is not one — axe reports it as a
  // critical `aria-required-children` violation and a screen reader in table
  // mode cannot move across the row. The button now sits inside the first
  // cell, the same shape the vulnerabilities row uses, and the row keeps its
  // own click handler for the pointer path.
  return (
    <div
      data-testid="compliance-row"
      data-conflict={row.conflict?.verdict ?? ""}
      data-finding-id={row.license_finding_id}
      data-spdx-id={row.spdx_id ?? ""}
      data-category={row.category}
      data-has-obligations={row.obligations.length > 0}
      data-notice-required={row.notice_required}
      data-row-index={rowIndex}
      data-waivable={isWaivable ? "true" : undefined}
      role="row"
      // +2: the header occupies row 1, and aria-rowindex is 1-based.
      aria-rowindex={rowIndex + 2}
      className={cn(
        "flex w-full flex-col border-b transition-colors duration-fast ease-out-soft hover:bg-muted/50",
      )}
    >
      <div
        onClick={onSelect}
        className={cn("flex w-full items-center gap-3 px-4 text-left text-sm")}
        style={{ height: "var(--table-row)" }}
      >
        <span
          className="flex w-44 flex-col truncate"
          role="cell"
          title={row.spdx_id ?? row.license_name}
        >
          <button
            type="button"
            data-testid="compliance-row-open"
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="truncate rounded-sm text-left font-mono text-xs hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
          >
            {row.spdx_id ?? t("compliance.row.no_spdx_id")}
          </button>
          <span className="truncate text-xs text-muted-foreground">
            {row.license_name}
          </span>
        </span>
        <span className="w-32" role="cell">
          <LicenseCategoryBadge category={row.category} />
        </span>
        {showConflict ? (
          <span className="w-32" role="cell">
            {row.conflict ? (
              <ConflictVerdictBadge
                verdict={row.conflict.verdict}
                why={row.conflict.why}
              />
            ) : null}
          </span>
        ) : null}
        <span
          className="flex flex-1 items-center gap-1 overflow-hidden"
          role="cell"
          data-testid="compliance-row-affected"
        >
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {row.affected_component_count}
          </span>
          <span className="flex flex-1 items-center gap-1 overflow-hidden">
            {affectedPreview.map((c) => (
              <Badge
                key={c.component_version_id}
                tone="info"
                className="max-w-[10rem] truncate"
                title={`${c.name}@${c.version}`}
              >
                <span className="truncate">{`${c.name}@${c.version}`}</span>
              </Badge>
            ))}
            {extraAffected > 0 ? (
              <span
                // G0-5 — `shrink-0 whitespace-nowrap`. This is the last item in
                // a flex row whose badges take the space first, so it was
                // squeezed to a few pixels and its own text wrapped: "+10개 더"
                // rendered as three stacked lines. It is a fixed, short count —
                // the thing in the row that must never be the one that gives.
                className="shrink-0 whitespace-nowrap text-xs text-muted-foreground"
                data-testid="compliance-row-affected-more"
              >
                {t("compliance.affected.more_count", { count: extraAffected })}
              </span>
            ) : null}
          </span>
        </span>
        <span
          className="flex w-64 items-center gap-1 overflow-hidden"
          role="cell"
          data-testid="compliance-row-obligations"
        >
          {obligationsPreview.length === 0 ? (
            <span className="text-xs text-muted-foreground">
              {t("compliance.obligations.none")}
            </span>
          ) : (
            obligationsPreview.map((ob) => (
              <ObligationChip key={ob.obligation_id} obligation={ob} />
            ))
          )}
          {extraObligations > 0 ? (
            <span
              // G0-5 — same as the affected count above: the overflow marker
              // sits behind chips that claim the width first, in a fixed w-64
              // cell that has even less of it to give.
              className="shrink-0 whitespace-nowrap text-xs text-muted-foreground"
              data-testid="compliance-row-obligations-more"
            >
              {t("compliance.obligations.more_count", {
                count: extraObligations,
              })}
            </span>
          ) : null}
        </span>
        <span
          className="w-28 text-center text-xs"
          role="cell"
          data-testid="compliance-row-notice"
        >
          {row.notice_required ? (
            <Badge tone="medium">{t("compliance.notice.required")}</Badge>
          ) : (
            <span className="text-muted-foreground">
              {t("compliance.notice.not_required")}
            </span>
          )}
        </span>
        <span
          className="w-32 text-xs text-muted-foreground"
          role="cell"
          data-testid="compliance-row-override"
        >
          {row.category_override_source ?? t("compliance.override.none")}
        </span>
      </div>

      {waivableComponents.length > 0 ? (
        // A second visual line, but structurally still part of this row, so it
        // is a cell. Without the role it would be an unowned child of a `row`,
        // which is the same violation the wrapping button caused.
        <div
          className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pb-2 pl-[12.25rem] text-xs"
          role="cell"
          data-testid="compliance-row-waive-strip"
        >
          <span className="text-muted-foreground">
            {t("waive.strip_label")}
          </span>
          {waivableComponents.map((c) => {
            const existing: LicenseException | null = findComponentException(
              policy,
              row.spdx_id,
              c.purl,
            );
            return (
              <span
                key={c.component_version_id}
                className="inline-flex items-center gap-1.5"
                data-testid="compliance-waive-component"
                data-component-purl={c.purl ?? ""}
              >
                <span
                  className="max-w-[12rem] truncate font-mono text-muted-foreground"
                  title={`${c.name}@${c.version}`}
                >{`${c.name}@${c.version}`}</span>
                <LicenseWaiveAction
                  projectId={projectId}
                  teamId={teamId}
                  projectRole={projectRole}
                  spdxId={row.spdx_id}
                  componentLabel={`${c.name}@${c.version}`}
                  componentPurl={c.purl}
                  existing={existing}
                  // The strip only renders for forbidden rows, so a waiver here
                  // always relaxes the build gate → a capped expiry is required.
                  requireExpiry={row.category === "forbidden"}
                  readOnly={readOnly}
                />
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

interface ObligationChipProps {
  obligation: ComplianceObligation;
}

function ObligationChip({ obligation }: ObligationChipProps) {
  const { t, i18n } = useTranslation("project_detail");
  const { pick } = useAdvisoryTranslation();
  // Re-use the obligations.kind.* dictionary the old ObligationsTab seeded.
  // For unknown kinds the catalog can emit anything (free-form), so fall
  // back to the raw kind verbatim.
  const dictKey = `obligations.kind.${obligation.kind}`;
  const label = i18n.exists(dictKey, { ns: "project_detail" })
    ? t(dictKey)
    : obligation.kind;
  // C1a — the hover summary follows the reader's language (advisory KO with
  // an English fallback); the drawer carries the English original.
  const summary = pick(obligation.summary, obligation.summary_ko);
  return (
    <Badge
      tone="info"
      className="max-w-[8rem] truncate"
      title={summary.text}
      data-testid="compliance-obligation-chip"
      data-kind={obligation.kind}
    >
      <span className="truncate">{label}</span>
    </Badge>
  );
}
