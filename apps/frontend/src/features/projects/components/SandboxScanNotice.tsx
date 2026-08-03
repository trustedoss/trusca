// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { AlertTriangle, FileUp, FlaskConical } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { bomLensUrl, DEMO_SANDBOX_SCAN_MAX_MB } from "@/lib/demoSandbox";

/**
 * SandboxScanNotice — feat/demo-sandbox-scan.
 *
 * The explainer shown on the "Demo Sandbox" project detail page when the
 * backend has the sandbox carve-out on (`/health.demo_sandbox_scans`). It sits
 * directly under the header, next to the (re-enabled) Scan entry point, and
 * spells out the deal: only source ≤ N MB is live-scanned here; anything larger
 * should be scanned locally with BomLens and its CycloneDX SBOM uploaded via the
 * ingest button. Both the scan results and the conformance score then flow into
 * the existing tabs.
 *
 * Rendering is gated by the caller (sandbox project + flag), so a normal deploy
 * never mounts it. The BomLens reference is an external link; the upload action
 * opens the shared {@link SbomIngestDialog}.
 */

export interface SandboxScanNoticeProps {
  /** Open the SBOM ingest dialog for this project. */
  onUploadSbom: () => void;
}

export function SandboxScanNotice({ onUploadSbom }: SandboxScanNoticeProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      role="note"
      data-testid="sandbox-scan-notice"
      className="flex flex-col gap-2 border-b border-risk-low/40 bg-risk-low/10 px-6 py-3 text-sm text-foreground sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex items-start gap-2.5">
        <FlaskConical
          className="mt-0.5 h-4 w-4 shrink-0 text-risk-low"
          aria-hidden
        />
        {/* G0-7 — the secondary lines below are `text-foreground`, not the
            muted grey they read as. The notice sits on the page ground
            (#fafafa) where the muted token clears AA by 0.03, and the tint
            this banner now actually paints takes it under. Size and weight
            still carry the hierarchy. */}
        <div className="space-y-1">
          <p className="font-medium">{t("sandbox.title")}</p>
          <p className="text-xs">
            {t("sandbox.live_limit", { max: DEMO_SANDBOX_SCAN_MAX_MB })}
          </p>
          <p className="text-xs">
            {/* ICU-safe: the BomLens link is a placeholder slot, not a
                concatenated string, so translators keep the sentence intact. */}
            <Trans
              t={t}
              i18nKey="sandbox.bomlens_hint"
              components={{
                bomlens: (
                  <a
                    href={bomLensUrl()}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-risk-low-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    data-testid="sandbox-bomlens-link"
                  />
                ),
              }}
            />
          </p>
          {/* M-3 — this is a SHARED, public sandbox: everyone uses the same
              project + demo account, and uploads are visible to other visitors
              until the periodic reset. Warn before anyone uploads. */}
          <p
            className="flex items-start gap-1.5 text-xs font-medium text-risk-high-foreground"
            data-testid="sandbox-shared-warning"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>{t("sandbox.shared_warning")}</span>
          </p>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onUploadSbom}
        className="shrink-0"
        data-testid="sandbox-upload-sbom"
      >
        <FileUp className="mr-1.5 h-4 w-4" aria-hidden />
        {t("sandbox.upload_sbom")}
      </Button>
    </div>
  );
}
