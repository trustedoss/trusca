// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { ChevronRight, FolderTree } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useGroups } from "@/features/groups/api/useGroups";
import type { GroupListItem } from "@/features/groups/api/groupsApi";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

/**
 * GroupListPage, group-hierarchy Phase 4 PR 4-B.
 *
 * Two independent modes, matching `GET /v1/groups`'s own two read shapes
 * (see `GroupsHarness`'s module docstring):
 *
 *   - Drill-down (default): no search term. Rows are the current level's
 *     DIRECT children (root groups with no drill state); clicking a row's
 *     name descends into ITS children, staying on this page. A trail above
 *     the table shows the path back to root.
 *   - Flat search: typing a term switches to a whole-tree, depth-independent
 *     match list (`?q=`); drill state stops mattering for what is SHOWN
 *     (the harness's own wording), though it is not cleared, so clearing the
 *     search returns to the level the reader was browsing.
 *
 * A row carries two independent click targets, and the harness is explicit
 * that these must stay separate: the name text drills into the row's own
 * children (stays on `/groups`); a dedicated button opens the row's own
 * detail page (`/groups/:id`, leaves this list).
 */

const PAGE_SIZE = 50;

interface DrillLevel {
  id: string;
  name: string;
}

export function GroupListPage() {
  const { t } = useTranslation("groups");
  const navigate = useNavigate();

  const [searchInput, setSearchInput] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [path, setPath] = useState<DrillLevel[]>([]);
  const [page, setPage] = useState(1);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(searchInput), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput]);

  const trimmedQuery = debouncedQuery.trim();
  const isSearching = trimmedQuery.length > 0;
  const currentParentId = path.length > 0 ? path[path.length - 1].id : undefined;

  // Narrowing (a new search term, or a new drill level) starts back on page 1,
  // otherwise a reader four pages into "everything" could land on an empty
  // page 4 of a two-row search result.
  useEffect(() => {
    setPage(1);
  }, [trimmedQuery, currentParentId]);

  const groupsQuery = useGroups({
    q: isSearching ? trimmedQuery : undefined,
    parent_id: isSearching ? undefined : currentParentId,
    page,
    page_size: PAGE_SIZE,
  });

  const items = groupsQuery.data?.items ?? [];
  const total = groupsQuery.data?.total ?? 0;
  const isBusy = groupsQuery.isFetching;
  const isEmpty = !groupsQuery.isLoading && !groupsQuery.isError && items.length === 0;

  function resetSearch() {
    setSearchInput("");
    setDebouncedQuery("");
  }

  function handleDrillInto(group: GroupListItem) {
    resetSearch();
    setPath((prev) => [...prev, { id: group.id, name: group.name }]);
  }

  function handleOpenDetail(group: GroupListItem) {
    navigate(`/groups/${group.id}`);
  }

  function handleDrilldownRoot() {
    resetSearch();
    setPath([]);
  }

  function handleDrilldownSegment(index: number) {
    resetSearch();
    setPath((prev) => prev.slice(0, index + 1));
  }

  return (
    <div className="flex h-full flex-col" data-testid="groups-page">
      <PageHeader variant="bar" title={t("page.title")} />

      <div
        className="flex flex-wrap items-center gap-3 border-b bg-card px-6 py-3"
        data-testid="groups-toolbar"
      >
        <div className="grow basis-64">
          <Label htmlFor="groups-search" className="text-xs text-muted-foreground">
            {t("list.search_placeholder")}
          </Label>
          <Input
            id="groups-search"
            data-testid="groups-search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("list.search_placeholder")}
            aria-label={t("list.search_aria")}
            maxLength={255}
            className="h-9"
          />
        </div>
      </div>

      {/* Drill trail. The root control is always present and is a SEPARATE
          affordance from the per-level segments (harness: `...-root` vs.
          `...-segment`); clicking it clears the whole path in one step
          rather than requiring N clicks back through N segments. */}
      <div
        className="flex flex-wrap items-center gap-1 border-b bg-muted/20 px-6 py-2 text-sm"
        data-testid="groups-drilldown-trail"
      >
        <button
          type="button"
          onClick={handleDrilldownRoot}
          data-testid="groups-drilldown-breadcrumb-root"
          className={cn(
            "rounded px-1.5 py-0.5 transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            path.length === 0
              ? "font-medium text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t("list.drilldown.root")}
        </button>
        {path.map((level, index) => (
          <span key={level.id} className="flex items-center gap-1">
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
            <button
              type="button"
              onClick={() => handleDrilldownSegment(index)}
              data-testid="groups-drilldown-breadcrumb-segment"
              data-group-name={level.name}
              className={cn(
                "rounded px-1.5 py-0.5 transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                index === path.length - 1
                  ? "font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {level.name}
            </button>
          </span>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {groupsQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="groups-error">
              <AlertDescription>{t("list.errors.load_failed")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="groups-table"
          aria-busy={isBusy}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("page.title")}</th>
              <th className="px-3 py-2" />
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody data-testid="groups-tbody">
            {groupsQuery.isLoading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={3}>
                      <Skeleton className="h-8 w-full" />
                    </td>
                  </tr>
                ))
              : items.map((group) => (
                  <GroupRow
                    key={group.id}
                    group={group}
                    onDrill={() => handleDrillInto(group)}
                    onOpenDetail={() => handleOpenDetail(group)}
                  />
                ))}
          </tbody>
        </table>

        {isEmpty ? (
          <EmptyState
            data-testid="groups-empty"
            icon={<FolderTree />}
            title={isSearching ? t("list.empty.title_search") : t("list.empty.title_root")}
            description={
              isSearching
                ? t("list.empty.description_search", { query: trimmedQuery })
                : t("list.empty.description_root")
            }
          />
        ) : null}
      </div>

      {total > PAGE_SIZE ? (
        <footer
          className="flex shrink-0 items-center justify-end gap-2 border-t bg-card px-6 py-2 text-xs"
          data-testid="groups-pagination"
        >
          <span className="text-muted-foreground">
            {page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))}
          </span>
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-50"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="groups-page-prev"
          >
            ‹
          </button>
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-50"
            disabled={page >= Math.ceil(total / PAGE_SIZE)}
            onClick={() => setPage((p) => p + 1)}
            data-testid="groups-page-next"
          >
            ›
          </button>
        </footer>
      ) : null}
    </div>
  );
}

function GroupRow({
  group,
  onDrill,
  onOpenDetail,
}: {
  group: GroupListItem;
  onDrill: () => void;
  onOpenDetail: () => void;
}) {
  const { t } = useTranslation("groups");
  const hasChildren = group.child_group_count > 0;

  return (
    <tr
      data-testid="groups-row"
      data-group-id={group.id}
      data-group-name={group.name}
      className="border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40"
    >
      <td className="px-6 py-2">
        <button
          type="button"
          onClick={onDrill}
          data-testid="groups-row-name"
          aria-label={t("list.row.drill_aria", { name: group.name })}
          className="flex flex-col items-start gap-0 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <span className="flex items-center gap-1 font-medium hover:underline">
            {group.name}
            {hasChildren ? (
              <ChevronRight
                className="h-3.5 w-3.5 text-muted-foreground"
                aria-hidden
                data-testid="groups-row-has-children"
              />
            ) : null}
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            {group.slug}
          </span>
        </button>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary" data-testid="groups-row-child-count" data-value={group.child_group_count}>
            {t("list.row.subgroups", { count: group.child_group_count })}
          </Badge>
          <Badge variant="secondary" data-testid="groups-row-project-count" data-value={group.project_count}>
            {t("list.row.projects", { count: group.project_count })}
          </Badge>
          <Badge variant="secondary" data-testid="groups-row-member-count" data-value={group.member_count}>
            {t("list.row.members", { count: group.member_count })}
          </Badge>
          <span className="ml-1 text-xs text-muted-foreground">
            <RelativeTime value={group.updated_at} />
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <button
          type="button"
          onClick={onOpenDetail}
          data-testid="groups-row-open-detail"
          aria-label={t("list.row.open_detail_aria", { name: group.name })}
          className="rounded border px-2 py-1 text-xs font-medium transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {t("list.row.open_detail")}
        </button>
      </td>
    </tr>
  );
}
