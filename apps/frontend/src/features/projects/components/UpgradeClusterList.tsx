import { ChevronRight, PackageCheck, PackageX } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type {
  UpgradeCluster,
  UpgradeClusterFinding,
} from "@/features/projects/api/vulnerabilitiesApi";
import { KevBadge } from "@/features/projects/components/KevBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { EPSS_EMPTY, formatEpssScore } from "@/features/projects/lib/epss";
import { cn } from "@/lib/utils";

/**
 * UpgradeClusterList — W9-#53 "Group by upgrade".
 *
 * Renders the Vulnerabilities tab's grouped view: one collapsible card per
 * minimum-safe-upgrade cluster (every open finding on a component_version that
 * a single version bump resolves). Cards arrive pre-sorted most-actionable
 * first from the backend, so this component renders `clusters` verbatim.
 *
 * A cluster whose `reason === "ok"` carries a concrete `recommended_version`
 * and reads "Upgrade {name} {current} → {recommended}"; the other reasons
 * (`no_fix_version` / `unparseable_version` / `no_open_findings`) surface a
 * "No upgrade available" label plus the distinguishing reason. Either way the
 * card shows the count of findings the bump would resolve and the worst
 * severity / KEV / direct signals so the highest-leverage bump is obvious.
 *
 * Expanding a card reveals its finding rows; clicking one calls
 * `onOpenFinding(finding_id)`, which the parent tab wires to the SAME
 * `VulnerabilityDrawer` the flat list opens (no second drawer). The list is
 * non-virtualized — the cluster count is modest even on large projects (one
 * per vulnerable component, not per finding) — while keeping the 40px compact
 * density and design tokens the flat rows use.
 */

export interface UpgradeClusterListProps {
  clusters: UpgradeCluster[];
  /** Open the shared vulnerability drawer keyed by `finding_id`. */
  onOpenFinding: (findingId: string) => void;
}

export function UpgradeClusterList({
  clusters,
  onOpenFinding,
}: UpgradeClusterListProps) {
  return (
    <div
      className="flex flex-1 flex-col overflow-y-auto"
      data-testid="vulnerabilities-upgrade-list"
      data-cluster-count={clusters.length}
    >
      {clusters.map((cluster) => (
        <UpgradeClusterCard
          key={cluster.component_version_id}
          cluster={cluster}
          onOpenFinding={onOpenFinding}
        />
      ))}
    </div>
  );
}

interface UpgradeClusterCardProps {
  cluster: UpgradeCluster;
  onOpenFinding: (findingId: string) => void;
}

