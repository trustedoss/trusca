import { ShieldCheck, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useGateResult } from "@/features/projects/api/useGateResult";
import { projectErrorMessageKey } from "@/features/projects/lib/projectErrorMessage";
import type { GateResultResponse } from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * GateResultCard — v2.1 UI gap #1.
 *
 * Surfaces the build-blocking policy-gate verdict the CI pipeline computes,
 * evaluated against the project's most recent successful scan. Sits on the
 * Overview tab next to the risk gauge / distributions.
 *
 * The verdict pairs a color with an icon and a localized label so color is
 * never the only signal (CLAUDE.md "디자인 시스템" + accessibility rule):
 *   - pass → emerald `success` badge + ShieldCheck
 *   - fail → red destructive badge + ShieldX
 *
 * When the project has never had a successful scan the backend returns
 * `gate: "pass"` with `scan_id: null`; we render that as a neutral
 * "no scan yet" state instead of a misleading green pass.
 *
 * Loading = skeleton (no spinner); errors surface the RFC 7807 title/detail.
 */

export interface GateResultCardProps {
  projectId: string;
}

export function GateResultCard({ projectId }: GateResultCardProps) {
  const { t } = useTranslation("project_detail");
  const query = useGateResult(projectId);

  if (query.isLoading) {
    return (
      <Card data-testid="gate-card-loading">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.gate_card.title", { defaultValue: "Build gate" })}
          </CardTitle>
          <CardDescription>
            {t("overview.gate_card.subtitle", {
              defaultValue:
                "Build-blocking verdict from the latest successful scan.",
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-7 w-24" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (query.isError) {
    // BUG-002: map the RFC 7807 problem to a localized key instead of
    // rendering the backend's English `title`/`detail` (which leaked into the
    // KO locale). The structured count-based reason below covers the success
    // path; this covers the load-failure path.
    const title = t("overview.gate_card.error_title", {
      defaultValue: "Could not load the build gate",
    });
    const detail = t(
      projectErrorMessageKey(query.error, "overview.gate_card.errors"),
      { defaultValue: "Please try again." },
    );
    return (
      <Card data-testid="gate-card-error-wrap">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.gate_card.title", { defaultValue: "Build gate" })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive" data-testid="gate-card-error">
            <AlertDescription>
              <div className="font-semibold">{title}</div>
              <div className="text-sm">{detail}</div>
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  const data = query.data;
  if (!data) return null;

  // No successful scan yet → backend convention is gate=pass / scan_id=null.
  // Render a neutral "nothing to gate on" state so we never imply a real pass.
  const noScan = data.scan_id == null;
  const passed = data.gate === "pass";

  return (
    <Card
      data-testid="gate-card"
      data-gate={noScan ? "none" : data.gate}
    >
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">
            {t("overview.gate_card.title", { defaultValue: "Build gate" })}
          </CardTitle>
          {noScan ? (
            <Badge tone="info" data-testid="gate-badge-none">
              {t("overview.gate_card.no_scan_badge", {
                defaultValue: "No scan yet",
              })}
            </Badge>
          ) : passed ? (
            <Badge
              tone="success"
              className="gap-1.5"
              data-testid="gate-badge-pass"
            >
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
              {t("overview.gate_card.pass", { defaultValue: "Pass" })}
            </Badge>
          ) : (
            <Badge
              variant="destructive"
              className="gap-1.5"
              data-testid="gate-badge-fail"
            >
              <ShieldX className="h-3.5 w-3.5" aria-hidden />
              {t("overview.gate_card.fail", { defaultValue: "Fail" })}
            </Badge>
          )}
        </div>
        <CardDescription>
          {t("overview.gate_card.subtitle", {
            defaultValue:
              "Build-blocking verdict from the latest successful scan.",
          })}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {noScan ? (
          <p
            className="text-sm text-muted-foreground"
            data-testid="gate-no-scan"
          >
            {t("overview.gate_card.no_scan_detail", {
              defaultValue:
                "Run a successful scan to evaluate the build gate.",
            })}
          </p>
        ) : (
          <>
            {!passed ? (
              <GateFailReason data={data} />
            ) : null}
            {passed ? (
              <p
                className="text-sm text-muted-foreground"
                data-testid="gate-pass-detail"
              >
                {t("overview.gate_card.pass_detail", {
                  defaultValue:
                    "No critical CVEs or forbidden licenses block this build.",
                })}
              </p>
            ) : null}

            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <GateMetric
                label={t("overview.gate_card.critical_cves", {
                  defaultValue: "Critical CVEs",
                })}
                value={data.critical_cve_count}
                emphasize={data.critical_cve_count > 0}
                testid="gate-metric-critical"
              />
              <GateMetric
                label={t("overview.gate_card.forbidden_licenses", {
                  defaultValue: "Forbidden licenses",
                })}
                value={data.forbidden_license_count}
                emphasize={data.forbidden_license_count > 0}
                testid="gate-metric-forbidden"
              />
              {data.epss_threshold != null ? (
                <GateMetric
                  label={t("overview.gate_card.epss_findings", {
                    defaultValue: "EPSS ≥ {{threshold}}",
                    threshold: data.epss_threshold,
                  })}
                  value={data.epss_gate_count}
                  emphasize={data.epss_gate_count > 0}
                  testid="gate-metric-epss"
                />
              ) : null}
            </dl>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * GateFailReason — BUG-002.
 *
 * Composes a localized failure reason from the gate's structured counts
 * instead of rendering the backend's English `data.reason`. Each count > 0
 * contributes one ICU-pluralized clause (so KO renders Korean and EN renders
 * English). When no count is positive (e.g. a forward-compat fail reason the
 * frontend doesn't model yet) we fall back to a generic localized message and
 * still surface the metric grid below.
 */
function GateFailReason({ data }: { data: GateResultResponse }) {
  const { t } = useTranslation("project_detail");

  const clauses: string[] = [];
  if (data.critical_cve_count > 0) {
    clauses.push(
      t("overview.gate_card.reason.critical_cve", {
        count: data.critical_cve_count,
      }),
    );
  }
  if (data.forbidden_license_count > 0) {
    clauses.push(
      t("overview.gate_card.reason.forbidden_license", {
        count: data.forbidden_license_count,
      }),
    );
  }
  if (data.epss_threshold != null && data.epss_gate_count > 0) {
    clauses.push(
      t("overview.gate_card.reason.epss", {
        count: data.epss_gate_count,
        threshold: data.epss_threshold,
      }),
    );
  }

  return (
    <div
      className="text-sm font-medium text-risk-critical"
      data-testid="gate-reason"
      data-reason-clauses={clauses.length}
      aria-live="polite"
    >
      {clauses.length > 0 ? (
        <>
          <p>{t("overview.gate_card.reason.intro")}</p>
          <ul className="ml-4 list-disc">
            {clauses.map((clause, idx) => (
              <li key={idx}>{clause}</li>
            ))}
          </ul>
        </>
      ) : (
        <p>{t("overview.gate_card.reason.fallback")}</p>
      )}
    </div>
  );
}

interface GateMetricProps {
  label: string;
  value: number;
  emphasize: boolean;
  testid: string;
}

function GateMetric({ label, value, emphasize, testid }: GateMetricProps) {
  return (
    <div
      className="rounded-md border bg-muted/40 px-3 py-2"
      data-testid={testid}
      data-value={value}
    >
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "mt-0.5 font-mono text-lg font-semibold tabular-nums",
          emphasize ? "text-risk-critical" : "text-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}
