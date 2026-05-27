import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import type { ScanSummary } from "@/features/projects/api/projectDetailApi";
import { useLatestRelease } from "@/features/projects/api/useLatestRelease";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { useReleases } from "@/features/projects/api/useReleases";
import { ComplianceTab } from "@/features/projects/components/ComplianceTab";
import { ComponentsTab } from "@/features/projects/components/ComponentsTab";
import { OverviewTab } from "@/features/projects/components/OverviewTab";
import { ReleaseSwitcher } from "@/features/projects/components/ReleaseSwitcher";
import { ReleasesTab } from "@/features/projects/components/ReleasesTab";
import { ReportsTab } from "@/features/projects/components/ReportsTab";
import { RiskGauge } from "@/features/projects/components/RiskGauge";
import { SettingsTab } from "@/features/projects/components/SettingsTab";
import { SnapshotBanner } from "@/features/projects/components/SnapshotBanner";
import { SourceTab } from "@/features/projects/components/SourceTab";
import { VulnerabilitiesTab } from "@/features/projects/components/VulnerabilitiesTab";
import { projectErrorMessageKey } from "@/features/projects/lib/projectErrorMessage";
import { releaseLabel } from "@/features/projects/lib/releaseLabel";
import { ScanProgress } from "@/features/scan/ScanProgress";
import { SourceSelectDialog } from "@/features/scan/SourceSelectDialog";
import { useDemoMode } from "@/hooks/useDemoMode";
import {
  getProject,
  type ProjectPublic,
  type ScanPublic,
  type ScanStatus,
} from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

/**
 * ProjectDetailPage — Phase 3 PR #10.
 *
 * Detail page rendered at `/projects/:id`. Houses the tab strip
 * (Overview / Components / Vulnerabilities / Licenses / Obligations) and a
 * breadcrumb-flavored header with the project name + risk badge.
 *
 * Tab selection is mirrored into `?tab=…` so reload + deep-link survive.
 */

/**
 * W4-C — Information Architecture overhaul (8-tab strip).
 *
 * The detail surface now has eight top-level tabs:
 *   overview / releases / components / vulnerabilities / source /
 *   compliance / reports / settings
 *
 * The four legacy tabs (licenses / obligations / sbom / remediation) were
 * absorbed:
 *   - licenses + obligations → compliance (single unified tab, sub-tabs
 *     ``?cview=licenses|obligations``)
 *   - sbom                   → reports (in-page section ``?rpt_section=sbom``)
 *   - remediation            → vulnerabilities (in-page panel
 *                              ``?vuln_section=remediation``)
 *
 * Legacy ``?tab=`` values still land the user on the right surface — see
 * {@link redirectLegacyTab}.
 */
const ALLOWED_TABS = new Set([
  "overview",
  "releases",
  "components",
  "vulnerabilities",
  "source",
  "compliance",
  "reports",
  "settings",
]);

/**
 * Map a legacy tab token (pre-W4-C) to its W4-C successor + the URL fragment
 * that re-anchors the user on the right sub-surface. Returning ``null`` means
 * "this token is not a legacy redirect" — the caller treats it as unknown.
 */
function redirectLegacyTab(
  raw: string,
): { tab: string; extra: Record<string, string> } | null {
  switch (raw) {
    case "licenses":
      return { tab: "compliance", extra: { cview: "licenses" } };
    case "obligations":
      return { tab: "compliance", extra: { cview: "obligations" } };
    case "sbom":
      return { tab: "reports", extra: { rpt_section: "sbom" } };
    case "remediation":
      return { tab: "vulnerabilities", extra: { vuln_section: "remediation" } };
    default:
      return null;
  }
}

