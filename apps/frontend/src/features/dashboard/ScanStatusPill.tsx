/**
 * ScanStatusPill — local status badge used in the DashboardPage recent-scans
 * table. Lives in its own file so the i18next-parser can route its
 * `t("page.status.<x>")` lookup to the `scans` namespace cleanly; mixing
 * three namespaces (`dashboard`, `projects`, `scans`) in one file confuses
 * the static analyzer used by `npm run i18n:check`.
 */
import { useTranslation } from "react-i18next";

import { type ScanStatus } from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

const STATUS_TONE_CLASS: Record<ScanStatus, string> = {
  succeeded: "border-emerald-300 bg-emerald-50 text-emerald-700",
  running: "border-blue-300 bg-blue-50 text-blue-700",
  queued: "border-amber-300 bg-amber-50 text-amber-700",
  failed: "border-red-300 bg-red-50 text-red-700",
  cancelled: "border-muted bg-muted text-muted-foreground",
};

export function ScanStatusPill({ status }: { status: ScanStatus }) {
  const { t } = useTranslation("scans");
  return (
    <span
      data-testid="dashboard-scan-status"
      data-status={status}
      className={cn(
        "inline-flex items-center rounded border px-2 py-0.5 font-mono text-[11px]",
        STATUS_TONE_CLASS[status],
      )}
    >
      {t(`page.status.${status}`)}
    </span>
  );
}
