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
 *   - ``?compliance_sort=category|license_name|spdx_id|affected_count``
 *   - ``?compliance_order=asc|desc``     order toggle
 *   - ``?compliance_page=N``             1-based page index
 *   - ``?license=<finding_id>``          drawer selection (shared with LicensesTab)
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
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import type {
  LicenseCategoryName,
  TeamScopedRole,
} from "@/features/projects/api/projectDetailApi";
import type {
  ComplianceObligation,
  ComplianceRow as ComplianceRowItem,
  ComplianceSortKey,
  SortOrder,
} from "@/features/projects/api/complianceApi";
import { useCompliance } from "@/features/projects/api/useCompliance";
import {
  findComponentException,
  useTeamLicensePolicy,
  type LicenseException,
} from "@/features/projects/api/useLicenseWaive";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { LicenseDrawer } from "@/features/projects/components/LicenseDrawer";
import { LicenseWaiveAction } from "@/features/projects/components/LicenseWaiveAction";
import type { LicensePolicyOut } from "@/lib/licensePoliciesApi";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;

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

function parsePage(raw: string | null): number {
  const n = raw ? Number.parseInt(raw, 10) : 1;
  if (!Number.isFinite(n) || n < 1) return 1;
  return n;
}

function parseHasObligations(raw: string | null): boolean | null {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return null;
}