function UpgradeClusterCard({
  cluster,
  onOpenFinding,
}: UpgradeClusterCardProps) {
  const { t } = useTranslation("project_detail");
  const [expanded, setExpanded] = useState(false);

  const isUpgradeable = cluster.reason === "ok" &&
    cluster.recommended_version != null;
  const anyKev = cluster.findings.some((f) => f.kev);

  return (
    <div
      className="border-b border-border/60 bg-card"
      data-testid="vulnerability-upgrade-cluster"
      data-component-version-id={cluster.component_version_id}
      data-reason={cluster.reason}
      data-recommended-version={cluster.recommended_version ?? ""}
      data-finding-count={cluster.finding_count}
      data-direct={cluster.direct ? "true" : "false"}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        data-testid="vulnerability-upgrade-cluster-header"
        className={cn(
          "flex w-full items-center gap-3 px-6 text-left text-sm transition-colors duration-fast ease-out-soft hover:bg-accent",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        )}
        style={{ minHeight: "var(--table-row)" }}
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-fast ease-out-soft",
            expanded && "rotate-90",
          )}
        />
        {isUpgradeable ? (
          <PackageCheck
            aria-hidden
            className="h-4 w-4 shrink-0 text-risk-low"
          />
        ) : (
          <PackageX
            aria-hidden
            className="h-4 w-4 shrink-0 text-muted-foreground"
          />
        )}

        <span className="flex min-w-0 flex-1 flex-col py-1.5">
          {isUpgradeable ? (
            <span
              className="truncate font-mono text-xs text-foreground"
              data-testid="vulnerability-upgrade-cluster-recommended"
              data-recommended-version={cluster.recommended_version ?? ""}
            >
              {t("vulnerabilities.upgrade_cluster.upgrade_to", {
                name: cluster.component_name,
                from: cluster.current_version,
                to: cluster.recommended_version,
              })}
            </span>
          ) : (
            <span className="flex flex-col">
              <span className="truncate text-xs font-medium text-foreground">
                {t("vulnerabilities.upgrade_cluster.no_upgrade")}
                {": "}
                <span className="font-mono font-normal text-muted-foreground">
                  {cluster.component_name}@{cluster.current_version}
                </span>
              </span>
              <span className="truncate text-[11px] text-muted-foreground">
                {reasonHint(cluster.reason, t)}
              </span>
            </span>
          )}
        </span>

        {cluster.direct ? (
          <Badge
            tone="info"
            className="shrink-0 text-[10px]"
            data-testid="vulnerability-upgrade-cluster-direct"
          >
            {t("vulnerabilities.upgrade_cluster.direct")}
          </Badge>
        ) : null}

        {anyKev ? <KevBadge kev className="shrink-0" /> : null}

        {cluster.max_severity != null ? (
          <SeverityBadge
            severity={cluster.max_severity}
            className="shrink-0"
          />
        ) : null}

        <span
          className="shrink-0 rounded bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground"
          data-testid="vulnerability-upgrade-cluster-fixes"
          data-finding-count={cluster.finding_count}
        >
          {t("vulnerabilities.upgrade_cluster.fixes_count", {
            count: cluster.finding_count,
          })}
        </span>
      </button>

      {expanded ? (
        <div data-testid="vulnerability-upgrade-cluster-findings">
          {cluster.findings.map((finding) => (
            <UpgradeClusterFindingRow
              key={finding.finding_id}
              finding={finding}
              onOpen={() => onOpenFinding(finding.finding_id)}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

interface UpgradeClusterFindingRowProps {
  finding: UpgradeClusterFinding;
  onOpen: () => void;
}

function UpgradeClusterFindingRow({
  finding,
  onOpen,
}: UpgradeClusterFindingRowProps) {
  const { t } = useTranslation("project_detail");
  const epss = formatEpssScore(finding.epss_score);
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="vulnerability-upgrade-finding"
      data-finding-id={finding.finding_id}
      data-cve-id={finding.cve_id}
      className={cn(
        "flex w-full items-center gap-3 border-t border-border/40 bg-background px-6 pl-14 text-left text-sm transition-colors duration-fast ease-out-soft hover:bg-accent",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span
        className="w-44 truncate font-mono text-xs"
        title={finding.cve_id}
      >
        {finding.cve_id}
      </span>
      <SeverityBadge severity={finding.severity} className="shrink-0" />
      {finding.kev ? <KevBadge kev className="shrink-0" /> : null}
      <span
        className="w-20 text-right font-mono text-xs tabular-nums text-muted-foreground"
        title={t("vulnerabilities.column.epss")}
      >
        {epss ?? EPSS_EMPTY}
      </span>
      <span className="flex-1 truncate text-right font-mono text-xs text-muted-foreground">
        {finding.fixed_version != null
          ? t("vulnerabilities.upgrade_cluster.finding_fixed_version", {
              version: finding.fixed_version,
            })
          : EPSS_EMPTY}
      </span>
    </button>
  );
}

/**
 * Distinguishing sub-label for a cluster with no concrete upgrade. The three
 * non-`ok` reasons read differently to a triager: some CVEs simply have no
 * published fix yet, versus a fix version we could not parse as semver.
 */
function reasonHint(
  reason: UpgradeCluster["reason"],
  t: (key: string) => string,
): string {
  if (reason === "unparseable_version") {
    return t("vulnerabilities.upgrade_cluster.unparseable_reason");
  }
  // no_fix_version + no_open_findings both read as "no fix available yet".
  return t("vulnerabilities.upgrade_cluster.no_fix_reason");
}
