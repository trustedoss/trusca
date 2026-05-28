/**
 * SbomSignatureSection — v2.3-s3.
 *
 * "Signature & Verification" panel on the project detail SBOM tab. Lets a
 * developer download everything needed to verify the project's signed SBOM
 * offline with cosign:
 *
 *   - PRIMARY: "Download signature bundle (.zip)" — a self-contained archive
 *     (SBOM + .sig + cert|public-key + attestation + keyless attest cert +
 *     VERIFY.md). This is the recommended one-button path; the bundle's
 *     VERIFY.md walks the consumer through `cosign verify-blob`.
 *   - SECONDARY: individual artifacts (signature, public key, attestation,
 *     certificates) for power users assembling their own verification flow.
 *
 * Downloads stream through the authenticated axios instance (bearer token in
 * the Authorization header, never the URL) and trigger a transient
 * `<a download>` click — same pattern as the SBOM export buttons above it.
 *
 * Graceful 404 handling (per the brief):
 *   - An UNSIGNED scan returns 404 from the bundle endpoint. We detect that on
 *     the primary attempt and flip the panel to a calm "no signature" state
 *     rather than spamming a destructive error.
 *   - The certificate artifacts return 404 on KEY-BASED deployments (no Fulcio
 *     cert exists there). The hook reports that as `lastNotApplicable`; we show
 *     a quiet "keyless only" hint, not an error.
 *
 * Inline, compact, no modal — per the design system.
 */
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useSbomSignature } from "@/features/projects/api/useSbomSignature";
import type { SbomSignatureArtifact } from "@/lib/projectsApi";
import { ProblemError } from "@/lib/problem";

export interface SbomSignatureSectionProps {
  projectId: string;
}

interface SecondaryArtifactRow {
  artifact: SbomSignatureArtifact;
  /** i18n key under `sbom.signature.artifact` for the button label. */
  labelKey: string;
  testIdSuffix: string;
}

// Power-user artifacts. Order: signature + public key (needed for key-based
// verification) first, then attestation + certificates (keyless extras).
const SECONDARY_ARTIFACTS: SecondaryArtifactRow[] = [
  {
    artifact: "signature",
    labelKey: "signature",
    testIdSuffix: "signature",
  },
  {
    artifact: "public-key",
    labelKey: "public_key",
    testIdSuffix: "public-key",
  },
  {
    artifact: "attestation",
    labelKey: "attestation",
    testIdSuffix: "attestation",
  },
  {
    artifact: "certificate",
    labelKey: "certificate",
    testIdSuffix: "certificate",
  },
  {
    artifact: "attestation-certificate",
    labelKey: "attestation_certificate",
    testIdSuffix: "attestation-certificate",
  },
];

/** Live cosign verification guide (v2.3-s3-doc). */
const VERIFY_DOCS_PATH = "/docs/reference/sbom-signature-verification";

export function SbomSignatureSection({ projectId }: SbomSignatureSectionProps) {
  const { t } = useTranslation("project_detail");
  const signature = useSbomSignature(projectId);
  // Flips to true when the bundle endpoint 404s for an unsigned scan — the
  // whole feature is then "not available" for this project's latest scan.
  const [unsigned, setUnsigned] = useState(false);

  const onDownloadBundle = useCallback(() => {
    setUnsigned(false);
    signature
      .download("bundle")
      .catch((err: unknown) => {
        // An unsigned scan 404s the bundle: surface the calm "no signature"
        // state instead of the destructive error alert. Clear the error the
        // hook stored so only the unsigned panel shows.
        if (err instanceof ProblemError && err.status === 404) {
          signature.clear();
          setUnsigned(true);
        }
        // Any other failure is already stored in `signature.error`.
      });
  }, [signature]);

  const onDownloadArtifact = useCallback(
    (artifact: SbomSignatureArtifact) => {
      signature.download(artifact).catch(() => {
        // Surfaced inline via signature.error / signature.lastNotApplicable.
      });
    },
    [signature],
  );

  const notApplicableLabel = signature.lastNotApplicable
    ? t(
        `sbom.signature.artifact.${
          signature.lastNotApplicable === "certificate"
            ? "certificate"
            : "attestation_certificate"
        }`,
      )
    : null;

  return (
    <section
      className="space-y-3 border-t pt-4"
      data-testid="sbom-signature-section"
      aria-labelledby="sbom-signature-heading"
    >
      <div className="space-y-1">
        <h3
          id="sbom-signature-heading"
          className="text-sm font-medium"
        >
          {t("sbom.signature.title")}
        </h3>
        <p className="text-xs text-muted-foreground">
          {t("sbom.signature.subtitle")}
        </p>
      </div>

      {unsigned ? (
        <Alert data-testid="sbom-signature-unsigned">
          <AlertDescription>{t("sbom.signature.unsigned")}</AlertDescription>
        </Alert>
      ) : null}

      <Button
        type="button"
        className="w-full justify-start sm:w-auto"
        disabled={signature.busyArtifact !== null}
        onClick={onDownloadBundle}
        data-testid="sbom-signature-download-bundle"
      >
        {signature.busyArtifact === "bundle"
          ? t("sbom.signature.downloading")
          : t("sbom.signature.download_bundle")}
      </Button>

      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          {t("sbom.signature.individual_label")}
        </p>
        <ul className="flex flex-wrap gap-2" data-testid="sbom-signature-individual">
          {SECONDARY_ARTIFACTS.map(({ artifact, labelKey, testIdSuffix }) => (
            <li key={artifact}>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-9"
                disabled={signature.busyArtifact !== null}
                onClick={() => {
                  onDownloadArtifact(artifact);
                }}
                data-testid={`sbom-signature-download-${testIdSuffix}`}
                data-artifact={artifact}
              >
                {signature.busyArtifact === artifact
                  ? t("sbom.signature.downloading")
                  : t(`sbom.signature.artifact.${labelKey}`)}
              </Button>
            </li>
          ))}
        </ul>
      </div>

      {notApplicableLabel ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="sbom-signature-not-applicable"
          aria-live="polite"
        >
          {t("sbom.signature.not_applicable", { artifact: notApplicableLabel })}
        </p>
      ) : null}

      {signature.error ? (
        <Alert variant="destructive" data-testid="sbom-signature-error">
          <AlertDescription>
            {signature.error instanceof ProblemError
              ? signature.error.detail
              : signature.error.message}
          </AlertDescription>
        </Alert>
      ) : null}

      <p className="text-xs text-muted-foreground">
        {t("sbom.signature.verify_hint")}{" "}
        <a
          href={VERIFY_DOCS_PATH}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium underline underline-offset-2 transition-colors duration-fast ease-out-soft hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="sbom-signature-verify-docs"
        >
          {t("sbom.signature.verify_docs_link")}
        </a>
      </p>
    </section>
  );
}
