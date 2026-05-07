/**
 * AdminLayout — Phase 4 PR #13 §4.1.
 *
 * Owns the admin-only chrome:
 *   - 224px fixed sidebar (CLAUDE.md design system) with two entries
 *     (Users, Teams). Phase 4 PR #14 will append DT/Scans/Disk/Audit/Health.
 *   - 48px top header with app name + LanguageToggle + signed-in email +
 *     sign-out.
 *   - Body slot (`<Outlet />`) for the nested route.
 *
 * Existence-hide guard: when the authenticated user is *not* a super-admin,
 * render the 404 shell instead of the layout. Anonymous visitors are filtered
 * out by the parent `<RequireAuth>` so we don't have to handle them here.
 *
 * No global app sidebar elsewhere — the rest of the portal keeps its current
 * shape. This layout is local to `/admin/*`.
 */
import { Building2, LogOut, Users as UsersIcon } from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { LanguageToggle } from "@/components/LanguageToggle";
import { Button } from "@/components/ui/button";
import { AdminNotFound } from "@/features/admin/AdminNotFound";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";

interface NavItem {
  to: string;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  testId: string;
}

const NAV_ITEMS: NavItem[] = [
  {
    to: "/admin/users",
    labelKey: "nav.admin.users",
    icon: UsersIcon,
    testId: "admin-nav-users",
  },
  {
    to: "/admin/teams",
    labelKey: "nav.admin.teams",
    icon: Building2,
    testId: "admin-nav-teams",
  },
];

export function AdminLayout() {
  const { t } = useTranslation("admin");
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const isSuperAdmin =
    user?.isSuperuser === true || user?.role === "super_admin";

  if (!isSuperAdmin) {
    return <AdminNotFound />;
  }

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div
      className="flex min-h-screen bg-background text-foreground"
      data-testid="admin-layout"
    >
      <aside
        className="flex shrink-0 flex-col border-r bg-card"
        style={{ width: "var(--layout-sidebar)" }}
        data-testid="admin-sidebar"
      >
        <div
          className="flex items-center border-b px-4 text-sm font-semibold tracking-tight"
          style={{ height: "var(--layout-header)" }}
        >
          {t("admin.layout.title")}
        </div>
        <nav className="flex-1 px-2 py-3" aria-label={t("admin.layout.title")}>
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    data-testid={item.testId}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                        "hover:bg-accent hover:text-accent-foreground",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                        isActive
                          ? "bg-primary/10 text-primary"
                          : "text-foreground",
                      )
                    }
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                    <span>{t(item.labelKey)}</span>
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex shrink-0 items-center justify-between border-b px-6"
          style={{ height: "var(--layout-header)" }}
          data-testid="admin-header"
        >
          <div className="flex items-baseline gap-3 text-sm">
            <span className="font-semibold tracking-tight">{t("app.name")}</span>
          </div>
          <div className="flex items-center gap-3">
            <span
              className="hidden text-xs text-muted-foreground sm:inline"
              data-testid="admin-signed-in-as"
            >
              {t("admin.layout.signed_in_as", { email: user?.email ?? "" })}
            </span>
            <LanguageToggle />
            <Button
              variant="outline"
              size="sm"
              onClick={handleLogout}
              data-testid="admin-logout"
            >
              <LogOut className="h-4 w-4" aria-hidden />
              <span>{t("auth.logout", { defaultValue: "Sign out" })}</span>
            </Button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto" data-testid="admin-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
