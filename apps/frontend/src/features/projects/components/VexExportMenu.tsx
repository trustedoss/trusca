import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useVexExport } from "@/features/projects/api/useVexExport";
import type { VexFormat } from "@/features/projects/api/vexApi";
import { cn } from "@/lib/utils";

/**
 * VexExportMenu — v2.1 Track A (A3).
 *
 * Two discrete download buttons (OpenVEX / CycloneDX VEX) that export the
 * project's current finding triage as a VEX document. Reuses the SBOM-tab
 * download UX (per-button busy state, blob download through the authenticated
 * axios instance) so the bearer token stays in the Authorization header, never
 * on the URL. Available to any reader (developer↑) — export is a read, matching
 * the backend `require_role("developer")` gate.
 *
 * Inline (no modal) per the design system. An error from the last attempt is
 * rendered inline below the buttons.
 */

const FORMATS: VexFormat[] = ["openvex", "cyclonedx"];

export interface VexExportMenuProps {
  projectId: string;
  projectName?: string | null;
  className?: string;
}

export function VexExportMenu({
  projectId,
  projectName,
  className,
}: VexExportMenuProps) {
  const { t } = useTranslation("project_detail");
  const exporter = useVexExport(projectId, projectName);

  return (
    <div className={cn("flex flex-col", className)} data-testid="vex-export">
      <span className="text-xs font-medium text-muted-foreground">
        {t("vulnerabilities.vex.export_label")}
      </span>
      <div className="mt-1 flex flex-wrap gap-1">
        {FORMATS.map((format) => (
          <Button
            key={format}
            type="button"
            variant="outline"
            size="sm"
            className="h-9"
            disabled={exporter.busyFormat !== null}
            onClick={() => {
              // Errors surface via exporter.error below — swallow the rejection
              // so it doesn't bubble as an unhandled promise rejection.
              exporter.download(format).catch(() => {
                /* surfaced inline via exporter.error */
              });
            }}
            data-testid={`vex-export-${format}`}
            data-format={format}
          >
            {exporter.busyFormat === format
              ? t("vulnerabilities.vex.export_downloading")
              : t(`vulnerabilities.vex.export_${format}`)}
          </Button>
        ))}
      </div>
      {exporter.error ? (
        <span
          className="mt-1 text-xs text-destructive"
          data-testid="vex-export-error"
          aria-live="polite"
        >
          {exporter.error.message}
        </span>
      ) : null}
    </div>
  );
}
