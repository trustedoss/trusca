/**
 * AdminUsersPage — Phase 4 PR #13 §4.2.
 *
 * Compact 40px-row table fed by `useAdminUsers`. Filters live inline at the
 * top (no modal) and apply via TanStack Query's tuple key. Search input is
 * debounced 300ms so the user can type without firing a request per keystroke.
 *
 * Detail flow: clicking a row opens `AdminUserDrawer`. The drawer talks to
 * the same query cache so a successful mutation is reflected back into the
 * table without an extra round-trip beyond the invalidation.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAdminUsers } from "@/features/admin/api/useAdminUsers";
import type { UserRole } from "@/features/admin/api/adminUsersApi";
import { AdminToast, type AdminToastMessage } from "@/features/admin/components/AdminToast";
import { RoleBadge } from "@/features/admin/components/RoleBadge";
import { AdminUserDrawer } from "@/features/admin/users/AdminUserDrawer";
import {
  AdminUsersToolbar,
  type UsersActiveFilter,
} from "@/features/admin/users/AdminUsersToolbar";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

function deriveRole(item: { is_superuser: boolean; role?: UserRole }): UserRole {
  // H-2: the list payload now carries the membership rollup (`role` =
  // highest-effective role), so team_admins render correctly in the column.
  // The is_superuser fallback only covers older fixtures without the field.
  return item.role ?? (item.is_superuser ? "super_admin" : "developer");
}

// W12 — URL filter parsers (filter URL persistence consistency).
const VALID_ROLE: (UserRole | "all")[] = [
  "all",
  "super_admin",
  "team_admin",
  "developer",
];
const VALID_ACTIVE: UsersActiveFilter[] = ["all", "active", "inactive"];
function parseRoleParam(v: string | null): UserRole | "all" {
  return v && (VALID_ROLE as readonly string[]).includes(v)
    ? (v as UserRole | "all")
    : "all";
}
function parseActiveParam(v: string | null): UsersActiveFilter {
  return v && (VALID_ACTIVE as readonly string[]).includes(v)
    ? (v as UsersActiveFilter)
    : "all";
}
function parsePageParam(v: string | null): number {
  if (!v) return 1;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
}
function parsePageSizeParam(v: string | null): PageSize {
  if (!v) return 50;
  const n = Number.parseInt(v, 10);
  return (PAGE_SIZE_OPTIONS as readonly number[]).includes(n)
    ? (n as PageSize)
    : 50;
}

export function AdminUsersPage() {
  const { t, i18n } = useTranslation("admin");

  // W12 — filter state is now URL-derived so reload / share / back button
  // restore the exact view (filter URL persistence consistency). Defaults
  // ("all", page 1, page_size 50, empty search) DELETE the param so the URL
  // stays clean. Filter changes rewind ?page so the user doesn't land on a
  // now-empty page after narrowing.
  const [searchParams, setSearchParams] = useSearchParams();
  const roleFilter = parseRoleParam(searchParams.get("role"));
  const activeFilter = parseActiveParam(searchParams.get("active"));
  const page = parsePageParam(searchParams.get("page"));
  const pageSize = parsePageSizeParam(searchParams.get("page_size"));

  // Search keeps a local typing buffer; the debounced value mirrors to URL.
  const [searchInput, setSearchInput] = useState(
    () => searchParams.get("search") ?? "",
  );
  const [searchDebounced, setSearchDebounced] = useState(searchInput);

  const [openUserId, setOpenUserId] = useState<string | null>(null);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  const updateFilterParam = useCallback(
    (key: string, next: string | null) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next == null || next === "") out.delete(key);
          else out.set(key, next);
          // Any filter change rewinds to page 1.
          out.delete("page");
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const setRoleFilter = useCallback(
    (next: UserRole | "all") =>
      updateFilterParam("role", next === "all" ? null : next),
    [updateFilterParam],
  );
  const setActiveFilter = useCallback(
    (next: UsersActiveFilter) =>
      updateFilterParam("active", next === "all" ? null : next),
    [updateFilterParam],
  );
  const setPage = useCallback(
    (next: number) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next <= 1) out.delete("page");
          else out.set("page", String(next));
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const setPageSize = useCallback(
    (next: PageSize) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next === 50) out.delete("page_size");
          else out.set("page_size", String(next));
          // Page size change resets pagination.
          out.delete("page");
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  // Debounce the search input → 300ms (matches ProjectListPage convention).
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearchDebounced(searchInput);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput]);

  // Mirror the debounced search value into the URL.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const current = prev.get("search") ?? "";
        if (current === searchDebounced) return prev;
        const out = new URLSearchParams(prev);
        if (searchDebounced) out.set("search", searchDebounced);
        else out.delete("search");
        // Search change rewinds page (was previously inlined in the debounce
        // effect via setPage(1)).
        out.delete("page");
        return out;
      },
      { replace: false },
    );
  }, [searchDebounced, setSearchParams]);

  const queryParams = useMemo(
    () => ({
      page,
      page_size: pageSize,
      role: roleFilter === "all" ? null : roleFilter,
      active:
        activeFilter === "all" ? null : activeFilter === "active" ? true : false,
      search: searchDebounced.trim() || null,
    }),
    [page, pageSize, roleFilter, activeFilter, searchDebounced],
  );

  const usersQuery = useAdminUsers(queryParams);
  const items = usersQuery.data?.items ?? [];
  const total = usersQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  return (
    <div className="flex h-full flex-col" data-testid="admin-users-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t("admin.users.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("admin.users.subtitle")}
        </p>
      </header>

      <AdminUsersToolbar
        search={searchInput}
        onSearchChange={(v) => setSearchInput(v)}
        role={roleFilter}
        onRoleChange={setRoleFilter}
        active={activeFilter}
        onActiveChange={setActiveFilter}
      />

      <div className="flex-1 overflow-y-auto">
        {usersQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="admin-users-error">
              <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="admin-users-table"
          aria-busy={usersQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("admin.users.column.email")}</th>
              <th className="px-3 py-2">{t("admin.users.column.full_name")}</th>
              <th className="px-3 py-2">{t("admin.users.column.role")}</th>
              <th className="px-3 py-2">{t("admin.users.column.active")}</th>
              <th className="px-3 py-2">
                {t("admin.users.column.last_login_at")}
              </th>
              <th className="px-3 py-2 text-right">
                {t("admin.users.column.team_count")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="admin-users-tbody">
            {usersQuery.isLoading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={6}>
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))
              : items.map((u) => (
                  <tr
                    key={u.id}
                    data-testid="admin-users-row"
                    data-user-id={u.id}
                    data-email={u.email}
                    data-role={deriveRole(u)}
                    data-active={u.is_active}
                    className={cn(
                      "cursor-pointer border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-within:bg-accent/40",
                    )}
                    style={{ height: "var(--table-row)" }}
                    tabIndex={0}
                    onClick={() => setOpenUserId(u.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenUserId(u.id);
                      }
                    }}
                  >
                    <td className="truncate px-6 font-mono text-xs">
                      {u.email}
                    </td>
                    <td className="truncate px-3">{u.full_name ?? "—"}</td>
                    <td className="px-3">
                      <RoleBadge role={deriveRole(u)} />
                    </td>
                    <td className="px-3">
                      <Badge
                        variant="outline"
                        className={cn(
                          u.is_active
                            ? "border-emerald-300 bg-emerald-50 text-emerald-700"
                            : "border-muted bg-muted text-muted-foreground",
                        )}
                      >
                        {u.is_active
                          ? t("admin.users.status.active")
                          : t("admin.users.status.inactive")}
                      </Badge>
                    </td>
                    <td className="px-3 text-xs text-muted-foreground">
                      {u.last_login_at ? (
                        <RelativeTime
                          value={u.last_login_at}
                          locale={i18n.resolvedLanguage}
                        />
                      ) : (
                        t("admin.users.drawer.never")
                      )}
                    </td>
                    <td
                      className="px-3 text-right text-xs text-muted-foreground"
                      data-testid="admin-users-team-count"
                    >
                      {/* H-2: membership rollup from the list payload. */}
                      {u.team_count ?? "—"}
                    </td>
                  </tr>
                ))}
            {!usersQuery.isLoading && items.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-6 py-12 text-center text-sm text-muted-foreground"
                  data-testid="admin-users-empty"
                >
                  {t("admin.users.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="admin-users-pagination"
      >
        <div className="flex items-center gap-2">
          <label
            htmlFor="admin-users-page-size"
            className="text-muted-foreground"
          >
            {t("admin.users.pagination.page_size_label")}
          </label>
          <select
            id="admin-users-page-size"
            data-testid="admin-users-page-size"
            className="h-8 rounded-md border border-input bg-background px-2"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value) as PageSize);
            }}
          >
            {PAGE_SIZE_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("admin.users.pagination.page_label", {
              page,
              total: totalPages,
            })}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage(Math.max(1, page - 1))}
            data-testid="admin-users-page-prev"
          >
            {t("admin.users.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage(Math.min(totalPages, page + 1))}
            data-testid="admin-users-page-next"
          >
            {t("admin.users.pagination.next")}
          </Button>
        </div>
      </footer>

      <AdminUserDrawer
        open={openUserId !== null}
        userId={openUserId}
        onOpenChange={(open) => {
          if (!open) setOpenUserId(null);
        }}
        notify={notify}
      />

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
