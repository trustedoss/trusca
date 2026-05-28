import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  ComponentDetailResponse,
  VulnerabilityRef,
} from "@/features/projects/api/projectDetailApi";
import { DependencyScopeBadge } from "@/features/projects/components/DependencyScopeBadge";
import { DependencyTypeBadge } from "@/features/projects/components/DependencyTypeBadge";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import {
  formatEpssPercentile,
  formatEpssScore,
} from "@/features/projects/lib/epss";
import { cn } from "@/lib/utils";

/**
 * ComponentDetailBody — W10-E.
 *
 * Surface-agnostic body for a component detail. The same data is rendered by
 * two surfaces:
 *
 *   1. `ComponentDrawer` — quick check from a list (Sheet wrapper).
 *   2. `ComponentDetailPage` — deep work, full-page route.
 *
 * This file owns only the *content* (meta panel, vulnerabilities list, raw_data
 * accordion). The surrounding shell (Sheet header, page header, close button,
 * scroll container) belongs to each surface.
 *
 * Mirrors the W10-A `VulnerabilityDetailBody` split for the component domain.
 * Test-id convention: every `data-testid` keeps the `component-drawer-*`
 * prefix so existing unit + e2e tests continue to pass unchanged. The prefix
 * is now slightly misleading (the body is no longer drawer-only) but renaming
 * it would be a parallel test-suite migration — out of scope for this phase.
 */

const SEVERITY_TONE: Record<
  string,
  "critical" | "high" | "medium" | "low" | "info"
> = {
  critical: "critical",
  high: "high",
  medium: "medium",
  low: "low",
  info: "info",
};

function vulnerabilityTone(severity: string) {
  return SEVERITY_TONE[severity.toLowerCase()] ?? "info";
}

export interface ComponentDetailBodyProps {
  /** The loaded component detail row. */
  detail: ComponentDetailResponse;
}

export function ComponentDetailBody({ detail }: ComponentDetailBodyProps) {
  const { t } = useTranslation("project_detail");
  // Local UI state: raw_data accordion open / closed. The toggle never leaves
  // the body, so it lives here instead of on the surface — closing the
  // drawer (or navigating away from the page) naturally resets it.
  const [rawOpen, setRawOpen] = useState(false);

  return (
    <div className="flex flex-col gap-5">
      <section
        className="flex flex-col gap-2"
        data-testid="component-drawer-meta"
      >
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={detail.severity_max} />
          <LicenseCategoryBadge category={detail.license_category} />
          {detail.license ? (
            <Badge tone="info" data-testid="component-license-name">
              {detail.license}
            </Badge>
          ) : null}
        </div>
        {detail.purl ? (
          <div className="font-mono text-xs text-muted-foreground">
            <span className="mr-2 uppercase tracking-wide">
              {t("drawer.purl_label")}
            </span>
            <span data-testid="component-drawer-purl">{detail.purl}</span>
          </div>
        ) : null}
        {/*
          * W2 #31 — BD-style Type + Usage rows.
          * Always rendered (even when depth is null / scope is null) so
          * the drawer's information layout doesn't jump between
          * components and the badge itself can express "—".
          */}
        <div
          className="flex items-center gap-2 text-xs"
          data-testid="component-drawer-dependency-type"
        >
          <span className="uppercase tracking-wide text-muted-foreground">
            {t("drawer.dependency_type_label")}
          </span>
          <DependencyTypeBadge direct={detail.direct} depth={detail.depth} />
        </div>
        <div
          className="flex items-center gap-2 text-xs"
          data-testid="component-drawer-usage"
        >
          <span className="uppercase tracking-wide text-muted-foreground">
            {t("drawer.usage_label")}
          </span>
          <DependencyScopeBadge scope={detail.dependency_scope} />
        </div>
      </section>

      <section
        className="flex flex-col gap-2"
        data-testid="component-drawer-vulns"
      >
        <h3 className="text-sm font-semibold">
          {t("drawer.vulns.title", {
            count: detail.vulnerabilities.length,
          })}
        </h3>
        {detail.vulnerabilities.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("drawer.vulns.empty")}
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {detail.vulnerabilities.map((vuln) => (
              <VulnerabilityRow key={vuln.cve_id} vuln={vuln} />
            ))}
          </ul>
        )}
      </section>

      <section
        className="flex flex-col gap-2"
        data-testid="component-drawer-raw"
      >
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setRawOpen((o) => !o)}
          aria-expanded={rawOpen}
          aria-controls="component-raw-data"
          data-testid="component-drawer-raw-toggle"
          className="self-start"
        >
          {rawOpen ? t("drawer.raw.hide") : t("drawer.raw.show")}
        </Button>
        {rawOpen ? (
          <pre
            id="component-raw-data"
            data-testid="component-drawer-raw-json"
            className={cn(
              "max-h-72 overflow-auto rounded-md border bg-muted p-3 font-mono text-xs",
            )}
          >
            {JSON.stringify(detail.raw_data, null, 2)}
          </pre>
        ) : null}
      </section>
    </div>
  );
}

function VulnerabilityRow({ vuln }: { vuln: VulnerabilityRef }) {
  const { t } = useTranslation("project_detail");
  const epssScore = formatEpssScore(vuln.epss_score);
  const epssPercentile = formatEpssPercentile(vuln.epss_percentile);
  return (
    <li
      data-testid="component-drawer-vuln"
      data-cve-id={vuln.cve_id}
      className="flex flex-col gap-1 rounded-md border p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          tone={vulnerabilityTone(vuln.severity)}
          data-testid="component-drawer-vuln-severity"
        >
          {vuln.severity}
        </Badge>
        <span className="font-mono text-xs">{vuln.cve_id}</span>
        {vuln.cvss != null ? (
          <span className="text-xs text-muted-foreground">
            {t("drawer.vulns.cvss_label")}: {vuln.cvss.toFixed(1)}
          </span>
        ) : null}
        {epssScore != null ? (
          <span
            className="font-mono text-xs text-muted-foreground"
            data-testid="component-drawer-vuln-epss"
            data-epss-score={vuln.epss_score ?? undefined}
            title={t("vulnerabilities.epss.tooltip", {
              defaultValue:
                "EPSS — probability this CVE is exploited in the wild within 30 days. Complements CVSS (severity).",
            })}
          >
            {t("drawer.vulns.epss_label", { defaultValue: "EPSS" })}:{" "}
            {epssScore}
            {epssPercentile != null ? ` (${epssPercentile})` : ""}
          </span>
        ) : null}
      </div>
      <div className="text-sm font-medium">{vuln.title}</div>
      {vuln.description ? (
        <p className="text-xs text-muted-foreground">{vuln.description}</p>
      ) : null}
      {vuln.fixed_version ? (
        <div className="text-xs">
          <span className="text-muted-foreground">
            {t("drawer.vulns.fixed_in")}:
          </span>{" "}
          <span className="font-mono">{vuln.fixed_version}</span>
        </div>
      ) : null}
    </li>
  );
}
