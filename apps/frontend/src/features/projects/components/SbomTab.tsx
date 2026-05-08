/**
 * SbomTab — Phase 3 / Step 4-A.
 *
 * Lists the four SBOM export formats the backend supports
 * (CycloneDX JSON / XML, SPDX JSON / Tag-Value) as discrete download
 * buttons. Each click streams the document as a Blob through axios so the
 * bearer token stays in the Authorization header (NOT on the URL/history),
 * then triggers a transient `<a download>` click via a blob URL.
 *
 * Per-button download state is tracked locally so a slow request only
 * disables the button the user clicked, not the entire tab.
 */
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ProblemError } from "@/lib/problem";
import { downloadSbom, type SbomFormat } from "@/lib/projectsApi";

interface SbomTabProps {
  projectId: string;
  /** Latest scan timestamp (ISO-8601). Optional; rendered when present. */
  lastScanAt?: string | null;
}

interface SbomFormatRow {
  format: SbomFormat;
  testIdSuffix: string;
}

const FORMATS: SbomFormatRow[] = [
  { format: "cyclonedx-json", testIdSuffix: "cyclonedx-json" },
  { format: "cyclonedx-xml", testIdSuffix: "cyclonedx-xml" },
  { format: "spdx-json", testIdSuffix: "spdx-json" },
  { format: "spdx-tv", testIdSuffix: "spdx-tv" },
];

function triggerBrowserDownload(blob: Blob, filename: string) {
  if (typeof document === "undefined" || typeof URL === "undefined") return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Defer revocation so the browser starts the download before we drop the URL.
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export function SbomTab({ projectId, lastScanAt }: SbomTabProps) {
  const { t, i18n } = useTranslation("project_detail");
  const [busyFormat, setBusyFormat] = useState<SbomFormat | null>(null);
  const [error, setError] = useState<{
    format: SbomFormat;
    message: string;
  } | null>(null);

  const onDownload = useCallback(
    async (format: SbomFormat) => {
      setBusyFormat(format);
      setError(null);
      try {
        const result = await downloadSbom(projectId, format);
        triggerBrowserDownload(result.blob, result.filename);
      } catch (err) {
        const message =
          err instanceof ProblemError
            ? err.detail
            : err instanceof Error
              ? err.message
              : t("sbom.errors.download_failed");
        setError({ format, message });
      } finally {
        setBusyFormat(null);
      }
    },
    [projectId, t],
  );

  const lastScanLabel = lastScanAt
    ? new Date(lastScanAt).toLocaleString(i18n.resolvedLanguage ?? "en")
    : null;

  return (
    <div className="p-6" data-testid="sbom-tab">
      <Card>
        <CardHeader>
          <CardTitle>{t("sbom.title")}</CardTitle>
          <CardDescription>{t("sbom.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {lastScanLabel ? (
            <p
              className="text-xs text-muted-foreground"
              data-testid="sbom-last-scan"
            >
              {t("sbom.last_scan_at", { date: lastScanLabel })}
            </p>
          ) : (
            <p
              className="text-xs text-muted-foreground"
              data-testid="sbom-no-scan"
            >
              {t("sbom.no_scan_yet")}
            </p>
          )}

          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FORMATS.map(({ format, testIdSuffix }) => (
              <li key={format}>
                <Button
                  type="button"
                  variant="outline"
                  className="w-full justify-start"
                  disabled={busyFormat !== null}
                  onClick={() => {
                    void onDownload(format);
                  }}
                  data-testid={`sbom-download-${testIdSuffix}`}
                  data-format={format}
                >
                  {busyFormat === format
                    ? t("sbom.downloading")
                    : t(`sbom.format.${format.replace("-", "_")}`)}
                </Button>
              </li>
            ))}
          </ul>

          {error ? (
            <Alert variant="destructive" data-testid="sbom-error">
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