export function ProjectDetailPage() {
  const { t, i18n } = useTranslation("project_detail");
  const { id: projectId } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const tabParam = searchParams.get("tab");
  // W4-C — accept a legacy token (licenses / obligations / sbom / remediation)
  // by routing the active surface to its successor. A useEffect below also
  // rewrites the URL so reload/share matches the new IA.
  const legacyRedirect = tabParam ? redirectLegacyTab(tabParam) : null;
  const activeTab =
    tabParam && ALLOWED_TABS.has(tabParam)
      ? tabParam
      : legacyRedirect != null
        ? legacyRedirect.tab
        : "overview";

  // Pinned snapshot scan (feature #28). When set, the whole detail surface
  // reads that historical scan instead of the latest succeeded one. Empty
  // string is treated as "not set" so a hand-edited `?scan=` can't wedge it.
  const scanParam = searchParams.get("scan");
  const pinnedScanId = scanParam && scanParam.length > 0 ? scanParam : undefined;

  // W4-C — rewrite a legacy `?tab=` token to its W4-C successor in the URL
  // bar so a deep-link or bookmark settles on the canonical IA. We do this
  // inside an effect (not during render) so React Router doesn't see two
  // setSearchParams calls per commit.
  useEffect(() => {
    if (legacyRedirect == null) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", legacyRedirect.tab);
        for (const [k, v] of Object.entries(legacyRedirect.extra)) {
          next.set(k, v);
        }
        return next;
      },
      { replace: true },
    );
    // Run on every URL change that produces a legacy redirect — the parsed
    // `legacyRedirect` object is recomputed each render, so the deps need
    // only the stable bits we care about.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabParam]);

  const projectQuery = useQuery({
    queryKey: ["projects", projectId, "summary"],
    queryFn: () => getProject(projectId as string),
    enabled: typeof projectId === "string" && projectId.length > 0,
  });

  // Overview is fetched here too so the header risk badge can render
  // alongside the breadcrumb without waiting for the tab to mount. The pinned
  // scan threads in so the header gauge matches the snapshot being viewed.
  const overview = useProjectOverview(projectId, pinnedScanId);

  // Resolve the latest succeeded scan (newest-first releases list, size 1) so
  // we can tell whether the pinned `?scan=` is the latest (normal view) or an
  // older snapshot (historical, read-only). Loading → not historical yet, so
  // the banner only appears once we can confirm it's an older scan.
  const latestRelease = useLatestRelease(projectId);
  const latestScanId = latestRelease.data?.scan_id ?? null;
  const isHistorical =
    pinnedScanId != null &&
    latestScanId != null &&
    pinnedScanId !== latestScanId;

  // Resolve a human label for the pinned snapshot for the banner: look it up in
  // the releases list (shares the ReleasesTab query key, so it's deduped when
  // that tab is open). Falls back to the scan id until the list resolves.
  const i18nLocale = i18n.language;
  const releasesLookup = useReleases(
    isHistorical ? projectId : undefined,
    { page: 1, size: 50 },
  );
  const snapshotLabel = (() => {
    const match = releasesLookup.data?.items.find(
      (item) => item.scan_id === pinnedScanId,
    );
    if (match) return releaseLabel(match, i18nLocale);
    return pinnedScanId ?? "";
  })();

  // Scan trigger lives here too (not only on the project list): users land on
  // the detail page right after creating a project, so a "Scan" button in the
  // header lets them start a scan without bouncing back to the list.
  const { demoReadOnly } = useDemoMode();
  const project = projectQuery.data ?? null;
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false);
  const [scanDrawer, setScanDrawer] = useState<{
    open: boolean;
    scanId: string | null;
    status: ScanStatus | null;
    release: string | null;
  }>({ open: false, scanId: null, status: null, release: null });

  function handleScanStarted(scan: ScanPublic, _project: ProjectPublic) {
    setSourceDialogOpen(false);
    setScanDrawer({
      open: true,
      scanId: scan.id,
      status: scan.status,
      release: scan.release,
    });
  }

  // Re-open the live progress drawer for a scan whose drawer was closed.
  // The WebSocket sends the current percent/step on connect (ws.py initial
  // sync push), so an in-flight scan resumes streaming where it left off.
  // The overview summary carries no `release`, so the chip is omitted here.
  function handleReopenScan(scan: ScanSummary) {
    setScanDrawer({
      open: true,
      scanId: scan.id,
      status: scan.status as ScanStatus,
      release: null,
    });
  }

  // Persistent "a scan is running" affordance (#29). The page-level overview
  // query is always mounted (independent of the active tab) and polls while any
  // recent scan is queued/running, so this stays live. Surfacing it in the
  // header means closing the progress drawer no longer strands the user — they
  // can re-open the in-flight scan from here regardless of which tab is active.
  const activeScan = (overview.data?.recent_scans ?? []).find(
    (scan) => scan.status === "queued" || scan.status === "running",
  );

  if (!projectId) {
    return (
      <div className="p-6" data-testid="project-detail-missing-id">
        <Alert variant="destructive">
          <AlertDescription>{t("page.missing_id")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  function setTab(next: string) {
    // W4-C — legacy tab tokens still route correctly even if a caller asks
    // for the old name (defensive). Translate them up front so the drop-
    // filter logic below sees the W4-C target.
    let target = next;
    let injected: Record<string, string> | null = null;
    const redirect = redirectLegacyTab(target);
    if (redirect != null) {
      target = redirect.tab;
      injected = redirect.extra;
    }

    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        // When switching tabs, drop tab-scoped filter params so we don't
        // carry a stale severity filter into Overview. Components,
        // Vulnerabilities, and Compliance all use `search` / `sort` /
        // `order`, but they have distinct drawer keys (`drawer` /
        // `vuln` / `license` / `obligation`), distinct multi-filter axes,
        // and distinct pagination semantics.
        if (
          target !== "components" &&
          target !== "vulnerabilities" &&
          target !== "compliance"
        ) {
          merged.delete("search");
          merged.delete("sort");
          merged.delete("order");
        }
        if (target !== "components" && target !== "vulnerabilities") {
          merged.delete("severity");
        }
        if (target !== "components" && target !== "compliance") {
          // license_category is shared by Components and the Compliance tab
          // (which hosts the legacy Licenses + Obligations surfaces). Drop
          // it when leaving so the next unrelated tab doesn't carry a stale
          // bucket. The Vulnerabilities tab also reads `license_category`
          // (W2 #33) so we keep it across that lane too.
          if (target !== "vulnerabilities") {
            merged.delete("license_category");
          }
        }
        if (target !== "components") {
          merged.delete("drawer");
        }
        if (target !== "vulnerabilities") {
          merged.delete("vuln");
          merged.delete("status");
          // W4-C #22 — vuln_section is internal to the Vulnerabilities tab
          // (controls the Remediation collapsible panel). Drop it when
          // leaving so another tab doesn't carry it.
          merged.delete("vuln_section");
        }
        if (
          target !== "vulnerabilities" &&
          target !== "compliance"
        ) {
          merged.delete("page");
        }
        if (target !== "compliance") {
          // `kind` is now scoped to the Compliance tab (the unified host of
          // the Licenses + Obligations sub-views). `license` / `obligation`
          // drawer keys belong here too. `cview` is the sub-view selector.
          merged.delete("kind");
          merged.delete("license");
          merged.delete("obligation");
          merged.delete("cview");
        }
        if (target !== "source") {
          // The Source tab mirrors the open file path into `?path=`. Drop it
          // when leaving so another tab doesn't inherit a stale file selector
          // (Components/Vulnerabilities use distinct drawer keys, not `path`).
          merged.delete("path");
        }
        if (target !== "reports") {
          // Reports tab mirrors its filter / page into `?rpt_type=` /
          // `?rpt_page=` / `?rpt_section=`. Drop them when leaving so
          // re-entry to another tab doesn't carry a stale Reports filter
          // into a different surface.
          merged.delete("rpt_type");
          merged.delete("rpt_page");
          merged.delete("rpt_section");
        }
        if (target === "overview") {
          merged.delete("tab");
        } else {
          merged.set("tab", target);
        }
        // Apply legacy-token injected params last so we land on the right
        // sub-surface (e.g. cview=licenses for the old ?tab=licenses).
        if (injected != null) {
          for (const [k, v] of Object.entries(injected)) {
            merged.set(k, v);
          }
        }
        return merged;
      },
      { replace: true },
    );
  }

  // Pin a snapshot and jump to a target tab. We preserve other params but
  // drop tab-scoped filter/drawer params (a stale severity filter or an open
  // drawer keyed to a different scan would be confusing) and set `?scan=`.
  //
  // P2 #2 — Releases tab now routes to Components (target tab passed by
  // caller); the header ReleaseSwitcher keeps the original Overview target.
  function pinSnapshotAndGoToTab(scanId: string, targetTab: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("scan", scanId);
        if (targetTab) {
          next.set("tab", targetTab);
        } else {
          next.delete("tab");
        }
        // Drop every tab-scoped param so the target tab opens clean on the
        // snapshot. W4-C — `cview` / `rpt_section` / `vuln_section` are the
        // new sub-view selectors and must reset too.
        for (const key of [
          "search",
          "sort",
          "order",
          "severity",
          "license_category",
          "kind",
          "page",
          "drawer",
          "vuln",
          "status",
          "license",
          "obligation",
          "path",
          "min_epss",
          "reachable",
          "vex_suppressed",
          "cview",
          "rpt_section",
          "rpt_type",
          "rpt_page",
          "vuln_section",
        ]) {
          next.delete(key);
        }
        return next;
      },
      { replace: false },
    );
  }

  // Header ReleaseSwitcher action: pin + jump to Overview (the snapshot's
  // landing view).
  function handleViewSnapshot(scanId: string) {
    pinSnapshotAndGoToTab(scanId, null);
  }

  // P2 #2 — Releases tab row action: pin + jump straight to Components.
  // A release IS a component snapshot, so the Components tab is the natural
  // landing surface when the user picks a row in the release history.
  function handleViewSnapshotComponents(scanId: string) {
    pinSnapshotAndGoToTab(scanId, "components");
  }

  // Clear the pinned snapshot — "Back to latest" returns to the live view.
  function clearScan() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("scan");
        return next;
      },
      { replace: false },
    );
  }

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      data-testid="project-detail-page"
      data-project-id={projectId}
    >
      <ProjectDetailHeader
        projectId={projectId}
        projectName={projectQuery.data?.name ?? null}
        riskScore={overview.data?.risk_score ?? null}
        isProjectLoading={projectQuery.isLoading}
        isProjectError={projectQuery.isError}
        projectError={projectQuery.error}
        canScan={project != null && !demoReadOnly}
        demoReadOnly={demoReadOnly}
        onScan={() => setSourceDialogOpen(true)}
        activeScan={activeScan ?? null}
        onReopenActiveScan={
          activeScan ? () => handleReopenScan(activeScan) : undefined
        }
        pinnedScanId={pinnedScanId}
        latestScanId={latestScanId}
        isHistorical={isHistorical}
        onSelectRelease={handleViewSnapshot}
        onSelectLatest={clearScan}
      />

      {isHistorical ? (
        <SnapshotBanner
          label={snapshotLabel}
          onBackToLatest={clearScan}
        />
      ) : null}

      <Tabs value={activeTab} onValueChange={setTab}>
        {/* W4-C — 8-tab strip. The IA overhaul collapsed Licenses +
            Obligations into Compliance, SBOM into Reports, and Remediation
            into Vulnerabilities. Legacy `?tab=` tokens still land on the
            right surface via `redirectLegacyTab`. */}
        <TabsList data-testid="project-detail-tabs">
          <TabsTrigger
            value="overview"
            data-testid="project-detail-tab-overview"
          >
            {t("tabs.overview")}
          </TabsTrigger>
          <TabsTrigger
            value="releases"
            data-testid="project-detail-tab-releases"
          >
            {t("tabs.releases")}
          </TabsTrigger>
          <TabsTrigger
            value="components"
            data-testid="project-detail-tab-components"
          >
            {t("tabs.components")}
          </TabsTrigger>
          <TabsTrigger
            value="vulnerabilities"
            data-testid="project-detail-tab-vulnerabilities"
          >
            {t("tabs.vulnerabilities")}
          </TabsTrigger>
          {/* P2 #3 — Source (raw artifact) is the cognitive predecessor of
              the classified outputs (Compliance, Reports), so it sits
              between Vulnerabilities and Compliance. */}
          <TabsTrigger
            value="source"
            data-testid="project-detail-tab-source"
          >
            {t("tabs.source")}
          </TabsTrigger>
          <TabsTrigger
            value="compliance"
            data-testid="project-detail-tab-compliance"
          >
            {t("tabs.compliance")}
          </TabsTrigger>
          <TabsTrigger
            value="reports"
            data-testid="project-detail-tab-reports"
          >
            {t("tabs.reports")}
          </TabsTrigger>
          <TabsTrigger
            value="settings"
            data-testid="project-detail-tab-settings"
          >
            {t("tabs.settings")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTab
            projectId={projectId}
            project={project}
            scanId={pinnedScanId}
            onSelectScan={handleReopenScan}
            onJumpToComponents={(scan) => handleViewSnapshotComponents(scan.id)}
          />
        </TabsContent>
        <TabsContent value="releases">
          <ReleasesTab
            projectId={projectId}
            onViewSnapshot={handleViewSnapshotComponents}
          />
        </TabsContent>
        <TabsContent value="components">
          <ComponentsTab projectId={projectId} scanId={pinnedScanId} />
        </TabsContent>
        <TabsContent value="vulnerabilities">
          <VulnerabilitiesTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
            scanId={pinnedScanId}
            readOnly={isHistorical}
          />
        </TabsContent>
        <TabsContent value="source">
          <SourceTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
            scanId={pinnedScanId}
          />
        </TabsContent>
        <TabsContent value="compliance">
          <ComplianceTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
            scanId={pinnedScanId}
          />
        </TabsContent>
        <TabsContent value="reports">
          <ReportsTab
            projectId={projectId}
            scanId={pinnedScanId}
            lastSucceededScanAt={overview.data?.last_succeeded_scan_at ?? null}
          />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsTab
            projectId={projectId}
            project={projectQuery.data ?? null}
          />
        </TabsContent>
      </Tabs>

      {project ? (
        <SourceSelectDialog
          open={sourceDialogOpen}
          onOpenChange={setSourceDialogOpen}
          project={project}
          onScanStarted={handleScanStarted}
        />
      ) : null}

      <Sheet
        open={scanDrawer.open}
        onOpenChange={(open) => setScanDrawer((s) => ({ ...s, open }))}
      >
        <SheetContent
          side="right"
          className="flex flex-col gap-4"
          data-testid="scan-progress-drawer"
        >
          <SheetHeader>
            <SheetTitle>{project?.name ?? ""}</SheetTitle>
            <SheetDescription>{t("page.scan_drawer_subtitle")}</SheetDescription>
          </SheetHeader>
          {scanDrawer.scanId ? (
            <ScanProgress
              scanId={scanDrawer.scanId}
              release={scanDrawer.release}
              status={scanDrawer.status ?? "queued"}
              onClose={() => setScanDrawer((s) => ({ ...s, open: false }))}
              onCancelled={() =>
                setScanDrawer((s) => ({ ...s, status: "cancelled" }))
              }
            />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

interface ProjectDetailHeaderProps {
  projectId: string;
  projectName: string | null;
  riskScore: number | null;
  isProjectLoading: boolean;
  isProjectError: boolean;
  projectError: unknown;
  canScan: boolean;
  demoReadOnly: boolean;
  onScan: () => void;
  /**
   * The project's currently queued/running scan, if any (#29). When present the
   * header shows a persistent, clickable "scan running" chip so closing the
   * progress drawer never strands the user.
   */
  activeScan: ScanSummary | null;
  /** Re-open the live progress drawer for {@link activeScan}. */
  onReopenActiveScan?: () => void;
  /** Currently pinned scan id (`?scan=`), or undefined for the live view. */
  pinnedScanId: string | undefined;
  /** Latest succeeded scan id, or null when none / still resolving. */
  latestScanId: string | null;
  /** Whether the pinned scan is an older (read-only) snapshot. */
  isHistorical: boolean;
  /** Pin a release (sets `?scan=`); same path as the Releases tab action. */
  onSelectRelease: (scanId: string) => void;
  /** Clear the pinned snapshot (`?scan=`) and return to the live view. */
  onSelectLatest: () => void;
}

function ProjectDetailHeader({
  projectId,
  projectName,
  riskScore,
  isProjectLoading,
  isProjectError,
  projectError,
  canScan,
  demoReadOnly,
  onScan,
  activeScan,
  onReopenActiveScan,
  pinnedScanId,
  latestScanId,
  isHistorical,
  onSelectRelease,
  onSelectLatest,
}: ProjectDetailHeaderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <header
      className={cn(
        "flex items-center justify-between gap-4 border-b px-6 py-3",
      )}
      data-testid="project-detail-header"
    >
      <div className="flex flex-col gap-1">
        <nav
          className="flex items-center gap-2 text-xs text-muted-foreground"
          aria-label={t("page.breadcrumb_aria")}
        >
          <Link
            to="/projects"
            className="hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="project-detail-breadcrumb-projects"
          >
            {t("page.breadcrumb_projects")}
          </Link>
          <span aria-hidden>/</span>
          <span data-testid="project-detail-breadcrumb-current">
            {/* BUG-004: once the load has settled into an error (e.g. 404) the
                crumb must stop showing the loading placeholder — fall through to
                an "unavailable" label instead of a perpetual "Loading…". */}
            {projectName ??
              (isProjectError
                ? t("page.breadcrumb_unavailable")
                : t("page.loading_name"))}
          </span>
        </nav>
        {isProjectLoading ? (
          <Skeleton className="h-6 w-48" />
        ) : isProjectError ? (
          <span
            className="text-base font-semibold text-destructive"
            data-testid="project-detail-load-error"
          >
            {/* BUG-002: localize the RFC 7807 problem (404/403) instead of
                rendering the backend's English `title` (e.g. "Project Not
                Found") so the KO locale shows Korean. */}
            {t(projectErrorMessageKey(projectError, "page.errors"), {
              defaultValue: t("page.load_error"),
            })}
          </span>
        ) : (
          <h1
            className="text-lg font-semibold tracking-tight"
            data-testid="project-detail-title"
          >
            {projectName}
          </h1>
        )}
        <div className="flex items-center gap-3">
          <span
            className="font-mono text-[10px] text-muted-foreground"
            data-testid="project-detail-id"
          >
            {projectId}
          </span>
          <ReleaseSwitcher
            projectId={projectId}
            pinnedScanId={pinnedScanId}
            latestScanId={latestScanId}
            isHistorical={isHistorical}
            onSelectRelease={onSelectRelease}
            onSelectLatest={onSelectLatest}
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        {activeScan ? (
          <button
            type="button"
            onClick={onReopenActiveScan}
            data-testid="project-detail-active-scan"
            data-status={activeScan.status}
            data-scan-id={activeScan.id}
            aria-label={t("page.scan_active_reopen_aria")}
            className="inline-flex items-center gap-1.5 rounded-full border border-risk-low/40 bg-risk-low/10 px-2.5 py-1 text-xs font-medium text-risk-low transition-colors hover:bg-risk-low/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {activeScan.status === "queued"
              ? t("page.scan_active_queued")
              : t("page.scan_active_running")}
          </button>
        ) : null}
        {/* P1 #10 — block re-trigger while a scan is queued or running for
            this project. The DB already enforces this via the partial unique
            index `ix_scans_project_active` (the trigger endpoint returns 409
            with `scan_already_in_progress=true`), but disabling the button
            prevents the user from ever hitting that conflict. The "scan
            running" chip to the left remains as the explicit affordance —
            click it to re-open the in-flight drawer. */}
        <Button
          size="sm"
          onClick={onScan}
          disabled={!canScan || activeScan !== null}
          title={
            demoReadOnly
              ? t("page.scan_demo_disabled")
              : activeScan !== null
                ? t("page.scan_already_active", {
                    defaultValue:
                      "A scan is already running for this project — open the in-progress drawer to view it.",
                  })
                : undefined
          }
          data-testid="project-detail-scan"
          data-scan-blocked={activeScan !== null ? "active" : undefined}
        >
          {t("page.scan")}
        </Button>
        {riskScore != null ? (
          <div data-testid="project-detail-risk-badge">
            <RiskGauge score={riskScore} />
          </div>
        ) : null}
      </div>
    </header>
  );
}

