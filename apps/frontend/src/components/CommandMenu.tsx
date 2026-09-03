// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * CommandMenu — global ⌘K palette.
 *
 * Why this exists:
 *   Cross-surface discoverability was the weak point: reaching a project, a
 *   component or a CVE meant knowing which screen owned it first. A global
 *   ⌘K palette collapses that into one keystroke from anywhere.
 *
 * Scope:
 *   - Four search categories: Projects (live API) + Components + CVEs (live
 *     cross-project search, BomLens parity Phase H-2) + Pages (static nav
 *     jumps).
 *   - Components/CVEs hit `GET /v1/search?q=&kinds=components,vulnerabilities`
 *     (team-scoped by the backend, ≤ 20 hits per category). They only fire
 *     once the debounced query is ≥ 3 chars; below that the palette behaves
 *     like before (projects + pages only). Selecting a hit deep-links into the
 *     owning project's Components / Vulnerabilities tab pre-filtered by the
 *     component name / CVE id.
 *   - 200ms debounce on the API calls to avoid request fan-out.
 *   - Admin pages are listed only for super-admin users (role-gated, matches
 *     the AppShell sidebar gating).
 *
 * Keyboard contract:
 *   - ⌘K (Mac) / Ctrl+K (Win/Linux) → toggle open.
 *   - Esc → close (provided by cmdk + Radix Dialog).
 *   - ↑/↓ → navigate; Enter → select (provided by cmdk).
 *
 * The component renders an open/close-controlled <CommandDialog>. Mounting
 * lives in <AppShell>, so the palette is reachable from every authenticated
 * route. The trigger button on the header is a discoverability affordance —
 * the shortcut works whether or not it's clicked.
 */
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  Building2,
  ClipboardCheck,
  ClipboardList,
  FolderOpen,
  HardDrive,
  KeyRound,
  ListChecks,
  Package,
  PackageSearch,
  Scale,
  ScanLine,
  Search as SearchIcon,
  ShieldAlert,
  Users as UsersIcon,
  type LucideIcon,
} from "lucide-react";
import {
  forwardRef,
  useEffect,
  useMemo,
  useState,
  type ButtonHTMLAttributes,
} from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { useDeploymentFeatures } from "@/features/about/api/useDeploymentFeatures";
import {
  COMPONENTS_SEARCH_PARAM,
  VULNERABILITIES_SEARCH_PARAM,
} from "@/features/projects/components/tabSearchParam";
import { listProjects, type ProjectPublic } from "@/lib/projectsApi";
import {
  globalSearch,
  type SearchComponentHit,
  type SearchVulnerabilityHit,
} from "@/lib/searchApi";
import { cn } from "@/lib/utils";
import { usePermissions } from "@/hooks/usePermissions";

// ---------------------------------------------------------------------------
// Cross-project search config (BomLens parity Phase H-2).
// ---------------------------------------------------------------------------

/**
 * Minimum debounced query length before the global-search endpoint fires.
 * Below this the palette shows only Projects + Pages, matching the pre-H-2
 * behavior and keeping the backend from doing prefix work on 1-2 char noise.
 * Must match the backend floor (`services.search_service.MIN_QUERY_LEN`) and
 * the full-search-page mirror (`SEARCH_MIN_CHARS` in
 * `features/search/api/useSearchResults.ts`): a lower value here would send
 * queries the backend just discards; a higher one would hide results the
 * backend is willing to return. Pinned by
 * `tests/unit/contracts/searchMinQueryLenContract.test.ts`.
 */
export const SEARCH_MIN_CHARS = 3;

/**
 * Map a backend severity token → the Tailwind risk-color token used for the
 * severity dot. Color is never the sole signal — the localized severity label
 * renders next to the dot (CLAUDE.md a11y: severity = color + label). Unknown
 * severities fall back to the muted (info) hue.
 */
const SEVERITY_DOT_CLASS: Record<string, string> = {
  critical: "bg-risk-critical",
  high: "bg-risk-high",
  medium: "bg-risk-medium",
  low: "bg-risk-low",
  info: "bg-risk-info",
  unknown: "bg-risk-info",
};

function severityDotClass(severity: string): string {
  return SEVERITY_DOT_CLASS[severity.toLowerCase()] ?? "bg-risk-info";
}

