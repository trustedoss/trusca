// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { AlertTriangle, FileUp } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useIngestSbom } from "@/hooks/useIngestSbom";
import { ProblemError } from "@/lib/problem";
import type { ProjectPublic, ScanPublic } from "@/lib/projectsApi";
import { formatBytes } from "@/lib/zipFolder";

/**
 * SbomIngestDialog — feat/demo-sandbox-scan.
 *
 * Upload an externally produced CycloneDX / SPDX SBOM instead of scanning source
 * (`POST /v1/projects/{id}/sbom-ingest`). Primary use is the sandbox demo lane:
 * a visitor scans a large project locally with BomLens, then uploads the
 * resulting SBOM here for CVE + license matching. It is a first-party portal
 * feature, not demo-only — the dialog itself makes no demo assumptions.
 *
 * The file input is a real `<input type="file">` driven by an i18n'd Button
 * (the native control renders an OS-locale label; mirrors SourceSelectDialog).
 * On success the persisted queued scan is handed back so the parent opens the
 * shared live `ScanProgress` drawer. Errors surface inline (413/415/422 →
 * localized) with `aria-live` so they are announced.
 */

export interface SbomIngestDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: ProjectPublic;
  /** Called with the persisted queued scan once the ingest succeeds. */
  onIngestStarted: (scan: ScanPublic, project: ProjectPublic) => void;
}

/** Map an ingest failure onto an i18n key under `scans:sbom_ingest.errors`. */
export function sbomIngestErrorKey(err: unknown): string {
  if (err instanceof ProblemError) {
    switch (err.status) {
      case 413:
        return "sbom_ingest.errors.too_large";
      case 415:
        return "sbom_ingest.errors.unsupported_type";
      case 422:
        return "sbom_ingest.errors.invalid_document";
      case 409:
        return "sbom_ingest.errors.scan_in_progress";
      case 404:
        return "sbom_ingest.errors.not_found";
      case 429:
        return "sbom_ingest.errors.rate_limited";
      case 0:
        return "sbom_ingest.errors.network";
      default:
        return "sbom_ingest.errors.unknown";
    }
  }
  return "sbom_ingest.errors.unknown";
}

const ACCEPT =
  ".json,.cdx.json,.spdx,.spdx.json,.tag,application/json,application/spdx+json";

export function SbomIngestDialog({
  open,
  onOpenChange,
  project,
  onIngestStarted,
}: SbomIngestDialogProps) {
  const { t } = useTranslation("scans");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [release, setRelease] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mutation = useIngestSbom(project.id);

  function reset() {
    setSelectedFile(null);
    setRelease("");
    mutation.reset();
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handlePick(event: React.ChangeEvent<HTMLInputElement>) {
    mutation.reset();
    setSelectedFile(event.target.files?.[0] ?? null);
  }

  async function handleSubmit() {
    if (!selectedFile) return;
    const trimmed = release.trim();
    try {
      const scan = await mutation.mutateAsync({
        file: selectedFile,
        release: trimmed.length > 0 ? trimmed : undefined,
      });
      onIngestStarted(scan, project);
      onOpenChange(false);
      reset();
    } catch {
      // Surfaced from mutation.error below.
    }
  }

  const isBusy = mutation.isPending;
  const errorMessage = mutation.error
    ? t(sbomIngestErrorKey(mutation.error))
    : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent
        className="max-w-xl"
        data-testid="sbom-ingest-dialog"
        onInteractOutside={(e) => {
          if (isBusy) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>{t("sbom_ingest.title")}</DialogTitle>
          <DialogDescription>
            {t("sbom_ingest.subtitle", { project: project.name })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2" data-testid="sbom-ingest-pick">
          <label
            htmlFor="sbom-ingest-input"
            className="block text-xs font-medium text-muted-foreground"
          >
            {t("sbom_ingest.file_label")}
          </label>
          <input
            ref={fileInputRef}
            id="sbom-ingest-input"
            type="file"
            accept={ACCEPT}
            onChange={handlePick}
            disabled={isBusy}
            data-testid="sbom-ingest-input"
            className="sr-only"
          />
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={isBusy}
              data-testid="sbom-ingest-choose"
            >
              <FileUp className="mr-1.5 h-4 w-4" aria-hidden />
              {t("sbom_ingest.choose_file")}
            </Button>
            <span
              className="truncate text-xs text-muted-foreground"
              data-testid="sbom-ingest-filename"
            >
              {selectedFile
                ? selectedFile.name
                : t("sbom_ingest.no_file_chosen")}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            {t("sbom_ingest.hint")}
          </p>
          {selectedFile ? (
            <p
              className="text-xs text-foreground"
              data-testid="sbom-ingest-selected"
            >
              {t("sbom_ingest.selected", {
                name: selectedFile.name,
                size: formatBytes(selectedFile.size),
              })}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5" data-testid="sbom-ingest-release-field">
          <label
            htmlFor="sbom-ingest-release"
            className="block text-xs font-medium text-muted-foreground"
          >
            {t("release.label")}
          </label>
          <Input
            id="sbom-ingest-release"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={release}
            onChange={(event) => setRelease(event.target.value)}
            disabled={isBusy}
            maxLength={100}
            placeholder={t("release.placeholder")}
            data-testid="sbom-ingest-release"
            className="font-mono"
          />
        </div>

        {/* M-3 — shared public sandbox warning, shown directly above the
            submit button so it is unmissable right before an upload. */}
        <p
          className="flex items-start gap-1.5 rounded-md border border-risk-high/40 bg-risk-high/10 px-3 py-2 text-xs font-medium text-risk-high-foreground"
          data-testid="sbom-ingest-shared-warning"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("sbom_ingest.shared_warning")}</span>
        </p>

        {errorMessage ? (
          <Alert variant="destructive" data-testid="sbom-ingest-error">
            <AlertDescription aria-live="polite">
              {errorMessage}
            </AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              reset();
              onOpenChange(false);
            }}
            disabled={isBusy}
            data-testid="sbom-ingest-cancel"
          >
            {t("sbom_ingest.cancel")}
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={!selectedFile || isBusy}
            data-testid="sbom-ingest-submit"
          >
            {isBusy ? t("sbom_ingest.uploading") : t("sbom_ingest.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
