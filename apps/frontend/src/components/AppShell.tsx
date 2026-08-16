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
  BookOpen,
  DatabaseBackup,
  ExternalLink,
  Keyboard,
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
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import {
  ShortcutHelpDialog,
  useShortcutHelpShortcut,
} from "@/components/ShortcutHelpDialog";
import { formatBadge } from "@/lib/badgeCount";
import { docsUrl } from "@/lib/docsUrl";
import { deriveInitials } from "@/lib/initials";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import { useActiveTeam } from "@/hooks/useActiveTeam";
import { useNavBadges, type NavBadgeKey } from "@/hooks/useNavBadges";
import { useUIStore } from "@/stores/uiStore";

interface NavItem {
  to: string;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  testId: string;
  /** Use exact matching so a prefix route ("/") isn't always-active. */
  end?: boolean;
  /**
   * C1: which live count this row carries, if any. Only the rows that mean
   * work waiting on a person get one - a number beside every destination
   * would be noise, and the eye stops reading badges that are always there.
   */
  badge?: NavBadgeKey;
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
        badge: "scans",
      },
      {
        to: "/approvals",
        labelKey: "nav.approvals",
        icon: ClipboardCheck,
        testId: "nav-approvals",
        badge: "approvals",
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
    // C1: the route, the page and both translations already existed; only
    // the nav entry was missing, so restoring a backup meant knowing the
    // URL. There is an e2e spec for it, which is how a live feature went
    // unreachable without anything failing.
    to: "/admin/backup",
    labelKey: "nav.admin.backup",
    icon: DatabaseBackup,
    testId: "nav-admin-backup",
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
  badgeCount,
}: {
  item: NavItem;
  ns: string;
  /** Icon-only rail mode — hide the text label, surface it via aria/title. */
  collapsed?: boolean;
  /** Fired after a nav click — used by the mobile drawer to close itself. */
  onNavigate?: () => void;
  /** C1: live count for this row. Undefined means "not known yet". */
  badgeCount?: number;
}) {
  const { t } = useTranslation(ns);
  const Icon = item.icon;
  const label = t(item.labelKey);
  // Hidden at zero as well as when unknown: a row that always carries a "0"
  // trains the eye to skip it, and then the 1 goes unread too.
  const badge = typeof badgeCount === "number" ? formatBadge(badgeCount) : "";
  const showBadge = badge !== "";
  // The pill alone reads as a bare number next to a word. Folding it into the
  // accessible name is also what makes it survive the collapsed rail, where
  // the visible label is gone. Keeps the visible label as a prefix, so the
  // accessible name still contains it (WCAG 2.5.3).
  const badgeLabel =
    showBadge && item.badge
      ? `${label}, ${t(`nav.badge.${item.badge}`, { count: badgeCount })}`
      : undefined;
  const accessibleName = badgeLabel ?? (collapsed ? label : undefined);
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
        aria-label={accessibleName}
        title={collapsed ? (badgeLabel ?? label) : undefined}
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
            {showBadge ? (
              <span
                data-testid={`${item.testId}-badge`}
                // aria-hidden: the count is already in the link's accessible
                // name, and a screen reader reading "Approvals 4 4" is worse
                // than one reading it once.
                aria-hidden
                className={cn(
                  "inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold leading-none",
                  // Neutral, not the Critical red the bell uses: work in a
                  // queue is normal, and a sidebar with two permanent red
                  // dots would spend the alarm colour on the resting state.
                  "bg-muted text-muted-foreground",
                  collapsed
                    ? "absolute right-1 top-1"
                    : "ml-auto",
                )}
              >
                {badge}
              </span>
            ) : null}
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
  const badges = useNavBadges();
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
                  badgeCount={item.badge ? badges[item.badge] : undefined}
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
  onOpenShortcutHelp,
  onLogout,
}: {
  onOpenMobileNav: () => void;
  onOpenCommandMenu: () => void;
  onOpenShortcutHelp: () => void;
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

      {/* `min-w-0` + `ml-auto` on the group. The search trigger stays hidden
          below `sm` because it is the widest control and the shortcut still
          reaches it; theme and language used to be hidden the same way, with
          no way to reach them at all on a phone. They live in the profile
          menu now, which is one button wide at every width. */}
      <div className="ml-auto flex min-w-0 items-center gap-1">
        <CommandMenuTrigger
          onOpen={onOpenCommandMenu}
          onInk
          className="hidden sm:inline-flex"
        />
        <HeaderBell onInk />
        <ProfileMenu
          initials={initials}
          onLogout={onLogout}
          onOpenShortcutHelp={onOpenShortcutHelp}
        />
      </div>
    </header>
  );
}

