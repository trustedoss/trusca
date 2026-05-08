import {
  ClipboardCheck,
  FolderOpen,
  LogOut,
  ScanLine,
  Activity,
  Building2,
  ClipboardList,
  HardDrive,
  ListChecks,
  Network,
  Users as UsersIcon,
} from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { LanguageToggle } from "@/components/LanguageToggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";

interface NavItem {
  to: string;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  testId: string;
}

const MAIN_NAV: NavItem[] = [
  {
    to: "/projects",
    labelKey: "nav.projects",
    icon: FolderOpen,
    testId: "nav-projects",
  },
  {
    to: "/scans",
    labelKey: "nav.scans",
    icon: ScanLine,
    testId: "nav-scans",
  },
  {
    to: "/approvals",
    labelKey: "nav.approvals",
    icon: ClipboardCheck,
    testId: "nav-approvals",
  },
];

const ADMIN_NAV: NavItem[] = [
  {
    to: "/admin/users",
    labelKey: "nav.admin.users",
    icon: UsersIcon,
    testId: "nav-admin-users",
  },
  {
    to: "/admin/teams",
    labelKey: "nav.admin.teams",
    icon: Building2,
    testId: "nav-admin-teams",
  },
  {
    to: "/admin/dt",
    labelKey: "nav.admin.dt",
    icon: Network,
    testId: "nav-admin-dt",
  },
  {
    to: "/admin/scans",
    labelKey: "nav.admin.scans",
    icon: ListChecks,
    testId: "nav-admin-scans",
  },
  {
    to: "/admin/disk",
    labelKey: "nav.admin.disk",
    icon: HardDrive,
    testId: "nav-admin-disk",
  },
  {
    to: "/admin/audit",
    labelKey: "nav.admin.audit",
    icon: ClipboardList,
    testId: "nav-admin-audit",
  },
  {
    to: "/admin/health",
    labelKey: "nav.admin.health",
    icon: Activity,
    testId: "nav-admin-health",
  },
];

function NavItemLink({ item, ns }: { item: NavItem; ns: string }) {
  const { t } = useTranslation(ns);
  const Icon = item.icon;
  return (
    <li>
      <NavLink
        to={item.to}
        data-testid={item.testId}
        className={({ isActive }) =>
          cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
            "hover:bg-accent hover:text-accent-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            isActive ? "bg-primary/10 text-primary" : "text-foreground",
          )
        }
      >
        <Icon className="h-4 w-4" aria-hidden />
        <span>{t(item.labelKey)}</span>
      </NavLink>
    </li>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const isSuperAdmin =
    user?.isSuperuser === true || user?.role === "super_admin";

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div
      className="flex min-h-screen bg-background text-foreground"
      data-testid="app-shell"
    >
      <aside
        className="flex shrink-0 flex-col border-r bg-card"
        style={{ width: "var(--layout-sidebar)" }}
        data-testid="app-sidebar"
      >
        <div
          className="flex items-center border-b px-4 text-sm font-semibold tracking-tight"
          style={{ height: "var(--layout-header)" }}
        >
          {t("app.name")}
        </div>
        <nav
          className="flex-1 px-2 py-3"
          aria-label={t("app.name")}
        >
          <ul className="space-y-1">
            {MAIN_NAV.map((item) => (
              <NavItemLink key={item.to} item={item} ns="common" />
            ))}
          </ul>

          {isSuperAdmin ? (
            <>
              <div className="mt-4 mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("nav.admin.section")}
              </div>
              <ul className="space-y-1">
                {ADMIN_NAV.map((item) => (
                  <NavItemLink key={item.to} item={item} ns="admin" />
                ))}
              </ul>
            </>
          ) : null}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex shrink-0 items-center justify-between border-b px-6"
          style={{ height: "var(--layout-header)" }}
          data-testid="app-header"
        >
          <div className="flex items-baseline gap-3 text-sm">
            <span className="font-semibold tracking-tight">
              {t("app.name")}
            </span>
            <span className="text-xs text-muted-foreground">
              {t("app.version")}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <LanguageToggle />
            <Button
              variant="outline"
              size="sm"
              onClick={handleLogout}
              data-testid="logout-button"
            >
              <LogOut className="h-4 w-4" aria-hidden />
              <span>{t("auth.logout")}</span>
            </Button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto" data-testid="app-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
