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
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

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
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

function deriveRole(item: { is_superuser: boolean }): UserRole {
  // The list payload doesn't carry memberships (kept lightweight), so
  // `super_admin` is the only role the column can show with full confidence.
  // Non-superusers display as the neutral "developer" badge until the drawer
  // loads the membership detail. Color is paired with an icon (RoleBadge).
  return item.is_superuser ? "super_admin" : "developer";
}

export function AdminUsersPage() {
  const { t, i18n } = useTranslation("admin");

  const [searchInput, setSearchInput] = useState("");
  const [searchDebounced, setSearchDebounced] = useState("");
  const [roleFilter, setRoleFilter] = useState<UserRole | "all">("all");
  const [activeFilter, setActiveFilter] = useState<UsersActiveFilter>("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZE_OPTIONS)[number]>(50);
  const [openUserId, setOpenUserId] = useState<string | null>(null);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  // Debounce the search input → 300ms (matches ProjectListPage convention).
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearchDebounced(searchInput);
      setPage(1);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput]);

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
        onRoleChange={(v) => {
          setRoleFilter(v);
          setPage(1);
        }}
        active={activeFilter}
        onActiveChange={(v) => {
          setActiveFilter(v);
          setPage(1);
        }}
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
                      {u.last_login_at
                        ? formatRelativeToNow(
                            u.last_login_at,
                            i18n.resolvedLanguage,
                          )
                        : t("admin.users.drawer.never")}
                    </td>
                    <td className="px-3 text-right text-xs text-muted-foreground">
                      {/* Backend list response stays lightweight; team count
                          comes from the drawer detail. Keep an em-dash so the
                          column is visible and visually balanced. */}
                      —
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
              setPageSize(
                Number(e.target.value) as (typeof PAGE_SIZE_OPTIONS)[number],
              );
              setPage(1);
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
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="admin-users-page-prev"
          >
            {t("admin.users.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
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
