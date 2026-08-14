// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { PackageCheck, ShieldCheck } from "lucide-react";
import {
  type ComponentPropsWithoutRef,
  forwardRef,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ariaSortFor,
  type SortState,
  SortableColumnHeader,
} from "@/components/ui/sortable-column-header";
import {
  type ColumnsPickerColumn,
  loadInitialVisibility,
} from "@/components/filters/ColumnsPicker";
import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { useUpgradeClusters } from "@/features/projects/api/useUpgradeClusters";
import { useVulnerabilities } from "@/features/projects/api/useVulnerabilities";
import type {
  ReachabilityFilter,
  SlaFilter,
  SlaStatus,
  SortOrder,
  UpgradeCluster,
  VulnFindingStatus,
  VulnSeverity,
  VulnerabilityListItem,
  VulnerabilitySortKey,
} from "@/features/projects/api/vulnerabilitiesApi";
import { SLA_STATUS_VALUES } from "@/features/projects/api/vulnerabilitiesApi";
import { BULK_TRANSITION_MAX } from "@/features/projects/api/vulnerabilitiesApi";
import { ActiveFilterChips } from "@/features/projects/components/ActiveFilterChips";
import { AxisPill } from "@/features/projects/components/AxisPill";
import { KevBadge } from "@/features/projects/components/KevBadge";
import { ReachabilityBadge } from "@/features/projects/components/ReachabilityBadge";
import { SlaBadge } from "@/features/projects/components/SlaBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
import { VulnerabilitiesRemediationPanel } from "@/features/projects/components/VulnerabilitiesRemediationPanel";
import { UpgradeClusterList } from "@/features/projects/components/UpgradeClusterList";
import {
  VulnerabilitiesToolbar,
  type VulnerabilitiesExtraFilter,
  type VulnerabilitiesGroupByMode,
} from "@/features/projects/components/VulnerabilitiesToolbar";
import { VulnerabilityBulkActionBar } from "@/features/projects/components/VulnerabilityBulkActionBar";
import { VulnerabilityDrawer } from "@/features/projects/components/VulnerabilityDrawer";
import { VulnerabilityStatusBadge } from "@/features/projects/components/VulnerabilityStatusBadge";
import {
  EPSS_EMPTY,
  epssPercentileLabel,
  formatEpssScore,
} from "@/features/projects/lib/epss";
import {
  ALL_VULNERABILITY_STATUSES,
  type TriageRole,
} from "@/features/projects/lib/vulnerabilityTransitions";
import {
  VULNERABILITIES_SEARCH_PARAM,
  readTabSearchParam,
  writeTabSearchParam,
} from "@/features/projects/components/tabSearchParam";
import { problemMessage } from "@/lib/problemMessage";
import RelativeTime from "@/components/RelativeTime";
import { toggleSingleValue } from "@/lib/searchParamsToggle";
import { cn } from "@/lib/utils";

/**
 * VulnerabilitiesTab — Phase 3 PR #11.
 *
 * Virtualized vulnerability findings table + drawer for the project detail
 * page. Mirrors the structure of `ComponentsTab`:
 *
 *   - `useVulnerabilities` is an infinite offset query keyed on the entire
 *     filter tuple. Filter or sort changes naturally invalidate the cached
 *     pages and refetch from offset 0.
 *   - Search input is debounced 300ms before it hits the query.
 *   - Filters, sort, and the selected drawer finding id are mirrored into
 *     URL search params (deep-link + reload survival). The drawer key is
 *     `?vuln=<finding_id>` so it doesn't collide with ComponentsTab's
 *     `?drawer=<component_id>` (PR #10).
 *   - Virtuoso renders fixed 40px rows (CLAUDE.md compact density).
 *
 * Pagination is offset/limit rather than cursor, and infinite rather than
 * paged. It was a single page for a while, because PATCH writes a full detail
 * payload back into the cache and one page needed no reconciliation. The
 * result was a table whose 101st row could not be reached by any means the UI
 * offered: `?page=` was written but never incremented, and no control existed
 * to change it.
 */

const PAGE_SIZE = 100;

/**
 * The table body, placed on the one element between the table and its rows
 * that the accessibility tree will not flatten.
 *
 * Everything Virtuoso renders between the two is a plain `div` — wrapper,
 * scroller, viewport, list, per-item box — and a plain `div` disappears from
 * the accessibility tree, so the rows read as owned by the table. All except
 * the scroller: it carries `tabindex` so the list can be scrolled from the
 * keyboard, and a focusable element is never flattened. It has to hold a role
 * the table allows, so it holds `rowgroup`.
 *
 * Two things were tried first and are worth not re-trying. `role="presentation"`
 * on the wrappers: axe rejects it, correctly — a presentational element is
 * still an owned child, and `table` owns rows and rowgroups, not presentation.
 * `rowgroup` on the wrapper div outside Virtuoso: the focusable scroller then
 * sits INSIDE the rowgroup and is reported there instead.
 *
 * Module scope, not inline: a `components` object rebuilt each render makes
 * Virtuoso remount its scroller, losing scroll position and re-measuring.
 * `role` goes after the spread so Virtuoso's own props cannot overwrite it.
 */
const VIRTUOSO_TABLE_BODY = {
  Scroller: forwardRef<HTMLDivElement, ComponentPropsWithoutRef<"div">>(
    function VirtuosoScroller(props, ref) {
      return <div ref={ref} {...props} role="rowgroup" />;
    },
  ),
};


/**
 * W9 #52 — column-picker catalog for the Vulnerabilities table. `cve_id` and
 * `severity` are required because they identify the row + carry the primary
 * triage signal; users would lose the table's information density if either
 * were hidden. The other columns are user-toggleable and persisted under
 * `VULN_COLUMNS_STORAGE_KEY`.
 *
 * The label string keys map to the existing `vulnerabilities.column.*` i18n
 * entries — no new translation work required for the column names themselves.
 */
const VULN_COLUMNS_STORAGE_KEY = "column-visibility:vulnerabilities";

function getVulnColumnsCatalog(
  t: (key: string) => string,
): ColumnsPickerColumn[] {
  return [
    { id: "cve_id", label: t("vulnerabilities.column.cve_id"), required: true },
    // M-27 — Title (source: `summary`) + Discovered (source: `discovered_at`)
    // are toggleable columns. `loadInitialVisibility` semantics apply: fresh
    // users see them (no stored set → all visible); users with a persisted
    // visibility set keep their stored selection (the new ids are absent from
    // the stored array → hidden until toggled on via the ColumnsPicker).
    { id: "title", label: t("vulnerabilities.column.title") },
    { id: "component", label: t("vulnerabilities.column.component") },
    {
      id: "severity",
      label: t("vulnerabilities.column.severity"),
      required: true,
    },
    { id: "cvss", label: t("vulnerabilities.column.cvss") },
    { id: "epss", label: t("vulnerabilities.column.epss") },
    { id: "reachable", label: t("vulnerabilities.column.reachable") },
    // X1 SLA/aging — due date + server-computed state badge. Default-visible
    // for fresh users (loadInitialVisibility: no stored set → all visible);
    // users with a persisted visibility set see it after toggling it on.
    { id: "sla_due", label: t("vulnerabilities.column.sla_due") },
    { id: "status", label: t("vulnerabilities.column.status") },
    { id: "discovered", label: t("vulnerabilities.column.discovered") },
  ];
}

