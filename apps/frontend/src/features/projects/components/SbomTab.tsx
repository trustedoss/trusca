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
import { SbomSignatureSection } from "@/features/projects/components/SbomSignatureSection";
import { triggerBlobDownload } from "@/lib/download";
import { ProblemError } from "@/lib/problem";
import {
  downloadSbom,
  type SbomFormat,
  type SbomProfile,
} from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

interface SbomTabProps {
  projectId: string;
  /**
   * Timestamp of the latest *succeeded* scan (ISO-8601) — the scan the SBOM is
   * actually exported from. Optional; rendered when present, otherwise the
   * "no scan yet" empty state shows. Callers pass `last_succeeded_scan_at`
   * (NOT `last_scan_at`, which may be a failed attempt) so the label matches
   * the downloaded artifact.
   */
  lastScanAt?: string | null;
  /**
   * Pinned snapshot scan id (feature #28). When set, downloads export that
   * historical scan's SBOM instead of the latest succeeded one. Omit → latest.
   */
  scanId?: string;
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

// C3 — the three profile choices. `"default"` is the sentinel for "no
// profile" (the canonical, signable export); the other two map to the
// backend `profile` query values.
type ProfileChoice = "default" | SbomProfile;
const PROFILE_CHOICES: ProfileChoice[] = [
  "default",
  "policy-annotated",
  "policy-filtered",
];

export function SbomTab({ projectId, lastScanAt, scanId }: SbomTabProps) {
  const { t, i18n } = useTranslation("project_detail");
  const [busyFormat, setBusyFormat] = useState<SbomFormat | null>(null);
  const [profile, setProfile] = useState<ProfileChoice>("default");
  const [error, setError] = useState<{
    format: SbomFormat;
    message: string;
  } | null>(null);

  const onDownload = useCallback(
    async (format: SbomFormat) => {
      setBusyFormat(format);
      setError(null);
      try {
        const result = await downloadSbom(projectId, format, {
          scanId,
          // "default" carries no profile so the export stays the canonical,
          // signable document; the other two pass the backend profile value.
          profile: profile === "default" ? undefined : profile,
        });
        triggerBlobDownload(result.blob, result.filename);
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
    [projectId, scanId, profile, t],
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

          <div className="flex flex-col gap-2" data-testid="sbom-profile-select">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t("sbom.profile.label")}
            </span>
            <div
              className="inline-flex flex-wrap gap-1 rounded-md border border-border bg-muted/30 p-1"
              role="radiogroup"
              aria-label={t("sbom.profile.label")}
            >
              {PROFILE_CHOICES.map((choice) => (
                <button
                  key={choice}
                  type="button"
                  role="radio"
                  aria-checked={profile === choice}
                  disabled={busyFormat !== null}
                  onClick={() => setProfile(choice)}
                  data-testid={`sbom-profile-${choice}`}
                  className={cn(
                    "rounded px-3 py-1 text-sm transition-colors duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                    profile === choice
                      ? "bg-background font-medium text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t(`sbom.profile.choice.${choice.replace("-", "_")}`)}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {profile === "default"
                ? t("sbom.profile.hint.default")
                : t("sbom.profile.hint.profiled")}
            </p>
          </div>

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

          <SbomSignatureSection projectId={projectId} />
        </CardContent>
      </Card>
    </div>
  );
}