/**
 * The localized label for a wire severity. Falls back to the raw value when
 * the backend sends a bucket the `common:risk` scale does not name (`unknown`,
 * or anything added later), which reads better than a missing-key path.
 */
function severityLabel(severity: string, t: TFunction): string {
  const bucket = severity.toLowerCase();
  return bucket in SEVERITY_DOT_CLASS && bucket !== "unknown"
    ? t(`risk.${bucket}`)
    : severity;
}

// ---------------------------------------------------------------------------
// Static route catalog — mirrors the AppShell sidebar (kept in sync by hand;
// the sidebar's nav arrays are not exported, and hard-coding here is simpler
// than threading them through the store. Diff-checked at code review time.)
// ---------------------------------------------------------------------------

interface RouteEntry {
  to: string;
  labelKey: string; // i18n key including namespace prefix, e.g. "common:nav.projects"
  icon: LucideIcon;
  /** When true, only show for super-admin users. */
  adminOnly?: boolean;
}

const MAIN_ROUTES: RouteEntry[] = [
  { to: "/projects", labelKey: "common:nav.projects", icon: FolderOpen },
  { to: "/scans", labelKey: "common:nav.scans", icon: ScanLine },
  { to: "/approvals", labelKey: "common:nav.approvals", icon: ClipboardCheck },
  { to: "/policies", labelKey: "common:nav.policies", icon: Scale },
  { to: "/integrations", labelKey: "common:nav.integrations", icon: KeyRound },
  { to: "/notifications", labelKey: "common:nav.bell.aria", icon: Bell },
];

const ADMIN_ROUTES: RouteEntry[] = [
  {
    to: "/admin/users",
    labelKey: "admin:nav.admin.users",
    icon: UsersIcon,
    adminOnly: true,
  },
  {
    to: "/admin/teams",
    labelKey: "admin:nav.admin.teams",
    icon: Building2,
    adminOnly: true,
  },
  {
    to: "/admin/scans",
    labelKey: "admin:nav.admin.scans",
    icon: ListChecks,
    adminOnly: true,
  },
  {
    to: "/admin/disk",
    labelKey: "admin:nav.admin.disk",
    icon: HardDrive,
    adminOnly: true,
  },
  {
    to: "/admin/audit",
    labelKey: "admin:nav.admin.audit",
    icon: ClipboardList,
    adminOnly: true,
  },
  {
    to: "/admin/health",
    labelKey: "admin:nav.admin.health",
    icon: Activity,
    adminOnly: true,
  },
];

// ---------------------------------------------------------------------------
// Header trigger button — discoverability affordance.
// Renders the localized "Search..." label + "⌘K" shortcut hint. Clicking it
// opens the palette, but the keyboard shortcut works regardless of whether
// the button is rendered.
// ---------------------------------------------------------------------------

interface CommandMenuTriggerProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  onOpen: () => void;
  /** Rendered on the dark global bar — use the topbar surface scale. */
  onInk?: boolean;
}

export const CommandMenuTrigger = forwardRef<
  HTMLButtonElement,
  CommandMenuTriggerProps
>(({ onOpen, onInk = false, className, ...props }, ref) => {
  const { t } = useTranslation("common");
  return (
    <button
      ref={ref}
      type="button"
      onClick={onOpen}
      data-testid="command-menu-trigger"
      className={cn(
        "inline-flex h-8 items-center gap-2 rounded-md border px-3 text-xs transition-colors duration-fast ease-out-soft",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
        onInk
          ? "border-topbar-border bg-topbar-accent text-topbar-muted-foreground hover:text-topbar-foreground focus-visible:ring-brand-on-ink focus-visible:ring-offset-topbar"
          : "bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:ring-ring",
        className,
      )}
      aria-label={t("command_menu.trigger_button")}
      {...props}
    >
      <span>{t("command_menu.trigger_button")}</span>
      <kbd
        className={cn(
          "inline-flex h-5 select-none items-center gap-0.5 rounded border px-1.5 font-mono text-[10px] font-medium",
          onInk
            ? "border-topbar-border bg-topbar text-topbar-muted-foreground"
            : "bg-muted text-muted-foreground",
        )}
      >
        {t("command_menu.shortcut_hint")}
      </kbd>
    </button>
  );
});
CommandMenuTrigger.displayName = "CommandMenuTrigger";

