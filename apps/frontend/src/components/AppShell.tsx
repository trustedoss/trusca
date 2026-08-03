// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
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
  ChevronDown,
  Activity,
  Building2,
  ClipboardList,
  HardDrive,
  Info,
  ListChecks,
  Users as UsersIcon,
  Boxes,
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
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { deriveInitials } from "@/lib/initials";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import { useActiveTeam } from "@/hooks/useActiveTeam";
import { useUIStore } from "@/stores/uiStore";

interface NavItem {
  to: string;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  testId: string;
  /** Use exact matching so a prefix route ("/") isn't always-active. */
  end?: boolean;
}

/**
 * Grouped navigation (W14).
 *
 * The flat list did not say what kind of place each destination was. Under
 * headings, the sidebar states the product's shape on sight: a portfolio you
 * survey, operations you run, and administration. That reading is the thing
 * a single-scan tool cannot borrow — its left rail is the table of contents
 * for one result.
 */
interface NavGroup {
  labelKey: string;
  items: NavItem[];
}

const MAIN_NAV: NavGroup[] = [
  {
    labelKey: "nav.group.portfolio",
    items: [
      {
        // W9-#50 — dedicated Dashboard at "/". `end` keeps the active state
        // from spilling onto every /projects/* descendant route.
        to: "/",
        labelKey: "nav.dashboard",
        icon: LayoutDashboard,
        testId: "nav-dashboard",
        end: true,
      },
      {
        to: "/components",
        labelKey: "nav.components",
        icon: Boxes,
        testId: "nav-components",
      },
      {
        to: "/projects",
        labelKey: "nav.projects",
        icon: FolderOpen,
        testId: "nav-projects",
      },
    ],
  },
  {
    labelKey: "nav.group.operations",
    items: [
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
    ],
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

// Not part of MAIN_NAV or ADMIN_NAV: it belongs to no group and must not be
// behind the super-admin gate. Rendered on its own below the divider.
const ABOUT_NAV_ITEM: NavItem = {
  to: "/about",
  labelKey: "nav.about",
  icon: Info,
  testId: "nav-about",
};

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
            "relative flex items-center rounded-md py-2 text-sm font-medium transition-colors duration-fast ease-out-soft",
            collapsed ? "justify-center px-2" : "gap-2 px-3",
            "hover:bg-accent hover:text-accent-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            // W14 — the active row is the brand's one job in the sidebar: a
            // teal indicator bar plus a tinted ground. The label stays ink
            // rather than teal, so legibility never depends on the accent
            // and colour is not the only thing marking the row.
            isActive
              ? "bg-brand-subtle text-foreground before:absolute before:inset-y-1 before:left-0 before:w-0.5 before:rounded-full before:bg-brand"
              : "text-foreground",
          )
        }
      >
        {({ isActive }) => (
          <>
            <Icon
              className={cn(
                "h-4 w-4 shrink-0 transition-colors duration-fast ease-out-soft",
                isActive ? "text-brand" : undefined,
              )}
              aria-hidden
            />
            {collapsed ? null : <span>{label}</span>}
          </>
        )}
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
      <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label={t("app.name")}>
        {MAIN_NAV.map((group, index) => (
          <div key={group.labelKey} className={index > 0 ? "mt-5" : undefined}>
            {collapsed ? (
              // No room for a heading on the rail; a divider keeps the
              // grouping legible without the label.
              index > 0 ? (
                <div className="mx-2 mb-2 border-t" role="separator" />
              ) : null
            ) : (
              <div className="mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t(group.labelKey)}
              </div>
            )}
            <ul className="space-y-1">
              {group.items.map((item) => (
                <NavItemLink
                  key={item.to}
                  item={item}
                  ns="common"
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </ul>
          </div>
        ))}

        {isSuperAdmin ? (
          <>
            {collapsed ? (
              // No room for the section label on the rail — a divider keeps
              // the admin links visually grouped.
              <div className="my-2 border-t" role="separator" />
            ) : (
              <div className="mt-5 mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
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

        {/* About sits below every group and outside the admin gate: license
            notices are something every user of a deployment is entitled to
            read, not an administration task. Separated by a divider in both
            states rather than given a section label of its own — one item does
            not need a heading. */}
        <div className="mx-2 mt-5 mb-2 border-t" role="separator" />
        <ul className="space-y-1">
          <NavItemLink
            item={ABOUT_NAV_ITEM}
            ns="common"
            collapsed={collapsed}
            onNavigate={onNavigate}
          />
        </ul>
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

/**
 * Organisation and team context (W14).
 *
 * Sits in the bar so the answer to "whose data am I looking at" is always
 * on screen. Users in one team see a plain label; multi-team users get a
 * menu. Switching moves the store's active team — which project creation
 * and the label follow — and deliberately does not filter any screen. A
 * control that quietly narrowed the dashboard would be a trap, not a
 * shortcut; scoping a view is a decision a screen should ask for out loud.
 */
function TeamSwitcher() {
  const { t } = useTranslation();
  const teams = useAuthStore((s) => s.user?.teams) ?? [];
  const setActiveTeamId = useUIStore((s) => s.setActiveTeamId);
  const active = useActiveTeam();

  if (!active) return null;

  if (teams.length < 2) {
    return (
      <span
        className="hidden max-w-[14rem] truncate rounded-md bg-topbar-accent px-2 py-1 text-xs text-topbar-muted-foreground sm:inline-block"
        data-testid="topbar-team"
        title={t("auth.active_team", { team: active.name })}
      >
        {active.name}
      </span>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="hidden max-w-[16rem] gap-1 bg-topbar-accent text-xs text-topbar-muted-foreground hover:bg-topbar-accent hover:text-topbar-foreground sm:inline-flex"
          data-testid="topbar-team-switcher"
          aria-label={t("auth.active_team", { team: active.name })}
        >
          <span className="truncate">{active.name}</span>
          <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      {/* Radio semantics, not plain menu items: the check glyph marking the
          current team is aria-hidden, so without `aria-checked` a screen
          reader announces every team identically and the user cannot tell
          which one they are acting as. axe cannot flag that — a named
          menuitem inside a menu is structurally valid. */}
      <DropdownMenuContent align="start" className="min-w-[12rem]">
        <DropdownMenuLabel>{t("nav.switchTeam")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={active.id}
          onValueChange={setActiveTeamId}
        >
          {teams.map((team) => (
            <DropdownMenuRadioItem
              key={team.id}
              value={team.id}
              data-testid={`topbar-team-option-${team.id}`}
            >
              <span className="truncate">{team.name}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Global bar (W14) — the shell's top edge, spanning the full width above
 * both the sidebar and the content.
 *
 * The silhouette is the point. A left rail that runs to the top of the
 * window is what a single-scan viewer looks like; a bar that owns the top
 * and puts the organisation, the team, and search above everything is what
 * a console looks like. It is also a shape a tool with no accounts and no
 * teams cannot copy, because it has nothing to put here.
 *
 * Ink surface in a light app, so it carries its own `topbar-*` foreground
 * scale (see index.css) rather than borrowing the page's.
 */
function GlobalBar({
  onOpenMobileNav,
  onOpenCommandMenu,
  onLogout,
}: {
  onOpenMobileNav: () => void;
  onOpenCommandMenu: () => void;
  onLogout: () => void;
}) {
  const { t } = useTranslation();
  const user = useAuthStore((s) => s.user);
  const initials = user ? deriveInitials(user.displayName || user.email) : "";

  return (
    <header
      className="flex shrink-0 items-center gap-3 border-b border-topbar-border bg-topbar px-4 text-topbar-foreground"
      style={{ height: "var(--layout-topbar)" }}
      data-testid="app-topbar"
    >
      <Button
        variant="ghost"
        size="icon"
        className="text-topbar-foreground hover:bg-topbar-accent hover:text-topbar-foreground lg:hidden"
        onClick={onOpenMobileNav}
        data-testid="sidebar-mobile-trigger"
        aria-label={t("nav.openMenu")}
      >
        <Menu className="h-4 w-4" aria-hidden />
      </Button>

      <NavLink
        to="/"
        className="flex shrink-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-on-ink focus-visible:ring-offset-2 focus-visible:ring-offset-topbar"
        data-testid="topbar-brand"
      >
        <BrandMark size={22} onInk />
        <span className="hidden text-sm font-semibold tracking-tight sm:inline">
          {t("app.name")}
        </span>
      </NavLink>

      <TeamSwitcher />

      {/* `min-w-0` + `ml-auto` on the group, and the two widest controls
          hidden below `sm`. Without this the bar could not fit a phone: the
          search trigger and the language button alone are ~190 px of
          non-shrinkable content, and the overflow pushed the sign-out button
          — the app's only one — off the right edge. The keyboard shortcut
          still opens search when its button is hidden. */}
      <div className="ml-auto flex min-w-0 items-center gap-1">
        <CommandMenuTrigger
          onOpen={onOpenCommandMenu}
          onInk
          className="hidden sm:inline-flex"
        />
        <HeaderBell onInk />
        <ThemeToggle onInk className="hidden sm:inline-flex" />
        <LanguageToggle onInk className="hidden sm:inline-flex" />
        <Button
          variant="ghost"
          size="sm"
          asChild
          className="text-topbar-foreground hover:bg-topbar-accent hover:text-topbar-foreground"
          data-testid="header-profile-link"
        >
          <NavLink to="/profile" aria-label={t("auth.profile")}>
            {initials ? (
              <span
                aria-hidden
                data-testid="header-avatar"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-topbar-accent text-xs font-medium text-topbar-foreground"
              >
                {initials}
              </span>
            ) : (
              <UserCircle2 className="h-4 w-4" aria-hidden />
            )}
            <span className="sr-only">{t("auth.profile")}</span>
          </NavLink>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onLogout}
          className="text-topbar-muted-foreground hover:bg-topbar-accent hover:text-topbar-foreground"
          data-testid="logout-button"
        >
          <LogOut className="h-4 w-4" aria-hidden />
          <span className="sr-only sm:not-sr-only">{t("auth.logout")}</span>
        </Button>
      </div>
    </header>
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
      className="flex h-screen flex-col bg-background text-foreground"
      data-testid="app-shell"
    >
      {/* The bar spans the full width, above the sidebar rather than beside
          it. That single move is what separates a console silhouette from a
          single-scan viewer's. */}
      <GlobalBar
        onOpenMobileNav={() => setMobileNavOpen(true)}
        onOpenCommandMenu={() => setCommandOpen(true)}
        onLogout={handleLogout}
      />

      <div className="flex min-h-0 flex-1">
        {/* Desktop sidebar. Hidden below `lg` (1024 px), where the mobile
            drawer takes over. Width animates between the full rail and the
            64 px icon rail; `data-collapsed` lets the test harness assert
            the state without measuring pixels. */}
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
          {/* v2.1 B5: read-only live-demo banner. Renders only when the
              backend reports demo_read_only (useDemoMode), so a normal
              deploy is unaffected. */}
          <DemoBanner />

          <main
            key={location.pathname}
            className="flex-1 overflow-y-auto animate-in fade-in-0 duration-slow ease-out-soft"
            data-testid="app-main"
          >
            <Outlet />
          </main>
        </div>
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