const VALID_SEVERITY = new Set<VulnSeverity>([
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "unknown",
]);

const VALID_STATUS = new Set<VulnFindingStatus>(ALL_VULNERABILITY_STATUSES);

const VALID_SORT = new Set<VulnerabilitySortKey>([
  "priority",
  "severity",
  "cvss",
  "status",
  "discovered_at",
  "epss",
  "reachable",
  "sla_due",
]);

/**
 * KEV feature — the DEFAULT sort is the composite `priority` ranking
 * (KEV → severity → EPSS). It replaces the previous "severity desc" default;
 * `parseSort` still falls back here for absent / invalid URL values, so a
 * hand-edited `?sort=` can never wedge the list.
 */
const DEFAULT_SORT: VulnerabilitySortKey = "priority";

const VALID_REACHABLE = new Set<ReachabilityFilter>([
  "true",
  "false",
  "unknown",
]);

const VALID_LICENSE = new Set<LicenseCategoryName>([
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
]);

function parseList<T extends string>(
  raw: string | null,
  valid: Set<T>,
): T[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter((v): v is T => valid.has(v as T));
}

function parseSort(raw: string | null): VulnerabilitySortKey {
  if (raw && VALID_SORT.has(raw as VulnerabilitySortKey)) {
    return raw as VulnerabilitySortKey;
  }
  return DEFAULT_SORT;
}

function parseOrder(raw: string | null): SortOrder {
  return raw === "asc" ? "asc" : "desc";
}

/**
 * Parse the `min_epss` URL param into a [0, 1] threshold, or `null` for "no
 * threshold". Out-of-range / non-numeric values fall back to null so a hand
 * edited URL can't wedge the filter.
 */
function parseMinEpss(raw: string | null): number | null {
  if (raw == null || raw.length === 0) return null;
  const n = Number.parseFloat(raw);
  if (!Number.isFinite(n) || n < 0 || n > 1) return null;
  return n;
}

/**
 * Parse the `reachable` URL param into one of the three legal tokens, or `null`
 * for "no filter". A hand-edited URL with anything else (or the empty string)
 * falls back to null so the filter can't get wedged into an invalid value the
 * backend would 422 on.
 */
function parseReachable(raw: string | null): ReachabilityFilter | null {
  if (raw && VALID_REACHABLE.has(raw as ReachabilityFilter)) {
    return raw as ReachabilityFilter;
  }
  return null;
}

const VALID_SLA = new Set<SlaFilter>(SLA_STATUS_VALUES);

/**
 * Parse the `sla` URL param into one of the three legal tokens, or `null`
 * for "no filter" — same defensive narrowing as `parseReachable` so a
 * hand-edited URL can't wedge the filter into a value the backend 422s on.
 */
function parseSla(raw: string | null): SlaFilter | null {
  if (raw && VALID_SLA.has(raw as SlaFilter)) {
    return raw as SlaFilter;
  }
  return null;
}

export interface VulnerabilitiesTabProps {
  projectId: string;
  /** Used to build the PDF report download filename fallback (G2). */
  projectName?: string | null;
  /**
   * Pinned snapshot scan id (feature #28). When set, the list reflects that
   * historical scan instead of the latest succeeded one. Omit → latest.
   */
  scanId?: string;
  /**
   * Historical (read-only) snapshot mode (feature #28). When `true`, all
   * write controls that mutate the *current* findings — VEX import and the
   * per-finding status transition — are disabled with a tooltip; editing an
   * old snapshot's findings would be wrong. Read paths are unaffected.
   */
  readOnly?: boolean;
  /**
   * Starts a scan from the empty state. Omitted when the caller cannot scan
   * (no permission, read-only demo, historical snapshot), and the empty state
   * then explains without offering a button that would fail.
   */
  onScan?: () => void;
}