// ---------------------------------------------------------------------------
// CommandMenu — the dialog itself. Self-controls open/close via the ⌘K
// shortcut + an imperative `open` prop pair. Mounted once in <AppShell>.
// ---------------------------------------------------------------------------

export interface CommandMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Hook returning [open, setOpen] plus a global ⌘K listener. Extracted so
 * AppShell can render both the menu (controlled) and the trigger button
 * sharing the same state, and so the test suite can drive open/close from
 * outside.
 */
export function useCommandMenuShortcut(): {
  open: boolean;
  setOpen: (open: boolean) => void;
} {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      // Mac uses metaKey; Win/Linux use ctrlKey. We accept either so the
      // shortcut works on any platform without sniffing navigator.platform.
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return { open, setOpen };
}

/**
 * Tiny debounce hook — 200ms by default. Returns the latest value AFTER the
 * timer elapses, so we don't fan out an API call on every keystroke.
 */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

export function CommandMenu({ open, onOpenChange }: CommandMenuProps) {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const { isSuperAdmin } = usePermissions();
  const features = useDeploymentFeatures();
  const packageLookupEnabled = features.external_package_lookup === true;

  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query.trim(), 200);

  // Reset the query each time the palette closes so the next open starts
  // empty rather than restoring the previous search.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  // Live projects search. We DO NOT pass `q` to the backend yet because the
  // existing `GET /v1/projects` already accepts a `q` filter (see
  // projectsApi.listProjects), but it's a substring match on the small
  // page-1 set. Sending `q` lets the backend's index do the work and keeps
  // the wire payload small even on tenants with thousands of projects.
  const projectsQuery = useQuery({
    queryKey: ["command-menu", "projects", debounced],
    queryFn: () => listProjects({ q: debounced || undefined, size: 10 }),
    // Keep the previous result visible while the next query loads so the
    // list doesn't flash empty between keystrokes.
    placeholderData: (previous) => previous,
    enabled: open, // don't fetch when the palette is closed
    staleTime: 30_000,
  });

  const projects: ProjectPublic[] = projectsQuery.data?.items ?? [];

  // Cross-project component/CVE search (BomLens parity Phase H-2). Fires only
  // once the debounced query clears SEARCH_MIN_CHARS so we don't fan out the
  // endpoint on 1-2 char noise; below the threshold the palette behaves like
  // the pre-H-2 version (projects + pages only).
  const searchEnabled = open && debounced.length >= SEARCH_MIN_CHARS;
  const searchQuery = useQuery({
    queryKey: ["command-menu", "search", debounced],
    queryFn: () => globalSearch(debounced),
    // Same anti-flash pattern as the projects query: keep the previous hits
    // visible while the next query resolves so the list doesn't blink empty.
    placeholderData: (previous) => previous,
    enabled: searchEnabled,
    staleTime: 30_000,
  });

  // Gate the rendered hits on `searchEnabled` too, so that dropping back below
  // the 2-char threshold immediately clears the Components/CVEs groups instead
  // of leaving stale placeholderData on screen.
  const componentHits: SearchComponentHit[] = searchEnabled
    ? (searchQuery.data?.components ?? [])
    : [];
  const vulnHits: SearchVulnerabilityHit[] = searchEnabled
    ? (searchQuery.data?.vulnerabilities ?? [])
    : [];
  const searchLoading =
    searchEnabled && searchQuery.isLoading && searchQuery.data == null;

  const visibleRoutes = useMemo(() => {
    const main = MAIN_ROUTES;
    const admin = isSuperAdmin ? ADMIN_ROUTES : [];
    return [...main, ...admin];
  }, [isSuperAdmin]);

  function handleSelectProject(project: ProjectPublic): void {
    onOpenChange(false);
    navigate(`/projects/${project.id}`);
  }

  function handleSelectRoute(route: RouteEntry): void {
    onOpenChange(false);
    navigate(route.to);
  }

  // The palette caps each category at 20 hits. Rather than let that ceiling be
  // silent, each group ends with a way through to the full search page, which
  // pages and facets the same query. It is a CommandItem rather than a footer
  // outside the list so ↑↓/Enter reach it like any other row.
  function handleSeeAll(kind: string): void {
    onOpenChange(false);
    navigate(`/search?kind=${kind}&q=${encodeURIComponent(debounced)}`);
  }

  // The search page is not in the sidebar — searching is an action, not a
  // place, and the three sidebar groups are places. That left the page
  // reachable only through `handleSeeAll` (needs hits, so needs a term) and a
  // saved search, so a user who had never typed anything could not find it at
  // all. This item is the standing door: it renders whether or not a term has
  // been typed, and carries the term along when there is one.
  function handleOpenSearchPage(): void {
    onOpenChange(false);
    navigate(debounced ? `/search?q=${encodeURIComponent(debounced)}` : "/search");
  }

  // Same standing-door rationale as handleOpenSearchPage, for the external
  // catalog lookup. Only the name field is prefilled: the lookup is a
  // deps.dev exact match, so guessing an ecosystem from a free-text term
  // would just as often be wrong as right.
  function handleOpenPackageLookup(): void {
    onOpenChange(false);
    navigate(
      debounced ? `/packages/lookup?name=${encodeURIComponent(debounced)}` : "/packages/lookup",
    );
  }

  // Deep-link into the owning project's Components tab, pre-filtered by the
  // component name. The tab reads its own `?components_search=` key into the
  // free-text filter (S1-5 gave each tab a distinct param so a term typed on
  // one tab stops leaking into the next), so the user lands with the row
  // already narrowed. We can't open the component drawer directly —
  // `?drawer=` keys on the component's internal id, which the search hit
  // doesn't carry — so the search filter is the closest stable anchor.
  function handleSelectComponent(hit: SearchComponentHit): void {
    onOpenChange(false);
    navigate(
      `/projects/${hit.project_id}?tab=components&${COMPONENTS_SEARCH_PARAM}=${encodeURIComponent(
        hit.component_name,
      )}`,
    );
  }

  // Deep-link into the owning project's Vulnerabilities tab, pre-filtered by
  // the CVE id (the tab reads `?vulnerabilities_search=` into its free-text
  // filter). Same rationale as components: `?vuln=` keys on the finding's
  // internal id, not the CVE id, so the search filter is the stable anchor.
  function handleSelectVulnerability(hit: SearchVulnerabilityHit): void {
    onOpenChange(false);
    navigate(
      `/projects/${hit.project_id}?tab=vulnerabilities&${VULNERABILITIES_SEARCH_PARAM}=${encodeURIComponent(
        hit.cve_id,
      )}`,
    );
  }

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      label={t("command_menu.placeholder")}
    >
      <CommandInput
        placeholder={t("command_menu.placeholder")}
        value={query}
        onValueChange={setQuery}
        data-testid="command-menu-input"
      />
      <CommandList data-testid="command-menu-list">
        <CommandEmpty>{t("command_menu.no_results")}</CommandEmpty>

        {projects.length > 0 ? (
          <CommandGroup heading={t("command_menu.group.projects")}>
            {projects.map((project) => (
              <CommandItem
                key={project.id}
                // cmdk filters items by matching the `value` against the
                // input. We include name + slug so the user can search by
                // either; the visible label keeps the name primary.
                value={`${project.name} ${project.slug}`}
                onSelect={() => handleSelectProject(project)}
                data-testid={`command-menu-project-${project.id}`}
              >
                <FolderOpen className="h-4 w-4 text-muted-foreground" aria-hidden />
                <span className="truncate">{project.name}</span>
                <span className="ml-2 truncate font-mono text-xs text-muted-foreground">
                  {project.slug}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}

        {searchLoading ? (
          <div
            role="status"
            aria-live="polite"
            className="py-6 text-center text-sm text-muted-foreground"
            data-testid="command-menu-search-loading"
          >
            {t("command_menu.searching")}
          </div>
        ) : null}

        {componentHits.length > 0 ? (
          <CommandGroup
            heading={t("command_menu.group.components")}
            data-testid="command-menu-group-components"
          >
            {componentHits.map((hit, index) => (
              <CommandItem
                key={`${hit.project_id}-${hit.purl}-${index}`}
                // Include the component name, purl, and the active query so
                // cmdk's client-side filter keeps the backend-matched hit
                // visible regardless of which field the server matched on.
                value={`${hit.component_name} ${hit.version} ${hit.purl} ${debounced}`}
                onSelect={() => handleSelectComponent(hit)}
                data-testid={`command-menu-component-${hit.project_id}-${index}`}
              >
                <Package
                  className="h-4 w-4 text-muted-foreground"
                  aria-hidden
                />
                <span className="truncate">{hit.component_name}</span>
                {hit.version ? (
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {hit.version}
                  </span>
                ) : null}
                <span className="ml-auto truncate pl-2 text-xs text-muted-foreground">
                  {hit.project_name}
                </span>
              </CommandItem>
            ))}
            <CommandItem
              value={`see-all-components ${debounced}`}
              onSelect={() => handleSeeAll("components")}
              data-testid="command-menu-see-all-components"
            >
              <Package className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span>{t("command_menu.see_all")}</span>
            </CommandItem>
          </CommandGroup>
        ) : null}

        {vulnHits.length > 0 ? (
          <CommandGroup
            heading={t("command_menu.group.cves")}
            data-testid="command-menu-group-cves"
          >
            {vulnHits.map((hit) => (
              <CommandItem
                key={`${hit.project_id}-${hit.cve_id}`}
                // Both spellings are searchable: cmdk filters on this string,
                // and a Korean session types 치명, not "critical".
                value={`${hit.cve_id} ${hit.severity} ${severityLabel(
                  hit.severity,
                  t,
                )} ${debounced}`}
                onSelect={() => handleSelectVulnerability(hit)}
                data-testid={`command-menu-cve-${hit.project_id}-${hit.cve_id}`}
              >
                <ShieldAlert
                  className="h-4 w-4 text-muted-foreground"
                  aria-hidden
                />
                <span className="truncate font-mono">{hit.cve_id}</span>
                {/* Severity = color dot + text label (a11y: color is not the
                    sole signal). */}
                <span className="inline-flex items-center gap-1.5 pl-1">
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      severityDotClass(hit.severity),
                    )}
                    aria-hidden
                  />
                  <span className="text-xs capitalize text-muted-foreground">
                    {severityLabel(hit.severity, t)}
                  </span>
                </span>
                <span className="ml-auto truncate pl-2 text-xs text-muted-foreground">
                  {hit.project_name}
                </span>
              </CommandItem>
            ))}
            <CommandItem
              value={`see-all-cves ${debounced}`}
              onSelect={() => handleSeeAll("vulnerabilities")}
              data-testid="command-menu-see-all-vulnerabilities"
            >
              <ShieldAlert className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span>{t("command_menu.see_all")}</span>
            </CommandItem>
          </CommandGroup>
        ) : null}

        <CommandGroup heading={t("command_menu.group.pages")}>
          {/* First row of the group, and the only one that carries the typed
              term with it. `value` includes the debounced term so cmdk's
              client-side filter keeps the row visible no matter what was
              typed — without it, typing "lodash" would filter away the very
              door that searches for "lodash". */}
          <CommandItem
            value={`search ${t("command_menu.open_search")} ${debounced}`}
            onSelect={handleOpenSearchPage}
            data-testid="command-menu-open-search"
          >
            <SearchIcon className="h-4 w-4 text-muted-foreground" aria-hidden />
            <span>{t("command_menu.open_search")}</span>
            <span className="ml-2 truncate text-xs text-muted-foreground">
              {t("command_menu.open_search_hint")}
            </span>
            <CommandShortcut className="font-mono">/search</CommandShortcut>
          </CommandItem>
          {packageLookupEnabled ? (
            <CommandItem
              value={`package-lookup ${t("command_menu.open_package_lookup")} ${debounced}`}
              onSelect={handleOpenPackageLookup}
              data-testid="command-menu-open-package-lookup"
            >
              <PackageSearch className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span>{t("command_menu.open_package_lookup")}</span>
              <span className="ml-2 truncate text-xs text-muted-foreground">
                {t("command_menu.open_package_lookup_hint")}
              </span>
              <CommandShortcut className="font-mono">/packages/lookup</CommandShortcut>
            </CommandItem>
          ) : null}
          {visibleRoutes.map((route) => {
            const Icon = route.icon;
            const namespace = route.labelKey.split(":")[0];
            const key = route.labelKey.split(":")[1];
            return (
              <CommandItem
                key={route.to}
                value={`${route.to} ${t(key, { ns: namespace })}`}
                onSelect={() => handleSelectRoute(route)}
                data-testid={`command-menu-route-${route.to}`}
              >
                <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                <span>{t(key, { ns: namespace })}</span>
                <CommandShortcut className="font-mono">
                  {route.to}
                </CommandShortcut>
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
