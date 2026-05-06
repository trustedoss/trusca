import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { VulnerabilityRef } from "@/features/projects/api/projectDetailApi";
import { useComponent } from "@/features/projects/api/useComponent";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * ComponentDrawer — Phase 3 PR #10.
 *
 * Right-side Sheet drawer rendered for the currently-selected component.
 * Lazy-fetches `GET /v1/components/{id}` and shows:
 *
 *   - Header: name, version, purl, severity / license badges.
 *   - Vulnerabilities list: CVE id, severity, CVSS, title, description,
 *     fixed_version. CVE id rendered as plain text (no anchor) — links go
 *     out to NVD in a follow-up to keep XSS surface small.
 *   - raw_data accordion: collapsible JSON viewer (read-only `<pre>`,
 *     stringified through `JSON.stringify` so no HTML injection is possible).
 *
 * Accessibility: ESC closes (Radix), focus trap inside, "color is not the
 * only signal" — every severity carries a label.
 */

const SEVERITY_TONE: Record<string, "critical" | "high" | "medium" | "low" | "info"> = {
  critical: "critical",
  high: "high",
  medium: "medium",
  low: "low",
  info: "info",
};

function vulnerabilityTone(severity: string) {
  return SEVERITY_TONE[severity.toLowerCase()] ?? "info";
}

export interface ComponentDrawerProps {
  open: boolean;
  componentId: string | null;
  onOpenChange: (open: boolean) => void;
}

export function ComponentDrawer({
  open,
  componentId,
  onOpenChange,
}: ComponentDrawerProps) {
  const { t } = useTranslation("project_detail");
  const detail = useComponent(open ? componentId : null);
  const [rawOpen, setRawOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-xl flex-col gap-4 overflow-y-auto sm:max-w-xl"
        data-testid="component-drawer"
      >
        <SheetHeader>
          <SheetTitle data-testid="component-drawer-title">
            {detail.data?.name ?? t("drawer.loading_title")}
          </SheetTitle>
          <SheetDescription>
            {detail.data
              ? t("drawer.subtitle", {
                  version: detail.data.version,
                })
              : t("drawer.loading_subtitle")}
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div
            className="flex flex-col gap-3"
            data-testid="component-drawer-loading"
          >
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : null}

        {detail.isError ? (
          <Alert variant="destructive" data-testid="component-drawer-error">
            <AlertDescription>
              {detail.error instanceof ProblemError
                ? detail.error.detail
                : t("drawer.error")}
            </AlertDescription>
          </Alert>
        ) : null}

        {detail.data ? (
          <div className="flex flex-col gap-5">
            <section
              className="flex flex-col gap-2"
              data-testid="component-drawer-meta"
            >
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={detail.data.severity_max} />
                <LicenseCategoryBadge category={detail.data.license_category} />
                {detail.data.license ? (
                  <Badge tone="info" data-testid="component-license-name">
                    {detail.data.license}
                  </Badge>
                ) : null}
              </div>
              {detail.data.purl ? (
                <div className="font-mono text-xs text-muted-foreground">
                  <span className="mr-2 uppercase tracking-wide">
                    {t("drawer.purl_label")}
                  </span>
                  <span data-testid="component-drawer-purl">
                    {detail.data.purl}
                  </span>
                </div>
              ) : null}
            </section>

            <section
              className="flex flex-col gap-2"
              data-testid="component-drawer-vulns"
            >
              <h3 className="text-sm font-semibold">
                {t("drawer.vulns.title", {
                  count: detail.data.vulnerabilities.length,
                })}
              </h3>
              {detail.data.vulnerabilities.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("drawer.vulns.empty")}
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {detail.data.vulnerabilities.map((vuln) => (
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
                  {JSON.stringify(detail.data.raw_data, null, 2)}
                </pre>
              ) : null}
            </section>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function VulnerabilityRow({ vuln }: { vuln: VulnerabilityRef }) {
  const { t } = useTranslation("project_detail");
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