export function VulnerabilitiesTab({
  projectId,
  projectName,
  scanId,
  readOnly = false,
  onScan,
}: VulnerabilitiesTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  // W9-#53 — group-by mode. Local UI state (NOT URL-persisted): switching
  // tabs unmounts the tab and resets this to the default flat list. "upgrade"
  // swaps the paginated findings table for whole-project upgrade clusters.
  const [groupBy, setGroupBy] = useState<VulnerabilitiesGroupByMode>("flat");

  // ----- filter state, hydrated from URL on first render -------------------
  const [search, setSearch] = useState(() =>
    readTabSearchParam(searchParams, VULNERABILITIES_SEARCH_PARAM),
  );
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [severity, setSeverity] = useState<VulnSeverity[]>(() =>
    parseList<VulnSeverity>(searchParams.get("severity"), VALID_SEVERITY),
  );
  const [status, setStatus] = useState<VulnFindingStatus[]>(() =>
    parseList<VulnFindingStatus>(searchParams.get("status"), VALID_STATUS),
  );
  const [sort, setSort] = useState<VulnerabilitySortKey>(() =>
    parseSort(searchParams.get("sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("order")),
  );
  const [minEpss, setMinEpss] = useState<number | null>(() =>
    parseMinEpss(searchParams.get("min_epss")),
  );
  // v2.3 r2 — tri-state reachability filter. URL flag `reachable=true|false|unknown`.
  const [reachable, setReachable] = useState<ReachabilityFilter | null>(() =>
    parseReachable(searchParams.get("reachable")),
  );
  // X1 — SLA status filter. URL flag `sla=overdue|imminent|ok` (single value,
  // same inline-select pattern as `reachable`).
  const [sla, setSla] = useState<SlaFilter | null>(() =>
    parseSla(searchParams.get("sla")),
  );
  // W2 #33 — License-risk multi-select. Mirrors the Components tab's pattern:
  // CSV-encoded in `?license_category=`, the same `parseList` helper, and the
  // same four `LicenseCategoryName` tokens so a triager can pivot between
  // tabs without re-learning the facet.
  const [licenseCategory, setLicenseCategory] = useState<LicenseCategoryName[]>(
    () =>
      parseList<LicenseCategoryName>(
        searchParams.get("license_category"),
        VALID_LICENSE,
      ),
  );
  // v2.1 A3 — "suppressed via VEX" inline filter. URL flag `vex_suppressed=1`.
  // The backend has no `analysis_source` query param yet, so we narrow the
  // current page client-side (sufficient for the triage workflow: a reviewer
  // wants to eyeball what a just-uploaded VEX document changed on this page).
  const [vexSuppressedOnly, setVexSuppressedOnly] = useState<boolean>(
    () => searchParams.get("vex_suppressed") === "1",
  );

  // W9 #52 — "+ Add filter" mount-on-demand facets. We seed the set from the
  // current URL state so a deep-linked `?severity=` / `?license_category=`
  // auto-mounts the matching MultiSelect (the user already has a non-empty
  // selection — the facet should be visible, not hidden).
  const [mountedExtraFilters, setMountedExtraFilters] = useState<
    Set<VulnerabilitiesExtraFilter>
  >(() => {
    const next = new Set<VulnerabilitiesExtraFilter>();
    const sevParam = searchParams.get("severity");
    if (sevParam && sevParam.length > 0) next.add("severity");
    const licParam = searchParams.get("license_category");
    if (licParam && licParam.length > 0) next.add("license_category");
    return next;
  });
  const mountExtraFilter = (filter: VulnerabilitiesExtraFilter) => {
    setMountedExtraFilters((prev) => {
      if (prev.has(filter)) return prev;
      const next = new Set(prev);
      next.add(filter);
      return next;
    });
  };
  const unmountExtraFilter = (filter: VulnerabilitiesExtraFilter) => {
    setMountedExtraFilters((prev) => {
      if (!prev.has(filter)) return prev;
      const next = new Set(prev);
      next.delete(filter);
      return next;
    });
  };

  // W9 #52 — column-picker catalog + visibility. Hydrated from localStorage so
  // the user's last preference survives reload; required ids are forced into
  // the set by `loadInitialVisibility`.
  const columnsCatalog = useMemo(
    () => getVulnColumnsCatalog((k) => t(k)),
    [t],
  );
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(() =>
    loadInitialVisibility(VULN_COLUMNS_STORAGE_KEY, columnsCatalog),
  );

  // W2 #33b — bulk-selection state. Single-page only (D-bulk): we clear the
  // set on page / filter / sort / scanId change because the row population
  // shifts under selection. Persisting across pages would require a server-
  // side "all matching" token which is out of scope for this PR.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  function toggleSelection(findingId: string, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        // Hard cap matches the backend's `BULK_TRANSITION_MAX` so the UI
        // can never queue an oversize selection that would 422 on submit.
        if (next.size >= BULK_TRANSITION_MAX) return prev;
        next.add(findingId);
      } else {
        next.delete(findingId);
      }
      return next;
    });
  }
  function clearSelection() {
    setSelectedIds((prev) => (prev.size === 0 ? prev : new Set()));
  }

  /**
   * Whether anything is narrowing the list. Drives which empty state the user
   * gets: filtered-to-nothing, or nothing to show at all.
   */
  const hasNarrowingFilters =
    debouncedSearch.trim().length > 0 ||
    severity.length > 0 ||
    status.length > 0 ||
    minEpss != null ||
    reachable != null ||
    sla != null ||
    licenseCategory.length > 0 ||
    vexSuppressedOnly;

  function clearAllFilters() {
    setSearch("");
    setDebouncedSearch("");
    setSeverity([]);
    setStatus([]);
    setMinEpss(null);
    setReachable(null);
    setSla(null);
    setLicenseCategory([]);
    setVexSuppressedOnly(false);
  }

  // Drawer state — `?vuln=<finding_id>` so reload restores the selection.
  const drawerId = searchParams.get("vuln");
  const drawerOpen = drawerId != null && drawerId.length > 0;

  /**
   * Opening the drawer pushes a history entry, so Back closes it instead of
   * leaving the tab. It used to replace, which meant Back threw away the
   * user's filters and scroll position to get out of a panel they opened by
   * clicking one row. Closing pushes too, matching PoliciesPage: Back then
   * reopens what was just closed, which is what a browser is expected to do.
   */
  function setDrawerVuln(findingId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (findingId) {
          next.set("vuln", findingId);
        } else {
          next.delete("vuln");
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

  // Mirror filter state into URL params for deep-linking + reload-survival.
  // We omit defaults so canonical URLs stay short.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        writeTabSearchParam(next, VULNERABILITIES_SEARCH_PARAM, debouncedSearch);
        if (severity.length) next.set("severity", severity.join(","));
        else next.delete("severity");
        if (status.length) next.set("status", status.join(","));
        else next.delete("status");
        if (sort !== DEFAULT_SORT) next.set("sort", sort);
        else next.delete("sort");
        if (order !== "desc") next.set("order", order);
        else next.delete("order");
        if (minEpss != null) next.set("min_epss", String(minEpss));
        else next.delete("min_epss");
        if (reachable != null) next.set("reachable", reachable);
        else next.delete("reachable");
        if (sla != null) next.set("sla", sla);
        else next.delete("sla");
        // W2 #33 — `?license_category=forbidden,conditional,...`. Empty array
        // drops the key entirely so the default URL stays clean.
        if (licenseCategory.length)
          next.set("license_category", licenseCategory.join(","));
        else next.delete("license_category");
        if (vexSuppressedOnly) next.set("vex_suppressed", "1");
        else next.delete("vex_suppressed");
        // No `page`: the table is infinite now, and the parameter it used to
        // write was never incremented by anything, so it only ever described
        // a position the user could not reach.
        next.delete("page");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    severity,
    status,
    sort,
    order,
    minEpss,
    reachable,
    sla,
    licenseCategory,
    vexSuppressedOnly,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      severity,
      status,
      sort,
      order,
      min_epss: minEpss,
      reachable,
      sla,
      license_category: licenseCategory,
      limit: PAGE_SIZE,
      scanId,
    }),
    [
      debouncedSearch,
      severity,
      status,
      sort,
      order,
      minEpss,
      reachable,
      sla,
      licenseCategory,
      scanId,
    ],
  );

  // W9-#53 — exactly one of {flat list, upgrade clusters} runs at a time.
  // The flat query is gated off in "upgrade" mode and vice-versa, threading
  // the same `scanId` snapshot anchor.
  const vulnerabilities = useVulnerabilities(projectId, filters, {
    enabled: groupBy === "flat",
  });
  const upgradeClusters = useUpgradeClusters(projectId, {
    scanId,
    enabled: groupBy === "upgrade",
  });
  const clusters = upgradeClusters.data?.clusters ?? [];

  // W2 #33b — drop selection when the row set shifts. Refs to the filter
  // tuple change atomically with the query key, so this fires exactly when
  // the table is about to repopulate from a different rowset.
  const severityKey = severity.join(",");
  const statusKey = status.join(",");
  const licenseKey = licenseCategory.join(",");
  useEffect(() => {
    clearSelection();
  }, [
    debouncedSearch,
    severityKey,
    statusKey,
    sort,
    order,
    minEpss,
    reachable,
    sla,
    licenseKey,
    scanId,
  ]);

  // BUG-005: the suppression gate must use the project-team-scoped role, not
  // the global JWT role. The overview query carries `current_user_role`; this
  // shares the `["projects", projectId, "overview"]` key with the page-level
  // fetch so TanStack Query dedupes it (no extra request). Default to the
  // least-privileged `developer` until it resolves.
  const overview = useProjectOverview(projectId);
  const projectRole: TriageRole =
    overview.data?.current_user_role ?? "developer";

  const pages = vulnerabilities.data?.pages;
  const total = pages?.[0]?.total ?? 0;

  const fetchedItems: VulnerabilityListItem[] = useMemo(
    () => (pages ?? []).flatMap((p) => p.items),
    [pages],
  );

  // Client-side narrowing for the "suppressed via VEX" filter. A finding counts
  // as VEX-suppressed when its last status mutation came from a VEX import.
  const items: VulnerabilityListItem[] = useMemo(
    () =>
      vexSuppressedOnly
        ? fetchedItems.filter((it) => it.analysis_source === "vex_import")
        : fetchedItems,
    [fetchedItems, vexSuppressedOnly],
  );

  // What the summary band counts. Without the VEX filter the server's total is
  // the honest number: rows beyond those loaded exist and are reachable by
  // scrolling. With it, the narrowing happens here, over loaded rows only, so
  // the server total would describe a different set than the one on screen —
  // it used to say "1,240" above four visible rows.
  const shownTotal = vexSuppressedOnly ? items.length : total;

  // M-28 — current statuses of the selected rows. Selection is single-page
  // (D-bulk) and cleared on any row-set shift, so every selected id is
  // guaranteed to be present in `items`. The bulk action bar intersects the
  // legal next states across these so a mixed selection can never queue a
  // transition the server would reject per-row.
  const selectedStatuses = useMemo<VulnFindingStatus[]>(
    () =>
      items.filter((it) => selectedIds.has(it.id)).map((it) => it.status),
    [items, selectedIds],
  );

  // W4-B #19 — SortableColumnHeader state derived from the existing
  // `sort` + `order` state. The default surface state is now the composite
  // "priority" ranking (KEV → severity → EPSS), which is NOT a table column,
  // so it maps to the un-sorted header bucket: a column header's cycle
  // returns to the priority default rather than being stuck on asc/desc.
  const currentSort: SortState | null = useMemo(() => {
    if (sort === "priority") return null;
    return { key: sort, order };
  }, [sort, order]);

  function handleSortChange(next: SortState | null) {
    if (!next) {
      setSort(DEFAULT_SORT);
      setOrder("desc");
      return;
    }
    setSort(next.key as VulnerabilitySortKey);
    setOrder(next.order);
  }

  // KEV feature — the toolbar's sort select shares the same `sort` state as
  // the column headers (single source of truth): picking a key here resets
  // the direction to the key's natural "most urgent first". For every key
  // that is desc, EXCEPT `sla_due` (X1): the soonest / most-overdue due date
  // is the smallest value, so urgent-first is asc (backend sorts NULLS LAST
  // in both directions, so no-SLA rows never float to the top).
  function handleSortKeyChange(next: VulnerabilitySortKey) {
    setSort(next);
    setOrder(next === "sla_due" ? "asc" : "desc");
  }

  // Finding-level severity distribution from the vulnerabilities list endpoint
  // (W6 follow-up — Overview's severity_distribution is component-scoped, so
  // the previous "Info=1" segment came from the worst-CVE-severity of one
  // component, not from a finding actually tagged Info, and clicking it
  // produced "0 of 0 findings"). The list query carries this distribution
  // alongside the page items, ignoring the active filters so the card stays
  // stable as the page rows narrow. The `unknown` bucket lands in the chart's
  // `none` slot with a relabeled legend ("Unknown") so we don't fork the
  // chart component for one extra bucket.
  // Every page repeats the same whole-project distribution, so the first one
  // is as good as any and does not shift as more pages load.
  const rawDistribution = pages?.[0]?.severity_distribution ?? {};
  const severityDistribution = {
    critical: rawDistribution.critical ?? 0,
    high: rawDistribution.high ?? 0,
    medium: rawDistribution.medium ?? 0,
    low: rawDistribution.low ?? 0,
    info: rawDistribution.info ?? 0,
    none: rawDistribution.unknown ?? 0,
  };
  const distributionHasAny = Object.values(severityDistribution).some(
    (v) => v > 0,
  );

  return (
    <div data-testid="vulnerabilities-tab" className="flex flex-1 flex-col">
      {groupBy === "flat" && distributionHasAny ? (
        <div
          // W11-C polish — distribution card lands on the canonical px-6 +
          // py-4 gutter shared by toolbar / rows below (Vercel deployments-1
          // axis). Border-bottom softened to /60.
          className="border-b border-border/60 px-6 py-4"
          data-testid="vulnerabilities-distribution-card"
        >
          <Card data-testid="vulnerabilities-severity-card">
            <CardHeader>
              <CardTitle className="flex items-baseline gap-2 text-base">
                <span>{t("overview.severity_card.title")}</span>
                <AxisPill>
                  {t("overview.severity_card.axis_findings")}
                </AxisPill>
              </CardTitle>
              <CardDescription>
                {t("overview.severity_card.subtitle_findings")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <SeverityDistributionChart
                distribution={severityDistribution}
                noneLabel={t("severity.unknown")}
                onSegmentClick={(key) => {
                  // The chart's `none` slot actually carries `unknown` finding
                  // counts (see the mapping above), so translate before
                  // feeding the filter token to the API.
                  // W9-#57 — re-clicking the only-active severity clears the
                  // filter (`toggleSingleValue` returns `[]`). Any other click
                  // collapses multi-selection down to the clicked bucket.
                  const vulnKey: VulnSeverity =
                    key === "none" ? "unknown" : (key as VulnSeverity);
                  setSeverity((prev) => toggleSingleValue(prev, vulnKey));
                }}
              />
            </CardContent>
          </Card>
        </div>
      ) : null}

      <VulnerabilitiesToolbar
        groupBy={groupBy}
        onGroupByChange={setGroupBy}
        search={search}
        onSearchChange={setSearch}
        sort={sort}
        onSortKeyChange={handleSortKeyChange}
        status={status}
        onStatusChange={(next) => {
          setStatus(next);
        }}
        minEpss={minEpss}
        onMinEpssChange={(next) => {
          setMinEpss(next);
        }}
        reachable={reachable}
        onReachableChange={(next) => {
          setReachable(next);
        }}
        sla={sla}
        onSlaChange={(next) => {
          setSla(next);
        }}
        vexSuppressedOnly={vexSuppressedOnly}
        onVexSuppressedOnlyChange={(next) => {
          setVexSuppressedOnly(next);
        }}
        projectId={projectId}
        projectName={projectName}
        projectRole={projectRole}
        readOnly={readOnly}
        severity={severity}
        onSeverityChange={(next) => {
          setSeverity(next);
        }}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={(next) => {
          setLicenseCategory(next);
        }}
        mountedExtraFilters={mountedExtraFilters}
        onMountExtraFilter={mountExtraFilter}
        onUnmountExtraFilter={unmountExtraFilter}
        columnsCatalog={columnsCatalog}
        visibleColumns={visibleColumns}
        onVisibleColumnsChange={setVisibleColumns}
        columnsStorageKey={VULN_COLUMNS_STORAGE_KEY}
      />

      {groupBy === "flat" ? (
        <>
      <ActiveFilterChips<VulnSeverity>
        severity={severity}
        onSeverityChange={(next) => {
          setSeverity(next);
        }}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={(next) => {
          setLicenseCategory(next);
        }}
      />

      <div
        // W11-C polish — summary band lands on the same px-6 gutter the table
        // header / rows use, with the softer border-border/60 divider so the
        // band reads as part of the table stack rather than a heavy seam.
        className="flex items-center justify-between border-b border-border/60 px-6 py-2 text-xs text-muted-foreground"
        data-testid="vulnerabilities-summary"
        data-total={shownTotal}
        data-loaded={items.length}
      >
        <span>
          {vexSuppressedOnly
            ? t("vulnerabilities.summary_vex_suppressed", {
                shown: items.length,
                scanned: fetchedItems.length,
              })
            : t("vulnerabilities.summary", {
                loaded: items.length,
                total,
              })}
        </span>
      </div>

      <VulnerabilityBulkActionBar
        projectId={projectId}
        selectedIds={Array.from(selectedIds)}
        selectedStatuses={selectedStatuses}
        projectRole={projectRole}
        readOnly={readOnly}
        onCleared={clearSelection}
      />

      {vulnerabilities.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="vulnerabilities-error">
            <AlertDescription>
              {problemMessage(vulnerabilities.error, t, {
                action: "vulnerabilities.errors.load_failed",
              })}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      {vulnerabilities.isLoading ? (
        <div
          className="flex flex-col gap-2 px-4 py-3"
          data-testid="vulnerabilities-loading"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : null}

      {!vulnerabilities.isLoading &&
      !vulnerabilities.isError &&
      items.length === 0 ? (
        <EmptyState
          data-testid="vulnerabilities-empty"
          className="m-6"
          icon={<ShieldCheck />}
          title={
            hasNarrowingFilters
              ? t("vulnerabilities.empty.filtered_title")
              : t("vulnerabilities.empty.title")
          }
          description={
            hasNarrowingFilters
              ? t("vulnerabilities.empty.filtered_subtitle")
              : t("vulnerabilities.empty.subtitle")
          }
          // Two different empty states wearing one label until now. Zero rows
          // because a filter excluded them wants the filter cleared; zero rows
          // because nothing has been scanned wants a scan. Offering "run a
          // scan" to someone with a severity filter on sends them to do work
          // that will not change what they see.
          action={
            hasNarrowingFilters ? (
              <Button
                variant="outline"
                size="sm"
                onClick={clearAllFilters}
                data-testid="vulnerabilities-empty-clear-filters"
              >
                {t("vulnerabilities.empty.clear_filters")}
              </Button>
            ) : onScan ? (
              <Button
                size="sm"
                onClick={onScan}
                data-testid="vulnerabilities-empty-scan"
              >
                {t("vulnerabilities.empty.run_scan")}
              </Button>
            ) : undefined
          }
        />
      ) : null}

      {!vulnerabilities.isLoading &&
      !vulnerabilities.isError &&
      items.length > 0 ? (
        // User-test follow-up — the row needs a pinned inner width to render
        // every cell without the flex-1 Component column collapsing the
        // fixed-width cells' visible position. Same pattern ComponentsTab
        // uses: pin the inner width and let the outer wrapper scroll
        // horizontally instead of column-hiding.
        //
        // G0-5 — the floor was 1250 px and the columns had outgrown it three
        // times (Title w-56, Discovered w-28, SLA due w-36 were each added
        // without raising it). The header, being a plain flex row, absorbed
        // the shortfall by shrinking its cells; the rows, whose inner button
        // had `min-width: auto`, refused to and overflowed the track instead.
        // The two disagreed by 327 px at the last column — "Discovered" sat
        // above the SLA values — and the misalignment is visible in the
        // committed 1440 px baseline, so it shipped.
        //
        // The floor below is the sum of the declared column widths, and it
        // has to stay that: 48 (px-6) + 16 (w-4) + 1152 (the nine other
        // fixed cells) + 120 (ten gap-3 gutters) + 260 (Component's floor)
        // = 1596, rounded up. `tests/e2e/tableColumnAlignment.spec.ts` fails
        // if a new column lands without this number moving with it — that
        // check is the reason a stale floor cannot silently return.
        <div className="flex flex-1 flex-col overflow-x-auto">
          <div
            className="min-w-[1600px] flex flex-1 flex-col"
            // G0-5 backlog — this is the table. Before it, the header and the
            // rows were flex `div`s with no relationship a screen reader could
            // use: no way to move by column, and nowhere valid to put
            // `aria-sort`, which is why that attribute had been sitting on a
            // button doing nothing.
            //
            // `aria-rowcount` is the size of the RESULT SET, not of the DOM.
            // Under virtualization those differ by design, and the whole point
            // of the attribute is to let assistive technology say "row 40 of
            // 64" while only a dozen rows exist.
            role="table"
            aria-label={t("vulnerabilities.table_aria", {
              defaultValue: "Vulnerability findings",
            })}
            aria-rowcount={total + 1}
          >
            <VulnerabilitiesTableHeader
              allSelected={
                items.length > 0 &&
                items.every((it) => selectedIds.has(it.id))
              }
              someSelected={
                items.some((it) => selectedIds.has(it.id)) &&
                !items.every((it) => selectedIds.has(it.id))
              }
              disabled={readOnly}
              currentSort={currentSort}
              onSortChange={handleSortChange}
              visibleColumns={visibleColumns}
              onToggleAll={(checked) => {
                if (checked) {
                  // Select-all is single-page (D-bulk). Truncate to the cap if
                  // the page is larger than what one bulk call can carry.
                  const pageIds = items
                    .slice(0, BULK_TRANSITION_MAX)
                    .map((it) => it.id);
                  setSelectedIds(new Set(pageIds));
                } else {
                  clearSelection();
                }
              }}
            />
            <div
              className="flex-1"
              data-testid="vulnerabilities-virtual"
              data-total={shownTotal}
              data-loaded={items.length}
            >
              <Virtuoso
                components={VIRTUOSO_TABLE_BODY}
                data={items}
                endReached={() => {
                  if (
                    vulnerabilities.hasNextPage &&
                    !vulnerabilities.isFetchingNextPage
                  ) {
                    void vulnerabilities.fetchNextPage();
                  }
                }}
                style={{
                  height: "calc(100vh - var(--layout-header) - 240px)",
                }}
                itemContent={(index, item) => (
                  <VulnerabilityRow
                    vulnerability={item}
                    rowIndex={index}
                    selected={selectedIds.has(item.id)}
                    selectionDisabled={readOnly}
                    visibleColumns={visibleColumns}
                    onToggleSelected={(checked) =>
                      toggleSelection(item.id, checked)
                    }
                    onSelect={() => setDrawerVuln(item.id)}
                  />
                )}
              />
            </div>
          </div>
        </div>
      ) : null}
        </>
      ) : (
        <UpgradeClustersSection
          query={upgradeClusters}
          clusters={clusters}
          totalFindings={upgradeClusters.data?.total_findings ?? 0}
          onOpenFinding={setDrawerVuln}
        />
      )}

      <VulnerabilityDrawer
        open={drawerOpen}
        findingId={drawerId}
        projectId={projectId}
        projectRole={projectRole}
        readOnly={readOnly}
        onOpenChange={(open) => {
          if (!open) setDrawerVuln(null);
        }}
      />

      {/* W4-C #22 — Remediation slot. The collapsible panel keeps the table
          above as the primary surface while exposing the npm dry-run preview
          and team-admin PR creation in one place. Skip in historical mode so
          a user looking at an old snapshot can't queue a PR against a
          stale findings set. */}
      {!readOnly ? (
        <VulnerabilitiesRemediationPanel projectId={projectId} />
      ) : null}
    </div>
  );
}

interface UpgradeClustersSectionProps {
  query: { isLoading: boolean; isError: boolean; error: unknown };
  clusters: UpgradeCluster[];
  totalFindings: number;
  onOpenFinding: (findingId: string) => void;
}

/**
 * W9-#53 — grouped ("By upgrade") view. Owns the same loading / error / empty
 * chrome the flat list uses (skeletons, destructive alert, EmptyState) plus a
 * summary band reading "N upgrades resolve M findings", then hands the sorted
 * clusters to {@link UpgradeClusterList}. Clicking a finding inside a cluster
 * opens the SAME shared drawer via `onOpenFinding`.
 */
function UpgradeClustersSection({
  query,
  clusters,
  totalFindings,
  onOpenFinding,
}: UpgradeClustersSectionProps) {
  const { t } = useTranslation("project_detail");

  if (query.isLoading) {
    return (
      <div
        className="flex flex-col gap-2 px-4 py-3"
        data-testid="vulnerabilities-upgrade-loading"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="px-6 py-6">
        <Alert variant="destructive" data-testid="vulnerabilities-upgrade-error">
          <AlertDescription>
            {problemMessage(query.error, t, {
              action: "vulnerabilities.errors.load_failed",
            })}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (clusters.length === 0) {
    return (
      <EmptyState
        data-testid="vulnerabilities-upgrade-empty"
        className="m-6"
        icon={<PackageCheck />}
        title={t("vulnerabilities.upgrade_cluster.empty.title")}
        description={t("vulnerabilities.upgrade_cluster.empty.subtitle")}
      />
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      <div
        className="flex items-center justify-between border-b border-border/60 px-6 py-2 text-xs text-muted-foreground"
        data-testid="vulnerabilities-upgrade-summary"
        data-clusters={clusters.length}
        data-findings={totalFindings}
      >
        <span>
          {t("vulnerabilities.upgrade_cluster.summary_count", {
            clusters: clusters.length,
            findings: totalFindings,
          })}
        </span>
      </div>
      <UpgradeClusterList clusters={clusters} onOpenFinding={onOpenFinding} />
    </div>
  );
}

interface VulnerabilitiesTableHeaderProps {
  allSelected: boolean;
  someSelected: boolean;
  disabled?: boolean;
  currentSort: SortState | null;
  onSortChange: (next: SortState | null) => void;
  /**
   * W9 #52 — column ids the user has chosen to show. `cve_id` + `severity`
   * are always present (the ColumnsPicker disables their checkboxes); other
   * columns are conditionally rendered.
   */
  visibleColumns: Set<string>;
  onToggleAll: (checked: boolean) => void;
}

function VulnerabilitiesTableHeader({
  allSelected,
  someSelected,
  disabled = false,
  currentSort,
  onSortChange,
  visibleColumns,
  onToggleAll,
}: VulnerabilitiesTableHeaderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      // W11-C polish — table header lands on the px-6 gutter shared by
      // distribution cards / summary band / rows (Vercel deployments-1 axis).
      // Background uses the canonical --muted token (no /30 opacity hack),
      // border-bottom softened via /60 so the seam reads as part of the
      // table chrome, and tracking-wider matches the SortableColumnHeader
      // chip typography. Height stays 32 px (compact identity).
      //
      // G0-5 — `gap-3`, not `gap-2`. The rows put `gap-3` between their cells
      // and the header put `gap-2`, so every column after the first drifted a
      // further 4 px from its data — 36 px of the total misalignment came from
      // this one character. The gutter is now the same value in all three
      // places that use it (here, the row container, the row's inner button).
      className="flex items-center gap-3 border-b border-border/60 bg-muted px-6 text-xs font-medium uppercase tracking-wider text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="vulnerabilities-header"
      // The header row of the table declared on the track above. `table` may
      // own a `row` directly, so this needs no `rowgroup` wrapper; the body
      // does need one, because Virtuoso puts its own elements in between.
      role="row"
      aria-rowindex={1}
    >
      <span className="w-4" role="columnheader">
        <input
          type="checkbox"
          data-testid="vulnerabilities-select-all"
          aria-label={t("vulnerabilities.bulk.select_all_aria")}
          checked={allSelected}
          ref={(el) => {
            // Show the tri-state indeterminate visual when some (but not all)
            // rows on the page are selected.
            if (el) el.indeterminate = someSelected && !allSelected;
          }}
          onChange={(e) => onToggleAll(e.currentTarget.checked)}
          disabled={disabled}
          // W11-C polish — checkbox picks up the focus-ring + accent token
          // so multi-select reads as deliberate UI, not browser default.
          className="h-3.5 w-3.5 rounded-sm border-border text-foreground accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        />
      </span>
      {/* User-test follow-up — column order: CVE / Component / Severity /
          CVSS / EPSS / Reachability / Status. The Summary column was
          dropped from the row (drawer carries the full summary); the
          row's last column is now Status. Component is sortable.
          W11-C — non-sortable CVE label inherits the strip's typography
          (uppercase + tracking-wider) from the parent, so the cell needs
          no extra class. */}
      <span className="w-44" role="columnheader">
        {t("vulnerabilities.column.cve_id")}
      </span>
      {/* M-27 — Title column (source: list `summary`). Not sortable: the
          backend exposes no summary sort key, so the header is a plain label
          like CVE. */}
      {visibleColumns.has("title") ? (
        <span
          className="w-56"
          data-testid="vulnerabilities-header-cell-title"
          role="columnheader"
        >
          {t("vulnerabilities.column.title")}
        </span>
      ) : null}
      {visibleColumns.has("component") ? (
        <span
          className="flex-1 min-w-[260px]"
          data-testid="vulnerabilities-header-cell-component"
          role="columnheader"
          aria-sort={ariaSortFor("component", currentSort)}
        >
          <SortableColumnHeader
            column="component"
            label={t("vulnerabilities.column.component")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-component"
          />
        </span>
      ) : null}
      <span
        className="w-28"
        role="columnheader"
        aria-sort={ariaSortFor("severity", currentSort)}
      >
        <SortableColumnHeader
          column="severity"
          label={t("vulnerabilities.column.severity")}
          currentSort={currentSort}
          onSort={onSortChange}
          testId="vulnerabilities-sort-header-severity"
        />
      </span>
      {visibleColumns.has("cvss") ? (
        <span
          className="w-16 text-right"
          data-testid="vulnerabilities-header-cell-cvss"
          role="columnheader"
          aria-sort={ariaSortFor("cvss", currentSort)}
        >
          <SortableColumnHeader
            column="cvss"
            label={t("vulnerabilities.column.cvss")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-cvss"
          />
        </span>
      ) : null}
      {visibleColumns.has("epss") ? (
        <span
          className="w-20 text-right"
          data-testid="vulnerabilities-header-cell-epss"
          role="columnheader"
          aria-sort={ariaSortFor("epss", currentSort)}
          title={t("vulnerabilities.epss.tooltip", {
            defaultValue:
              "EPSS — probability this CVE is exploited in the wild within 30 days. Complements CVSS (severity).",
          })}
        >
          <SortableColumnHeader
            column="epss"
            label={t("vulnerabilities.column.epss", { defaultValue: "EPSS" })}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-epss"
          />
        </span>
      ) : null}
      {visibleColumns.has("reachable") ? (
        <span
          className="w-28"
          data-testid="vulnerabilities-header-cell-reachable"
          role="columnheader"
          aria-sort={ariaSortFor("reachable", currentSort)}
        >
          <SortableColumnHeader
            column="reachable"
            label={t("vulnerabilities.column.reachable")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-reachable"
          />
        </span>
      ) : null}
      {/* X1 — SLA due column (source: `sla_due_date` + `sla_status`).
          Sortable via the backend's `sla_due` key: asc = most urgent first,
          rows without an SLA always last (NULLS LAST). */}
      {visibleColumns.has("sla_due") ? (
        <span
          className="w-36"
          data-testid="vulnerabilities-header-cell-sla-due"
          role="columnheader"
          aria-sort={ariaSortFor("sla_due", currentSort)}
        >
          <SortableColumnHeader
            column="sla_due"
            label={t("vulnerabilities.column.sla_due")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-sla-due"
          />
        </span>
      ) : null}
      {visibleColumns.has("status") ? (
        <span
          className="w-32"
          data-testid="vulnerabilities-header-cell-status"
          role="columnheader"
          aria-sort={ariaSortFor("status", currentSort)}
        >
          <SortableColumnHeader
            column="status"
            label={t("vulnerabilities.column.status")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-status"
          />
        </span>
      ) : null}
      {/* M-27 — Discovered column (source: `discovered_at`). The sort key
          `discovered_at` was already in VALID_SORT — the header just wires
          it to the existing SortableColumnHeader cycle. */}
      {visibleColumns.has("discovered") ? (
        <span
          className="w-28"
          data-testid="vulnerabilities-header-cell-discovered"
          role="columnheader"
          aria-sort={ariaSortFor("discovered_at", currentSort)}
        >
          <SortableColumnHeader
            column="discovered_at"
            label={t("vulnerabilities.column.discovered")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="vulnerabilities-sort-header-discovered"
          />
        </span>
      ) : null}
    </div>
  );
}

interface VulnerabilityRowProps {
  vulnerability: VulnerabilityListItem;
  rowIndex: number;
  selected: boolean;
  selectionDisabled?: boolean;
  /** W9 #52 — controlled by the ColumnsPicker. See `VULN_COLUMNS_STORAGE_KEY`. */
  visibleColumns: Set<string>;
  onToggleSelected: (checked: boolean) => void;
  onSelect: () => void;
}

function VulnerabilityRow({
  vulnerability,
  rowIndex,
  selected,
  selectionDisabled = false,
  visibleColumns,
  onToggleSelected,
  onSelect,
}: VulnerabilityRowProps) {
  return (
    <div
      data-testid="vulnerability-row"
      data-finding-id={vulnerability.id}
      data-cve-id={vulnerability.cve_id}
      data-row-index={rowIndex}
      data-selected={selected ? "true" : "false"}
      // G0-5 backlog — a real row in a real table.
      //
      // `aria-rowindex` is 1-based and counts the header, so the first data
      // row is 2. It is the index in the WHOLE result set, not in the rendered
      // window: that is the point of the attribute under virtualization —
      // "row 40 of 64" stays true while only a dozen rows exist in the DOM.
      //
      // The click moved here from the button that used to wrap most of the
      // cells. That button could not stay: a `row` owns `cell`s, and a button
      // spanning nine of them is not one. The keyboard path lives in the CVE
      // cell now, which is a cell a `row` can legitimately own.
      role="row"
      aria-rowindex={rowIndex + 2}
      onClick={onSelect}
      className={cn(
        // W11-C polish — Vercel deployments-1 row tone. The row sits on
        // bg-card so it reads as a white surface against the off-white
        // canvas; hover lifts the `--accent` muted tint via the Linear
        // motion tokens. Border softened to /60 so the seam stays light at
        // 40 px density. Selected state uses the same accent token (no /30
        // opacity hack) so the visual carries on to dark mode forward-compat.
        // G0-5 — `gap-3` here too, so the checkbox-to-CVE gutter matches the
        // header's. See the header's note on the 4 px-per-column drift.
        "flex w-full items-center gap-3 border-b border-border/60 bg-card px-6 text-left text-sm transition-colors duration-fast ease-out-soft hover:bg-accent",
        selected ? "bg-accent" : undefined,
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span
        className="w-4"
        role="cell"
        data-testid="vulnerability-row-checkbox-cell"
      >
        <input
          type="checkbox"
          data-testid="vulnerability-row-checkbox"
          aria-label={`select-${vulnerability.cve_id}`}
          checked={selected}
          disabled={selectionDisabled}
          onChange={(e) => onToggleSelected(e.currentTarget.checked)}
          onClick={(e) => {
            // Stop the row body's click handler from also opening the drawer
            // when the user is just toggling selection.
            e.stopPropagation();
          }}
          // W11-C polish — checkbox picks up the focus-ring + accent so
          // bulk-select reads as deliberate UI, matching the header
          // select-all checkbox.
          className="h-3.5 w-3.5 rounded-sm border-border text-foreground accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        />
      </span>
      {/* Column order matches the header above:
          CVE / Component / Severity / CVSS / EPSS / Reachability /
          Summary / Status. License lives in the drawer only — user-test
          feedback flagged the row license column as noise.
          W9 #52 — each optional cell is gated on `visibleColumns`; required
          ids (`cve_id`, `severity`) always render. */}
      {/* KEV feature — the CVE cell pairs the id with a compact KEV badge
          when the CVE sits in the CISA catalog. The badge carries its own
          "KEV" text label (color is never the only signal); the due date
          rides on its tooltip so the 40px row stays quiet. */}
      <span
        className="flex w-44 min-w-0 items-center gap-1.5"
        role="cell"
        data-testid="vulnerability-row-cve"
        data-kev={vulnerability.kev ? "true" : "false"}
      >
        {/* G0-5 — the keyboard path into the drawer. It used to be a button
            wrapping nine cells, which a `row` cannot own; here it is one
            control in the identifier cell. `stopPropagation` keeps the row's own click from
            firing a second time on top of this one. */}
        <button
          type="button"
          data-testid="vulnerability-row-open"
          onClick={(e) => {
            e.stopPropagation();
            onSelect();
          }}
          title={vulnerability.cve_id}
          className="truncate rounded-sm font-mono text-xs hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
        >
          {vulnerability.cve_id}
        </button>
        <KevBadge
          kev={vulnerability.kev}
          dueDate={vulnerability.kev_due_date}
          className="shrink-0 px-1.5 py-0 text-[10px]"
        />
      </span>
      {visibleColumns.has("title") ? (
        <TitleCell summary={vulnerability.summary} />
      ) : null}
      {visibleColumns.has("component") ? (
        <ComponentColumnCell
          name={vulnerability.affected_component_name}
          version={vulnerability.affected_component_version}
          count={vulnerability.affected_component_count}
        />
      ) : null}
      <span className="w-28" role="cell">
        <SeverityBadge severity={vulnerability.severity} />
      </span>
      {visibleColumns.has("cvss") ? (
        <span
          className="w-16 text-right font-mono text-xs tabular-nums"
          role="cell"
          data-testid="vulnerability-row-cvss"
        >
          {vulnerability.cvss_score != null
            ? vulnerability.cvss_score.toFixed(1)
            : "—"}
        </span>
      ) : null}
      {visibleColumns.has("epss") ? (
        <EpssCell
          score={vulnerability.epss_score}
          percentile={vulnerability.epss_percentile}
        />
      ) : null}
      {visibleColumns.has("reachable") ? (
        <span
          className="flex w-28 items-center"
          role="cell"
          data-testid="vulnerability-row-reachability"
          data-reachable={
            vulnerability.reachable == null
              ? "unknown"
              : String(vulnerability.reachable)
          }
        >
          <ReachabilityBadge
            reachable={vulnerability.reachable}
            source={vulnerability.reachability_source}
          />
        </span>
      ) : null}
      {visibleColumns.has("sla_due") ? (
        <SlaDueCell
          status={vulnerability.sla_status}
          dueDate={vulnerability.sla_due_date}
        />
      ) : null}
      {visibleColumns.has("status") ? (
        <span className="flex w-32 items-center gap-1" role="cell">
          <VulnerabilityStatusBadge status={vulnerability.status} />
          {vulnerability.analysis_source === "vex_import" ? (
            <VexProvenanceMarker />
          ) : null}
        </span>
      ) : null}
      {visibleColumns.has("discovered") ? (
        <DiscoveredCell discoveredAt={vulnerability.discovered_at} />
      ) : null}
    </div>
  );
}

interface TitleCellProps {
  /** Advisory summary from the list payload — may be null on sparse CVEs. */
  summary: string | null;
}

/**
 * Title table cell (M-27) — renders the finding's advisory `summary` as a
 * single truncated line at 40px density. The full text is folded into the
 * `title` tooltip; a null summary renders the same em-dash placeholder the
 * CVSS / EPSS cells use, never an empty gap.
 */
function TitleCell({ summary }: TitleCellProps) {
  const hasSummary = summary != null && summary.length > 0;
  return (
    <span
      className={cn(
        "w-56 truncate text-xs",
        hasSummary ? "text-foreground" : "text-muted-foreground",
      )}
      role="cell"
      data-testid="vulnerability-row-title"
      title={hasSummary ? summary : undefined}
    >
      {hasSummary ? summary : "—"}
    </span>
  );
}

interface DiscoveredCellProps {
  /** ISO-8601 instant the finding was first persisted. Always on the wire. */
  discoveredAt: string;
}

/**
 * Discovered table cell (M-27) — relative timestamp via the shared
 * `RelativeTime` component (same pattern across the product), which renders a
 * semantic `<time>` carrying the absolute instant in its `title` tooltip for
 * auditors. The outer span keeps the `data-discovered-at` harness hook.
 */
function DiscoveredCell({ discoveredAt }: DiscoveredCellProps) {
  const { i18n } = useTranslation("project_detail");
  return (
    <span
      className="w-28 truncate text-xs text-muted-foreground"
      role="cell"
      data-testid="vulnerability-row-discovered"
      data-discovered-at={discoveredAt}
    >
      <RelativeTime value={discoveredAt} locale={i18n.language} />
    </span>
  );
}

interface SlaDueCellProps {
  /** Server-computed SLA state — rendered verbatim, never recomputed. */
  status: SlaStatus | null;
  /** SLA due instant (ISO-8601) or `null` when the severity has no SLA. */
  dueDate: string | null;
}

/**
 * SLA due table cell (X1). Renders the {@link SlaBadge} with the due date
 * inline (state label + date — the column IS the deadline, so hiding the
 * date behind a tooltip would defeat it). Findings without an SLA
 * (info / unknown severity → `sla_status === null`) render the same em-dash
 * placeholder the CVSS / EPSS cells use, never an empty gap.
 */
function SlaDueCell({ status, dueDate }: SlaDueCellProps) {
  const { t } = useTranslation("project_detail");
  return (
    <span
      className="flex w-36 items-center"
      role="cell"
      data-testid="vulnerability-row-sla"
      data-sla-status={status ?? "none"}
    >
      {status != null ? (
        <SlaBadge
          status={status}
          dueDate={dueDate}
          showDueDate
          className="px-1.5 py-0 text-[10px]"
        />
      ) : (
        <span
          className="font-mono text-xs text-muted-foreground"
          title={t("vulnerabilities.sla.empty")}
        >
          —
        </span>
      )}
    </span>
  );
}

interface EpssCellProps {
  score: number | null;
  percentile: number | null;
}

/**
 * EPSS table cell. Renders the score as a one-decimal percentage in the mono
 * accent font and folds the percentile ("Top N%") into the title tooltip so
 * the compact 40px row stays narrow. A missing score renders the em-dash
 * placeholder — never "0%".
 */
function EpssCell({ score, percentile }: EpssCellProps) {
  const { t } = useTranslation("project_detail");
  const formattedScore = formatEpssScore(score);
  const percentileLabel = epssPercentileLabel(percentile);
  const formattedPercentile = percentileLabel
    ? t(percentileLabel.key, percentileLabel.params)
    : null;

  if (formattedScore == null) {
    return (
      <span
        className="w-20 text-right font-mono text-xs tabular-nums text-muted-foreground"
        role="cell"
        data-testid="vulnerability-row-epss"
        data-epss-empty="true"
        title={t("vulnerabilities.epss.empty", {
          defaultValue: "No EPSS data for this CVE",
        })}
      >
        {EPSS_EMPTY}
      </span>
    );
  }

  // Percentile becomes the tooltip — "97.3% · Top 9%" — so triagers can read
  // the rank without widening the column.
  const tooltip =
    formattedPercentile != null
      ? t("vulnerabilities.epss.cell_tooltip", {
          score: formattedScore,
          percentile: formattedPercentile,
          defaultValue: "{{score}} · {{percentile}} most likely to be exploited",
        })
      : formattedScore;

  return (
    <span
      className="w-20 text-right font-mono text-xs tabular-nums"
      role="cell"
      data-testid="vulnerability-row-epss"
      data-epss-score={score ?? undefined}
      title={tooltip}
    >
      {formattedScore}
    </span>
  );
}

interface ComponentColumnCellProps {
  /** Pinned cv name. Backend may return null on legacy rows (cv deleted). */
  name: string | null;
  /** Pinned cv version string. */
  version: string | null;
  /**
   * Distinct cvs affected by this CVE in the scan (the row is one of them).
   * Drives the `+N-1` suffix when the CVE bundles more than one cv.
   */
  count: number;
}

/**
 * Component@Version cell — replaces the old standalone "Affected count"
 * column. Renders the FK-pinned cv as `name@version` in the mono accent so
 * package names stand out, then appends `+N-1` when the CVE touches more
 * cvs (the drawer still carries the full list via `affected_components`).
 * A row missing both name and version renders the localized dash so empty
 * cells are obvious — never the bare string "null@null".
 *
 * The count badge stays inline (not stacked) so the column doubles as
 * "Affected": triagers reading the list see at a glance whether the CVE
 * touches one or many packages without opening the drawer.
 */
function ComponentColumnCell({ name, version, count }: ComponentColumnCellProps) {
  const { t } = useTranslation("project_detail");
  const hasIdentity = name != null && version != null;
  const label = hasIdentity
    ? `${name}@${version}`
    : t("components.license.unknown_dash");
  const remainder = Math.max(0, count - 1);
  // tabular-nums on the suffix keeps "+9" / "+99" aligned across rows.
  return (
    <span
      className="flex flex-1 min-w-[260px] items-center gap-1 truncate"
      role="cell"
      data-testid="vulnerability-row-component"
      data-component-name={name ?? ""}
      data-component-version={version ?? ""}
      data-affected-count={count}
      title={hasIdentity ? label : undefined}
    >
      <span
        className={cn(
          "truncate font-mono text-xs",
          hasIdentity ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {label}
      </span>
      {remainder > 0 ? (
        <span
          className="shrink-0 rounded bg-muted px-1 text-[10px] font-medium tabular-nums text-muted-foreground"
          data-testid="vulnerability-row-component-more"
          title={t("vulnerabilities.column.affected_more_count_tooltip", {
            count: remainder,
            defaultValue:
              "{{count}} more component(s) also affected — see drawer",
          })}
        >
          {t("vulnerabilities.column.affected_more_count", {
            count: remainder,
            defaultValue: "+{{count}}",
          })}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Small "VEX" marker shown beside a finding's status badge when the status was
 * driven by a VEX import (`analysis_source === "vex_import"`). Pairs the color
 * with the literal "VEX" label so the signal is not color-only (a11y).
 */
function VexProvenanceMarker() {
  const { t } = useTranslation("project_detail");
  return (
    <span
      data-testid="vulnerability-row-vex-marker"
      className="rounded border border-primary/40 bg-primary/10 px-1 text-[9px] font-semibold uppercase tracking-wide text-primary"
      title={t("vulnerabilities.vex.marker_tooltip")}
    >
      {t("vulnerabilities.vex.marker")}
    </span>
  );
}