export interface ComplianceTabProps {
  projectId: string;
  /**
   * Project name — accepted for API parity with the old wrapper signature
   * (the obligations sub-view used it to name the NOTICE download). The
   * unified grid no longer owns NOTICE download itself (that affordance
   * lives in the LicenseDrawer / Reports tab), so this prop is currently
   * unused. Kept for source-compat with ProjectDetailPage.
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
}

export function ComplianceTab({
  projectId,
  scanId,
  teamId = null,
  projectRole = "developer",
  readOnly = false,
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
  const [sort, setSort] = useState<ComplianceSortKey>(() =>
    parseSort(searchParams.get("compliance_sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("compliance_order")),
  );
  const [page, setPage] = useState<number>(() =>
    parsePage(searchParams.get("compliance_page")),
  );

  // Drawer selection. ``?license=<finding_id>`` is shared with LicensesTab so
  // a deep-link from a chart or a recent-finding card still works.
  const drawerId = searchParams.get("license");
  const drawerOpen = drawerId != null && drawerId.length > 0;

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
      { replace: true },
    );
  }

  // Debounce the search input → 300ms before a network call.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(search);
      // A new search resets pagination — otherwise the user could be stuck
      // on page 5 of a now-tiny result set.
      setPage(1);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

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
        if (sort !== "category") next.set("compliance_sort", sort);
        else next.delete("compliance_sort");
        if (order !== "desc") next.set("compliance_order", order);
        else next.delete("compliance_order");
        if (page !== 1) next.set("compliance_page", String(page));
        else next.delete("compliance_page");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    categories,
    hasObligations,
    sort,
    order,
    page,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      categories,
      kinds: [],
      hasObligations,
      sort,
      order,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
      scanId,
    }),
    [debouncedSearch, categories, hasObligations, sort, order, page, scanId],
  );

  const compliance = useCompliance(projectId, filters);

  // Effective team policy carries the per-component waivers. A 404 ("no team
  // policy, static fallback") resolves to null — not an error — so the grid
  // still renders waive affordances. Only fetched when we know the team.
  const teamPolicy = useTeamLicensePolicy(teamId);
  const policy = teamPolicy.data ?? null;

  const items: ComplianceRowItem[] = compliance.data?.items ?? [];
  const total = compliance.data?.total ?? 0;

  return (
    <div data-testid="compliance-tab" className="flex flex-1 flex-col">
      <ComplianceToolbar
        search={search}
        onSearchChange={setSearch}
        categories={categories}
        onCategoriesChange={(next) => {
          setCategories(next);
          setPage(1);
        }}
        hasObligations={hasObligations}
        onHasObligationsChange={(next) => {
          setHasObligations(next);
          setPage(1);
        }}
        sort={sort}
        onSortChange={(next) => {
          setSort(next);
          setPage(1);
        }}
        order={order}
        onOrderChange={(next) => {
          setOrder(next);
          setPage(1);
        }}
      />

      <div
        className="flex items-center justify-between border-b px-4 py-2 text-xs text-muted-foreground"
        data-testid="compliance-summary"
        data-total={total}
        data-loaded={items.length}
      >
        <span>
          {t("compliance.summary", {
            loaded: items.length,
            total,
          })}
        </span>
      </div>

      {compliance.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="compliance-error">
            <AlertDescription>
              {compliance.error instanceof ProblemError
                ? compliance.error.detail
                : t("compliance.errors.load_list")}
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
          title={t("compliance.empty.title")}
          description={t("compliance.empty.description")}
        />
      ) : null}

      {!compliance.isLoading && !compliance.isError && items.length > 0 ? (
        <>
          <ComplianceTableHeader />
          <div
            className="flex-1"
            data-testid="compliance-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              data={items}
              style={{
                height: "calc(100vh - var(--layout-header) - 240px)",
              }}
              itemContent={(index, item) => (
                <ComplianceGridRow
                  row={item}
                  rowIndex={index}
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
        </>
      ) : null}

      <LicenseDrawer
        open={drawerOpen}
        findingId={drawerId}
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
  search: string;
  onSearchChange: (value: string) => void;
  categories: LicenseCategoryName[];
  onCategoriesChange: (value: LicenseCategoryName[]) => void;
  hasObligations: boolean | null;
  onHasObligationsChange: (value: boolean | null) => void;
  sort: ComplianceSortKey;
  onSortChange: (value: ComplianceSortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
}

function ComplianceToolbar({
  search,
  onSearchChange,
  categories,
  onCategoriesChange,
  hasObligations,
  onHasObligationsChange,
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header + row
// ---------------------------------------------------------------------------

function ComplianceTableHeader() {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="compliance-header"
    >
      <span className="w-44">{t("compliance.column.license")}</span>
      <span className="w-32">{t("compliance.column.category")}</span>
      <span className="flex-1">{t("compliance.column.affected")}</span>
      <span className="w-64">{t("compliance.column.obligations")}</span>
      <span className="w-28 text-center">
        {t("compliance.column.notice_required")}
      </span>
      <span className="w-32">{t("compliance.column.override_source")}</span>
    </div>
  );
}

interface ComplianceGridRowProps {
  row: ComplianceRowItem;
  rowIndex: number;
  onSelect: () => void;
  projectId: string;
  teamId: string | null;
  projectRole: TeamScopedRole;
  readOnly: boolean;
  policy: LicensePolicyOut | null;
}

const AFFECTED_PREVIEW_CAP = 3;
const OBLIGATIONS_PREVIEW_CAP = 3;

function ComplianceGridRow({
  row,
  rowIndex,
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
  // The drawer-open affordance is a single inner button covering the read-only
  // columns; the waive strip below sits outside it.
  return (
    <div
      data-testid="compliance-row"
      data-finding-id={row.license_finding_id}
      data-spdx-id={row.spdx_id ?? ""}
      data-category={row.category}
      data-has-obligations={row.obligations.length > 0}
      data-notice-required={row.notice_required}
      data-row-index={rowIndex}
      data-waivable={isWaivable ? "true" : undefined}
      className={cn(
        "flex w-full flex-col border-b transition-colors duration-fast ease-out-soft hover:bg-muted/50",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        data-testid="compliance-row-open"
        className={cn(
          "flex w-full items-center gap-3 px-4 text-left text-sm",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        )}
        style={{ height: "var(--table-row)" }}
      >
        <span
          className="flex w-44 flex-col truncate"
          title={row.spdx_id ?? row.license_name}
        >
          <span className="truncate font-mono text-xs">
            {row.spdx_id ?? t("compliance.row.no_spdx_id")}
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {row.license_name}
          </span>
        </span>
        <span className="w-32">
          <LicenseCategoryBadge category={row.category} />
        </span>
        <span
          className="flex flex-1 items-center gap-1 overflow-hidden"
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
                className="text-xs text-muted-foreground"
                data-testid="compliance-row-affected-more"
              >
                {t("compliance.affected.more_count", { count: extraAffected })}
              </span>
            ) : null}
          </span>
        </span>
        <span
          className="flex w-64 items-center gap-1 overflow-hidden"
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
              className="text-xs text-muted-foreground"
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
          data-testid="compliance-row-override"
        >
          {row.category_override_source ?? t("compliance.override.none")}
        </span>
      </button>

      {waivableComponents.length > 0 ? (
        <div
          className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pb-2 pl-[12.25rem] text-xs"
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
  // Re-use the obligations.kind.* dictionary the old ObligationsTab seeded.
  // For unknown kinds the catalog can emit anything (free-form), so fall
  // back to the raw kind verbatim.
  const dictKey = `obligations.kind.${obligation.kind}`;
  const label = i18n.exists(dictKey, { ns: "project_detail" })
    ? t(dictKey)
    : obligation.kind;
  return (
    <Badge
      tone="info"
      className="max-w-[8rem] truncate"
      title={obligation.summary}
      data-testid="compliance-obligation-chip"
      data-kind={obligation.kind}
    >
      <span className="truncate">{label}</span>
    </Badge>
  );
}
