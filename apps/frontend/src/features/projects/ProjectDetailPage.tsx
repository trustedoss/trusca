// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";
import { FolderX, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
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
import {
  COMPONENTS_SEARCH_PARAM,
  LICENSES_SEARCH_PARAM,
  OBLIGATIONS_SEARCH_PARAM,
  VULNERABILITIES_SEARCH_PARAM,
} from "@/features/projects/components/tabSearchParam";
import { useLatestRelease } from "@/features/projects/api/useLatestRelease";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { useReleases } from "@/features/projects/api/useReleases";
import { ComplianceTab } from "@/features/projects/components/ComplianceTab";
import { ComponentsTab } from "@/features/projects/components/ComponentsTab";
import { OverviewTab } from "@/features/projects/components/OverviewTab";
import { ReleaseSwitcher } from "@/features/projects/components/ReleaseSwitcher";
import { ReleasesTab } from "@/features/projects/components/ReleasesTab";
import { ReportsTab } from "@/features/projects/components/ReportsTab";
import { SandboxScanNotice } from "@/features/projects/components/SandboxScanNotice";
import { SettingsTab } from "@/features/projects/components/SettingsTab";
import { GovernanceBand } from "@/features/projects/components/GovernanceBand";
import { SnapshotBanner } from "@/features/projects/components/SnapshotBanner";
import { SourceTab } from "@/features/projects/components/SourceTab";
import { VulnerabilitiesTab } from "@/features/projects/components/VulnerabilitiesTab";
import { projectErrorMessageKey } from "@/features/projects/lib/projectErrorMessage";
import { releaseLabel } from "@/features/projects/lib/releaseLabel";
import { SbomIngestDialog } from "@/features/scan/SbomIngestDialog";
import { ScanProgress } from "@/features/scan/ScanProgress";
import { SourceSelectDialog } from "@/features/scan/SourceSelectDialog";
import { useDemoMode } from "@/hooks/useDemoMode";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { isDemoSandboxProjectName } from "@/lib/demoSandbox";
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

  // The tab is part of the address, so it belongs in the tab title too: one
  // project open on Overview and Vulnerabilities is two tabs the user needs to
  // tell apart, and the project name alone would make them identical.
  useDocumentTitle(projectQuery.data?.name, t(`tabs.${activeTab}`));

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
  const { demoReadOnly, demoSandboxScans } = useDemoMode();
  const project = projectQuery.data ?? null;
  /**
   * The project could not be fetched — 404, 403, or anything else.
   *
   * G0-5: everything below the header used to render regardless, so a
   * nonexistent id produced a full console (tabs, governance band, Scan
   * button) wrapped around two error messages, one of them in English. This
   * gates the body on the one condition that makes all of it meaningless.
   */
  const isProjectUnavailable = projectQuery.isError;
  // feat/demo-sandbox-scan — when the read-only demo has the sandbox carve-out
  // on AND this is the seeded "Demo Sandbox" project, the backend permits a
  // source scan + SBOM ingest here (and only here). We re-open those two
  // affordances; every other project / write stays read-only.
  const isSandboxProject =
    demoSandboxScans && isDemoSandboxProjectName(project?.name);
  // Effective read-only for the write affordances on THIS page: normally the
  // demo flag, but relaxed for the sandbox project so the Scan button + ingest
  // entry point are live.
  const writesDisabled = demoReadOnly && !isSandboxProject;
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false);
  const [sbomIngestOpen, setSbomIngestOpen] = useState(false);
  const [scanDrawer, setScanDrawer] = useState<{
    open: boolean;
    scanId: string | null;
    status: ScanStatus | null;
    release: string | null;
  }>({ open: false, scanId: null, status: null, release: null });

  function handleScanStarted(scan: ScanPublic, _project: ProjectPublic) {
    setSourceDialogOpen(false);
    setSbomIngestOpen(false);
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
  // The concurrency gate is per-(project, branch), and the Scan button always
  // triggers an ad-hoc (ref-less) run — so only another ad-hoc scan can block
  // it. A CI scan on `main` leaves the button usable; disabling on "any active
  // scan" would hide the concurrency the gate now allows.
  const blockingScan = (overview.data?.recent_scans ?? []).find(
    (scan) =>
      (scan.status === "queued" || scan.status === "running") &&
      scan.ref == null,
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
        // Vulnerabilities, and Compliance still share `sort` / `order`, but
        // they have distinct drawer keys (`drawer` / `vuln` / `license` /
        // `obligation`), distinct multi-filter axes, and distinct pagination
        // semantics.
        //
        // S1-5 — the per-tab search keys are deliberately NOT dropped here.
        // The deletion existed because all four tabs shared one `search` key
        // and it would leak into the next tab; now that each owns its own,
        // keeping it means returning to a tab restores the term you left
        // there, and no sibling can read it. Only the legacy shared key is
        // retired.
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
        // Only Compliance still pages. The Vulnerabilities tab went infinite
        // and clears `page` itself, so preserving it on the way in would only
        // hand over a parameter about to be dropped.
        if (target !== "compliance") {
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
      // Tab navigation pushes a new history entry so browser back returns the
      // user to the previously selected tab (Overview ↔ Versions etc.). The
      // other `setSearchParams` call sites in this file (drawer toggles,
      // filter inputs, pinning a snapshot) keep using `{ replace: true }`
      // because their churn would otherwise flood the back-stack with noise.
      { replace: false },
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
          // S1-5 — each detail tab now mirrors into its own search key. The
          // bare `search` stays in the list because a link minted before that
          // change can still carry it.
          "search",
          COMPONENTS_SEARCH_PARAM,
          VULNERABILITIES_SEARCH_PARAM,
          LICENSES_SEARCH_PARAM,
          OBLIGATIONS_SEARCH_PARAM,
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
        isProjectLoading={projectQuery.isLoading}
        isProjectError={projectQuery.isError}
        projectError={projectQuery.error}
        canScan={project != null && !writesDisabled}
        demoReadOnly={writesDisabled}
        onScan={() => setSourceDialogOpen(true)}
        activeScan={activeScan ?? null}
        blockingScan={blockingScan ?? null}
        onReopenActiveScan={
          activeScan ? () => handleReopenScan(activeScan) : undefined
        }
        pinnedScanId={pinnedScanId}
        latestScanId={latestScanId}
        isHistorical={isHistorical}
        onSelectRelease={handleViewSnapshot}
        onSelectLatest={clearScan}
      />

      {isSandboxProject ? (
        <SandboxScanNotice onUploadSbom={() => setSbomIngestOpen(true)} />
      ) : null}

      {isHistorical ? (
        <SnapshotBanner
          label={snapshotLabel}
          onBackToLatest={clearScan}
        />
      ) : null}

      {/* The governance band sits above the tabs, not inside one. Everything
          in it is reachable from a tab already; putting it here is what makes
          reading it cost no navigation, so the project's standing state is
          present while you work inside any tab.

          It describes the LIVE snapshot, so it is hidden while a historical
          one is pinned. Every tab below would be showing a release from two
          months ago while the band said "Blocked · 5 critical" about today's
          HEAD, and two of its five tiles — approvals and KEV deadlines — have
          no historical form to switch to. */}
      {/* G0-5 — a project that failed to load gets one surface, not the whole
          console.

          On a 404 the page used to render its full chrome around nothing: the
          governance band (which then reported its own separate failure, so one
          missing project produced two error messages), the 8-tab strip, and a
          tab body showing the backend's untranslated `Project Not Found /
          project <uuid> not found` inside an otherwise Korean page. Every tab
          was clickable and every one of them led to another error.

          The header above already says what happened, localized. Below it the
          only useful thing is the way back. */}
      {isProjectUnavailable ? (
        <div className="flex flex-1 items-start px-6 py-10">
          <EmptyState
            data-testid="project-detail-unavailable"
            icon={<FolderX />}
            title={t(projectErrorMessageKey(projectQuery.error, "page.errors"), {
              defaultValue: t("page.load_error"),
            })}
            description={t("page.unavailable_help")}
            action={
              <Button asChild variant="outline" size="sm">
                <Link
                  to="/projects"
                  data-testid="project-detail-unavailable-back"
                >
                  {t("page.unavailable_back")}
                </Link>
              </Button>
            }
          />
        </div>
      ) : (
        <>
      {isHistorical ? null : <GovernanceBand projectId={projectId} />}

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
            // Same guard as the Vulnerabilities and Compliance tabs below:
            // no button on a demo deployment, a historical snapshot, or
            // before the project has resolved.
            onScan={
              project != null && !writesDisabled && !isHistorical
                ? () => setSourceDialogOpen(true)
                : undefined
            }
          />
        </TabsContent>
        <TabsContent value="releases">
          <ReleasesTab
            projectId={projectId}
            onViewSnapshot={handleViewSnapshotComponents}
          />
        </TabsContent>
        <TabsContent value="components">
          <ComponentsTab
            projectId={projectId}
            scanId={pinnedScanId}
            onScan={
              project != null && !writesDisabled && !isHistorical
                ? () => setSourceDialogOpen(true)
                : undefined
            }
          />
        </TabsContent>
        <TabsContent value="vulnerabilities">
          <VulnerabilitiesTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
            scanId={pinnedScanId}
            readOnly={isHistorical}
            onScan={
              project != null && !writesDisabled && !isHistorical
                ? () => setSourceDialogOpen(true)
                : undefined
            }
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
            teamId={project?.team_id ?? null}
            projectRole={overview.data?.current_user_role ?? "developer"}
            readOnly={isHistorical}
            onScan={
              project != null && !writesDisabled && !isHistorical
                ? () => setSourceDialogOpen(true)
                : undefined
            }
          />
        </TabsContent>
        <TabsContent value="reports">
          <ReportsTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
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
        </>
      )}

      {project ? (
        <SourceSelectDialog
          open={sourceDialogOpen}
          onOpenChange={setSourceDialogOpen}
          project={project}
          onScanStarted={handleScanStarted}
        />
      ) : null}

      {project ? (
        <SbomIngestDialog
          open={sbomIngestOpen}
          onOpenChange={setSbomIngestOpen}
          project={project}
          onIngestStarted={handleScanStarted}
        />
      ) : null}

      <Sheet
        open={scanDrawer.open}
        onOpenChange={(open) => setScanDrawer((s) => ({ ...s, open }))}
      >
        <SheetContent
          side="right"
          // Default Sheet width (sm:max-w-md ≈ 448px) crammed the tool log
          // panel — long ScanCode warnings / Trivy report lines wrapped after
          // ~30 chars. The progress drawer hosts per-step + tool log panels
          // that benefit from monospace lines breathing, so widen this sheet
          // specifically (other drawers stay at the default).
          className="flex flex-col gap-4 sm:max-w-3xl"
          data-testid="scan-progress-drawer"
        >
          <SheetHeader>
            <SheetTitle>{project?.name ?? ""}</SheetTitle>
            <SheetDescription>{t("page.scan_drawer_subtitle")}</SheetDescription>
          </SheetHeader>
          {scanDrawer.scanId ? (
            <>
              <ScanProgress
                scanId={scanDrawer.scanId}
                release={scanDrawer.release}
                status={scanDrawer.status ?? "queued"}
                onClose={() => setScanDrawer((s) => ({ ...s, open: false }))}
                onCancelled={() =>
                  setScanDrawer((s) => ({ ...s, status: "cancelled" }))
                }
                hideInlineLog
              />
              {/*
               * Always-visible link out to the dedicated full-page log view.
               * The inline log panel was removed from the drawer (it cramped
               * long Trivy / ScanCode lines into ~30 chars); this link takes
               * the user to a real route they can deep-link and download
               * the log from.
               */}
              <div className="mt-2 border-t pt-3">
                <Link
                  to={`/scans/${scanDrawer.scanId}`}
                  className="text-sm text-primary hover:underline focus-visible:underline focus-visible:outline-none"
                  data-testid="scan-drawer-open-full-log"
                >
                  {t("scans:progress.open_full_log")}
                </Link>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

interface ProjectDetailHeaderProps {
  projectId: string;
  projectName: string | null;
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
  /** The active ad-hoc scan that would actually conflict with the button. */
  blockingScan: ScanSummary | null;
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
  isProjectLoading,
  isProjectError,
  projectError,
  canScan,
  demoReadOnly,
  onScan,
  activeScan,
  blockingScan,
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
        // G0-6 follow-up (F3): this row used to be a single unconditional
        // flex row (`items-center justify-between`). At 390 px the
        // breadcrumb/title/id/release-switcher block on the left and the
        // scan chip + Scan button on the right needed ~2 px more than the
        // viewport had, so the row spilled past the edge (the child blocks
        // stayed under 390 px individually, only their sum plus padding
        // and gap did not, which is why the overflow showed up on this
        // `<header>` and its page wrapper, not on any control inside it).
        // Below `sm` the two blocks stack instead of sharing a row, the
        // same `flex-col … sm:flex-row` shape `ComponentsToolbar` already
        // uses for its filter bar. At `sm` and up this renders exactly as
        // before.
        "flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:px-6",
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
            className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
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
          {/* G0-5 — no release picker for a project that does not load. It
              rendered "No version" next to a "not found" heading, which reads
              as a project that exists and happens to be unscanned. */}
          {isProjectError ? null : (
            <ReleaseSwitcher
              projectId={projectId}
              pinnedScanId={pinnedScanId}
              latestScanId={latestScanId}
              isHistorical={isHistorical}
              onSelectRelease={onSelectRelease}
              onSelectLatest={onSelectLatest}
            />
          )}
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
            className="inline-flex items-center gap-1.5 rounded-full border border-risk-low/40 bg-risk-low/10 px-2.5 py-1 text-xs font-medium text-risk-low-foreground transition-colors duration-fast ease-out-soft hover:bg-risk-low/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
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
        {/* G0-5 — and no Scan button either. Disabled was not enough: a greyed
            "Scan" beside "Project not found" still offers an action against a
            project there is nothing to scan. */}
        {isProjectError ? null : (
        <Button
          size="sm"
          onClick={onScan}
          disabled={!canScan || blockingScan !== null}
          title={
            demoReadOnly
              ? t("page.scan_demo_disabled")
              : blockingScan !== null
                ? t("page.scan_already_active", {
                    defaultValue:
                      "A scan is already running for this project — open the in-progress drawer to view it.",
                  })
                : undefined
          }
          data-testid="project-detail-scan"
          data-scan-blocked={blockingScan !== null ? "active" : undefined}
        >
          {t("page.scan")}
        </Button>
        )}
      </div>
    </header>
  );
}

