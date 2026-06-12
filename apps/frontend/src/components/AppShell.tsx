import {
  ClipboardCheck,
  FolderOpen,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  PanelLeft,
  PanelLeftClose,
  Scale,
  ScanLine,
  UserCircle2,
  Activity,
  Building2,
  ClipboardList,
  HardDrive,
  ListChecks,
  Users as UsersIcon,
} from "lucide-react";
import { useState, type ComponentType, type SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import {
  CommandMenu,
  CommandMenuTrigger,
  useCommandMenuShortcut,
} from "@/components/CommandMenu";
import { BrandMark } from "@/components/BrandMark";
import { DemoBanner } from "@/components/DemoBanner";
import { HeaderBell } from "@/components/HeaderBell";
import { LanguageToggle } from "@/components/LanguageToggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { deriveInitials } from "@/lib/initials";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

interface NavItem {
  to: string;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  testId: string;
  /** Use exact matching so a prefix route ("/") isn't always-active. */
  end?: boolean;
}

const MAIN_NAV: NavItem[] = [
  {
    // W9-#50 — dedicated Dashboard at "/". Lives above Projects so it's the
    // first landing point for authenticated users, matching the BD / Snyk /
    // Datadog / Mend convention of opening on a portfolio overview rather
    // than a list view. `end` keeps the active state from spilling onto every
    // /projects/* descendant route.
    to: "/",
    labelKey: "nav.dashboard",
    icon: LayoutDashboard,
    testId: "nav-dashboard",
    end: true,
  },
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
  {
    to: "/policies",
    labelKey: "nav.policies",
    icon: Scale,
    testId: "nav-policies",
  },
  {
    to: "/integrations",
    labelKey: "nav.integrations",
    icon: KeyRound,
    testId: "nav-integrations",
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

function NavItemLink({
  item,
  ns,
  collapsed,
  onNavigate,
}: {
  item: NavItem;
  ns: string;
  /** Icon-only rail mode — hide the text label, surface it via aria/title. */
  collapsed?: boolean;
  /** Fired after a nav click — used by the mobile drawer to close itself. */
  onNavigate?: () => void;
}) {
  const { t } = useTranslation(ns);
  const Icon = item.icon;
  const label = t(item.labelKey);
  return (
    <li>
      <NavLink
        to={item.to}
        end={item.end}
        data-testid={item.testId}
        onClick={onNavigate}
        // In the collapsed rail the visible label is gone, so the accessible
        // name has to come from aria-label; `title` gives sighted mouse users
        // a native hover tooltip without pulling in a tooltip dependency.
        aria-label={collapsed ? label : undefined}
        title={collapsed ? label : undefined}
        className={({ isActive }) =>
          cn(
            // W11-F polish — sidebar nav hover/active transitions land on the
            // W11-A 150 ms ease-out-soft tokens for parity with every other
            // hoverable affordance (buttons, dropdown items, tabs).
            "flex items-center rounded-md py-2 text-sm font-medium transition-colors duration-fast ease-out-soft",
            collapsed ? "justify-center px-2" : "gap-2 px-3",
            "hover:bg-accent hover:text-accent-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            isActive ? "bg-primary/10 text-primary" : "text-foreground",
          )
        }
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        {collapsed ? null : <span>{label}</span>}
      </NavLink>
    </li>
  );
}

/**
 * Sidebar body — brand mark, nav lists, and (desktop only) the collapse
 * toggle. Shared verbatim between the fixed desktop `<aside>` and the mobile
 * `<Sheet>` drawer so the two surfaces never drift apart.
 */
function SidebarNav({
  collapsed,
  isSuperAdmin,
  onNavigate,
  onCollapseToggle,
}: {
  collapsed: boolean;
  isSuperAdmin: boolean;
  /** Mobile drawer: close on navigate. Omitted on desktop. */
  onNavigate?: () => void;
  /** Desktop: toggle the icon-rail. Omitted in the mobile drawer. */
  onCollapseToggle?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <div
        className={cn(
          "flex items-center border-b text-sm font-semibold tracking-tight",
          collapsed ? "justify-center px-2" : "px-4",
        )}
        style={{ height: "var(--layout-header)" }}
      >
        {collapsed ? (
          <>
            <BrandMark size={22} />
            <span className="sr-only">{t("app.name")}</span>
          </>
        ) : (
          <span className="flex items-center gap-2">
            <BrandMark size={20} />
            {t("app.name")}
          </span>
        )}
      </div>

      <nav className="flex-1 px-2 py-3" aria-label={t("app.name")}>
        <ul className="space-y-1">
          {MAIN_NAV.map((item) => (
            <NavItemLink
              key={item.to}
              item={item}
              ns="common"
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          ))}
        </ul>

        {isSuperAdmin ? (
          <>
            {collapsed ? (
              // No room for the section label on the rail — a divider keeps
              // the admin links visually grouped.
              <div className="my-2 border-t" role="separator" />
            ) : (
              <div className="mt-4 mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("nav.admin.section")}
              </div>
            )}
            <ul className="space-y-1">
              {ADMIN_NAV.map((item) => (
                <NavItemLink
                  key={item.to}
                  item={item}
                  ns="admin"
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </ul>
          </>
        ) : null}
      </nav>

      {onCollapseToggle ? (
        <div className="border-t p-2">
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "w-full",
              collapsed ? "px-0" : "justify-start gap-2 px-3",
            )}
            onClick={onCollapseToggle}
            data-testid="sidebar-collapse-toggle"
            aria-label={
              collapsed ? t("nav.expandSidebar") : t("nav.collapseSidebar")
            }
            title={collapsed ? t("nav.expandSidebar") : t("nav.collapseSidebar")}
          >
            {collapsed ? (
              <PanelLeft className="h-4 w-4 shrink-0" aria-hidden />
            ) : (
              <>
                <PanelLeftClose className="h-4 w-4 shrink-0" aria-hidden />
                <span className="text-sm font-medium">
                  {t("nav.collapseSidebar")}
                </span>
              </>
            )}
          </Button>
        </div>
      ) : null}
    </>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  // W12-C — route-change entrance. Keying <main> on the pathname remounts the
  // routed subtree on navigation so the fade-in replays; search-param changes
  // (tabs, filters) keep the same pathname and therefore do NOT re-animate.
  const location = useLocation();
  const toggleSidebarCollapsed = useUIStore((s) => s.toggleSidebarCollapsed);

  // The mobile drawer is ephemeral — it must reset on reload and on navigate,
  // so it stays local state instead of going through the persisted uiStore.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const isSuperAdmin =
    user?.isSuperuser === true || user?.role === "super_admin";

  // M-17 — header identity. Initials come from displayName (full_name with
  // an email fallback, mapped in lib/api.ts). The "active team" concept is
  // the store's default `teamId` (first membership, oldest-first); for
  // multi-team users we surface that default and deliberately do NOT offer
  // switching here (out of scope). No team / still bootstrapping → omit the
  // label entirely instead of rendering a placeholder.
  const initials = user ? deriveInitials(user.displayName || user.email) : "";
  const teams = user?.teams ?? [];
  const activeTeamName =
    teams.find((team) => team.id === user?.teamId)?.name ??
    teams[0]?.name ??
    null;

  // W9-#54 — global ⌘K palette. The hook owns the keyboard listener so
  // the shortcut is reachable from any authenticated route, even when the
  // header trigger affordance is off-screen on a narrow viewport.
  const { open: commandOpen, setOpen: setCommandOpen } = useCommandMenuShortcut();

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div
      className="flex min-h-screen bg-background text-foreground"
      data-testid="app-shell"
    >
      {/* Desktop sidebar. Hidden below `lg` (1024 px), where the mobile
          drawer takes over. Width animates between the full rail and the
          64 px icon rail; `data-collapsed` lets the test harness assert the
          state without measuring pixels. */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-r bg-card lg:flex",
          "transition-[width] duration-slow ease-out-soft",
          sidebarCollapsed
            ? "w-[var(--layout-sidebar-collapsed)]"
            : "w-sidebar",
        )}
        data-testid="app-sidebar"
        data-collapsed={sidebarCollapsed}
      >
        <SidebarNav
          collapsed={sidebarCollapsed}
          isSuperAdmin={isSuperAdmin}
          onCollapseToggle={toggleSidebarCollapsed}
        />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* v2.1 B5: read-only live-demo banner. Renders only when the backend
            reports demo_read_only (useDemoMode), so a normal deploy is unaffected. */}
        <DemoBanner />
        <header
          className="flex shrink-0 items-center justify-between border-b px-6"
          style={{ height: "var(--layout-header)" }}
          data-testid="app-header"
        >
          {/* Mobile-only hamburger — opens the nav drawer. On `lg`+ it's
              removed from layout and the fixed sidebar carries navigation, so
              the right-side actions justify-between to the edge as before. */}
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileNavOpen(true)}
            data-testid="sidebar-mobile-trigger"
            aria-label={t("nav.openMenu")}
          >
            <Menu className="h-4 w-4" aria-hidden />
          </Button>
          <div className="flex items-center gap-3">
            {/* Global ⌘K palette trigger (W9-#54). The button is a
                discoverability affordance — the keyboard shortcut works
                whether or not this button is on screen. */}
            <CommandMenuTrigger onOpen={() => setCommandOpen(true)} />
            {/* Notification bell — sole entry point to /notifications. We
                deliberately do NOT add a sidebar nav entry to keep the
                left rail focused on top-level domains; chore A2 design. */}
            <HeaderBell />
            <LanguageToggle />
            {/* M-17 — initials avatar + active team. Keeps the existing
                NavLink-to-/profile behavior and the `header-profile-link`
                testid (ProfileHarness + docs-uat depend on it); only the
                visual content changes from icon+"Profile" to monogram+team. */}
            <Button
              variant="ghost"
              size="sm"
              asChild
              data-testid="header-profile-link"
            >
              <NavLink to="/profile" aria-label={t("auth.profile")}>
                {initials ? (
                  <span
                    aria-hidden
                    data-testid="header-avatar"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground"
                  >
                    {initials}
                  </span>
                ) : (
                  <UserCircle2 className="h-4 w-4" aria-hidden />
                )}
                {activeTeamName ? (
                  <span
                    data-testid="header-active-team"
                    className="max-w-[10rem] truncate text-xs text-muted-foreground"
                    aria-label={t("auth.active_team", { team: activeTeamName })}
                    title={t("auth.active_team", { team: activeTeamName })}
                  >
                    {activeTeamName}
                  </span>
                ) : null}
                <span className="sr-only">{t("auth.profile")}</span>
              </NavLink>
            </Button>
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

        <main
          key={location.pathname}
          className="flex-1 overflow-y-auto animate-in fade-in-0 duration-slow ease-out-soft"
          data-testid="app-main"
        >
          <Outlet />
        </main>
      </div>

      {/* Mobile navigation drawer (<lg). Always shows the full-label sidebar
          (never the collapsed rail) and closes on navigate / overlay / ESC
          via Radix Dialog semantics inherited from Sheet. */}
      <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <SheetContent
          side="left"
          className="flex w-64 flex-col p-0"
          data-testid="mobile-nav-drawer"
        >
          <SheetTitle className="sr-only">{t("app.name")}</SheetTitle>
          <SidebarNav
            collapsed={false}
            isSuperAdmin={isSuperAdmin}
            onNavigate={() => setMobileNavOpen(false)}
          />
        </SheetContent>
      </Sheet>

      {/* W9-#54 — global command palette. Mounted once at the AppShell
          level so the ⌘K shortcut is reachable from every authenticated
          route. The dialog itself is portal-rendered to document.body, so
          this position in the DOM is purely organizational. */}
      <CommandMenu open={commandOpen} onOpenChange={setCommandOpen} />
    </div>
  );
}
