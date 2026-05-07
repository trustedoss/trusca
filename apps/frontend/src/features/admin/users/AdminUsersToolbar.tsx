/**
 * Inline toolbar for the admin Users table — role / status / search filters.
 * Filters are URL-state-friendly: parent owns the filter values and passes
 * controlled props in. No modal — every filter is inline (CLAUDE.md "디자인
 * 시스템" §filter rules).
 */
import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { UserRole } from "@/features/admin/api/adminUsersApi";
import { cn } from "@/lib/utils";

export type UsersActiveFilter = "all" | "active" | "inactive";

export interface AdminUsersToolbarProps {
  search: string;
  onSearchChange: (next: string) => void;
  role: UserRole | "all";
  onRoleChange: (next: UserRole | "all") => void;
  active: UsersActiveFilter;
  onActiveChange: (next: UsersActiveFilter) => void;
}

export function AdminUsersToolbar({
  search,
  onSearchChange,
  role,
  onRoleChange,
  active,
  onActiveChange,
}: AdminUsersToolbarProps) {
  const { t } = useTranslation("admin");

  return (
    <div
      className="flex flex-wrap items-end gap-3 border-b bg-card px-6 py-3"
      data-testid="admin-users-toolbar"
    >
      <div className="grow basis-64">
        <Label
          htmlFor="admin-users-search"
          className="text-xs text-muted-foreground"
        >
          {t("admin.users.filter.search_placeholder")}
        </Label>
        <Input
          id="admin-users-search"
          data-testid="admin-users-search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={t("admin.users.filter.search_placeholder")}
          className="h-9"
        />
      </div>

      <div className="basis-40">
        <Label
          htmlFor="admin-users-role-filter"
          className="text-xs text-muted-foreground"
        >
          {t("admin.users.column.role")}
        </Label>
        <select
          id="admin-users-role-filter"
          data-testid="admin-users-role-filter"
          className={cn(
            "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          )}
          value={role}
          onChange={(e) => onRoleChange(e.target.value as UserRole | "all")}
        >
          <option value="all">{t("admin.users.filter.role_all")}</option>
          <option value="super_admin">{t("admin.users.role.super_admin")}</option>
          <option value="team_admin">{t("admin.users.role.team_admin")}</option>
          <option value="developer">{t("admin.users.role.developer")}</option>
        </select>
      </div>

      <div className="basis-40">
        <Label
          htmlFor="admin-users-active-filter"
          className="text-xs text-muted-foreground"
        >
          {t("admin.users.column.active")}
        </Label>
        <select
          id="admin-users-active-filter"
          data-testid="admin-users-active-filter"
          className={cn(
            "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          )}
          value={active}
          onChange={(e) =>
            onActiveChange(e.target.value as UsersActiveFilter)
          }
        >
          <option value="all">{t("admin.users.filter.active_all")}</option>
          <option value="active">{t("admin.users.filter.active_only")}</option>
          <option value="inactive">{t("admin.users.filter.inactive_only")}</option>
        </select>
      </div>
    </div>
  );
}
