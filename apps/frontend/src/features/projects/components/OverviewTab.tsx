import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { GateResultCard } from "@/features/projects/components/GateResultCard";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { RecentScansTable } from "@/features/projects/components/RecentScansTable";
import { RiskGauge } from "@/features/projects/components/RiskGauge";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
import { ProblemError } from "@/lib/problem";

/**
 * OverviewTab — Phase 3 PR #10.
 *
 * Top section of the project detail page. Aggregates the project's risk
 * gauge, severity / license distributions, and the five most recent scans.
 * Wraps the `useProjectOverview` query with skeleton + RFC 7807 error UI.
 */

export interface OverviewTabProps {
  projectId: string;
}

export function OverviewTab({ projectId }: OverviewTabProps) {
  const { t } = useTranslation("project_detail");
  const overview = useProjectOverview(projectId);

  if (overview.isLoading) {
    return (
      <div
        data-testid="overview-loading"
        className="grid gap-4 p-6 md:grid-cols-2"
      >
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-32 w-full md:col-span-2" />
        <Skeleton className="h-48 w-full md:col-span-2" />
      </div>
    );
  }

  if (overview.isError) {
    const err = overview.error;
    const title =
      err instanceof ProblemError ? err.title : t("overview.errors.title");
    const detail =
      err instanceof ProblemError && err.detail
        ? err.detail
        : t("overview.errors.detail");
    return (
      <div className="p-6">
        <Alert variant="destructive" data-testid="overview-error">
          <AlertDescription>
            <div className="font-semibold">{title}</div>
            <div className="text-sm">{detail}</div>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const data = overview.data;
  if (!data) return null;

  return (
    <div
      data-testid="overview-tab"
      data-total-components={data.total_components}
      className="grid gap-4 p-6 md:grid-cols-2"
    >
      <Card data-testid="overview-risk-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.risk_card.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.risk_card.subtitle", {
              total: data.total_components,
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center pt-0">
          <RiskGauge score={data.risk_score} />
        </CardContent>
      </Card>

      <GateResultCard projectId={projectId} />

      <Card data-testid="overview-severity-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.severity_card.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.severity_card.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SeverityDistributionChart
            distribution={data.severity_distribution}
          />
        </CardContent>
      </Card>

      <Card className="md:col-span-2" data-testid="overview-license-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.license_card.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.license_card.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LicenseDistributionChart distribution={data.license_distribution} />
        </CardContent>
      </Card>

      <Card className="md:col-span-2" data-testid="overview-recent-scans-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.recent_scans.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.recent_scans.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RecentScansTable scans={data.recent_scans} />
        </CardContent>
      </Card>
    </div>
  );
}