/**
 * The account menu (C1).
 *
 * Before this the bar carried five separate controls on the right, and the
 * two it could not fit on a phone were simply dropped: below 640px there was
 * no way to change theme or language at all. Folding them into one menu is
 * what makes the bar fit and the settings reachable at the same time.
 *
 * Sign-out stays here rather than beside it. It was the app's only sign-out
 * and it sat one careless click from the avatar; behind a menu it needs an
 * intent.
 */
function ProfileMenu({
  initials,
  onLogout,
  onOpenShortcutHelp,
}: {
  initials: string;
  onLogout: () => void;
  onOpenShortcutHelp: () => void;
}) {
  const { t } = useTranslation();
  const user = useAuthStore((s) => s.user);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="text-topbar-foreground hover:bg-topbar-accent hover:text-topbar-foreground"
          data-testid="header-profile-menu"
          aria-label={t("auth.profile")}
        >
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
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        {/* Who you are signed in as. The bar shows initials at best, and on a
            deployment where several people share a workstation that is not
            enough to answer the question. */}
        <DropdownMenuLabel className="font-normal">
          <span className="block truncate text-sm font-medium">
            {user?.displayName || user?.email}
          </span>
          {user?.displayName ? (
            <span className="block truncate text-xs text-muted-foreground">
              {user.email}
            </span>
          ) : null}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        <DropdownMenuItem asChild data-testid="header-profile-link">
          <NavLink to="/profile">
            <UserCircle2 className="h-4 w-4" aria-hidden />
            {t("auth.profile")}
          </NavLink>
        </DropdownMenuItem>

        <DropdownMenuItem asChild data-testid="header-docs-link">
          {/* `noreferrer` alongside `noopener`: the docs site is ours, but a
              deploy can repoint this at a mirror we do not control. */}
          <a href={docsUrl()} target="_blank" rel="noopener noreferrer">
            <BookOpen className="h-4 w-4" aria-hidden />
            {t("nav.documentation")}
            <ExternalLink className="ml-auto h-3 w-3 opacity-60" aria-hidden />
          </a>
        </DropdownMenuItem>

        <DropdownMenuItem
          onSelect={onOpenShortcutHelp}
          data-testid="header-shortcuts-link"
        >
          <Keyboard className="h-4 w-4" aria-hidden />
          {t("shortcuts.title")}
          <kbd className="ml-auto rounded border px-1 font-mono text-[10px]">
            ?
          </kbd>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        {/* The two controls that had no home below `sm`. They keep their own
            components so the cycling logic and the labels stay in one place. */}
        <div className="flex items-center justify-between gap-2 px-2 py-1.5">
          <span className="text-sm">{t("theme.label")}</span>
          <ThemeToggle />
        </div>
        <div className="flex items-center justify-between gap-2 px-2 py-1.5">
          <span className="text-sm">{t("language.label")}</span>
          <LanguageToggle />
        </div>

        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onLogout} data-testid="logout-button">
          <LogOut className="h-4 w-4" aria-hidden />
          {t("auth.logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
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

  // C1: the same shape, for the sheet that lists both of these. Mounted
  // here so `?` works from any authenticated route.
  const { open: shortcutHelpOpen, setOpen: setShortcutHelpOpen } =
    useShortcutHelpShortcut();

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div
      className="flex h-screen flex-col bg-background text-foreground"
      data-testid="app-shell"
    >
      {/* C1: the first thing Tab reaches, on every screen.
          Without it a keyboard reader crosses the whole bar and every nav
          item before reaching the page they asked for, on every navigation.
          Visually hidden until focused, which is the only way it can be both
          out of the way and reachable. */}
      <a
        href="#main-content"
        className={cn(
          "sr-only rounded-md bg-background px-4 py-2 text-sm font-medium",
          "focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50",
          "focus:ring-2 focus:ring-ring focus:ring-offset-2",
        )}
        data-testid="skip-to-content"
      >
        {t("nav.skipToContent")}
      </a>

      {/* The bar spans the full width, above the sidebar rather than beside
          it. That single move is what separates a console silhouette from a
          single-scan viewer's. */}
      <GlobalBar
        onOpenMobileNav={() => setMobileNavOpen(true)}
        onOpenCommandMenu={() => setCommandOpen(true)}
        onOpenShortcutHelp={() => setShortcutHelpOpen(true)}
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
            id="main-content"
            // Focusable only as a skip target: without this the browser moves
            // the scroll but leaves focus in the bar, so the next Tab goes
            // back to where the reader just skipped from.
            tabIndex={-1}
            className="flex-1 overflow-y-auto animate-in fade-in-0 duration-slow ease-out-soft focus:outline-none"
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
      <ShortcutHelpDialog
        open={shortcutHelpOpen}
        onOpenChange={setShortcutHelpOpen}
      />
    </div>
  );
}
