import { FileArchive, FolderOpen, GitBranch } from "lucide-react";
import { useMemo, useRef, useState } from "react";
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
import { Progress } from "@/components/ui/progress";
import {
  type ScanStage,
  type SourceMethod,
  type TriggerScanProgress,
  useTriggerScan,
} from "@/hooks/useTriggerScan";
import type { ProjectPublic, ScanPublic } from "@/lib/projectsApi";
import { uploadErrorMessageKey } from "@/lib/sourceArchiveApi";
import { cn } from "@/lib/utils";
import {
  FolderZipError,
  formatBytes,
  type FolderInspection,
  inspectFolderSelection,
  rootFolderName,
} from "@/lib/zipFolder";

/**
 * SourceSelectDialog — feat/zip-upload.
 *
 * Modal that lets the user choose how to provide the project's source before
 * a scan runs: the project's configured Git URL, an uploaded `.zip`, or a
 * folder that we zip in the browser. On success it hands the persisted scan
 * back to the parent which opens the existing right-side `ScanProgress`
 * drawer.
 *
 * Method selection is an ARIA radiogroup of three cards; the file / folder
 * inputs are real `<input type="file">` elements with associated `<label>`s so
 * the picker is keyboard reachable. Progress is a single staged bar (zip →
 * upload → trigger) with an `aria-live` stage label.
 */

export interface SourceSelectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: ProjectPublic;
  /** Called with the persisted scan once the trigger succeeds. */
  onScanStarted: (scan: ScanPublic, project: ProjectPublic) => void;
}

interface MethodMeta {
  value: SourceMethod;
  icon: typeof GitBranch;
  testid: string;
}

const METHODS: MethodMeta[] = [
  { value: "git", icon: GitBranch, testid: "source-method-git" },
  { value: "upload", icon: FileArchive, testid: "source-method-upload" },
  { value: "folder", icon: FolderOpen, testid: "source-method-folder" },
];

export function SourceSelectDialog({
  open,
  onOpenChange,
  project,
  onScanStarted,
}: SourceSelectDialogProps) {
  const { t } = useTranslation("scans");
  const hasGitUrl = Boolean(project.git_url);
  const [method, setMethod] = useState<SourceMethod>(
    hasGitUrl ? "git" : "upload",
  );
  const [progress, setProgress] = useState<TriggerScanProgress>({
    stage: "idle",
    percent: 0,
  });
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [folderInspection, setFolderInspection] =
    useState<FolderInspection | null>(null);
  const [folderRoot, setFolderRoot] = useState<string | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const mutation = useTriggerScan(project.id, {
    onUpdate: (next) => setProgress(next),
  });

  function resetTransient() {
    setProgress({ stage: "idle", percent: 0 });
    setPreflightError(null);
    mutation.reset();
  }

  function handleFilePick(event: React.ChangeEvent<HTMLInputElement>) {
    resetTransient();
    const file = event.target.files?.[0] ?? null;
    if (file && !/\.zip$/i.test(file.name)) {
      setSelectedFile(null);
      setPreflightError(t("upload.errors.not_a_zip"));
      return;
    }
    setSelectedFile(file);
  }

  function handleFolderPick(event: React.ChangeEvent<HTMLInputElement>) {
    resetTransient();
    const list = event.target.files;
    if (!list || list.length === 0) {
      setFolderInspection(null);
      setFolderRoot(null);
      setPreflightError(t("upload.errors.empty_folder"));
      return;
    }
    setFolderInspection(inspectFolderSelection(list));
    setFolderRoot(rootFolderName(list));
  }

  const canSubmit = useMemo(() => {
    if (mutation.isPending) return false;
    if (method === "git") return hasGitUrl;
    if (method === "upload") return Boolean(selectedFile);
    if (method === "folder") {
      return Boolean(
        folderInspection &&
          !folderInspection.isEmpty &&
          !folderInspection.exceedsMax,
      );
    }
    return false;
  }, [method, hasGitUrl, selectedFile, folderInspection, mutation.isPending]);

  async function handleSubmit() {
    setPreflightError(null);
    try {
      let scan: ScanPublic;
      if (method === "git") {
        scan = await mutation.mutateAsync({ method: "git" });
      } else if (method === "upload" && selectedFile) {
        scan = await mutation.mutateAsync({ method: "upload", file: selectedFile });
      } else if (method === "folder" && folderInspection) {
        scan = await mutation.mutateAsync({
          method: "folder",
          folderFiles: folderInspection.files,
          rootName: folderRoot ?? project.slug,
        });
      } else {
        return;
      }
      onScanStarted(scan, project);
      onOpenChange(false);
    } catch (err) {
      // Client-side zip guards throw FolderZipError before the network; map
      // them onto the same i18n keys the server-side problems use.
      if (err instanceof FolderZipError) {
        setPreflightError(
          t(
            err.token === "too_large"
              ? "upload.errors.too_large"
              : "upload.errors.empty_folder",
          ),
        );
      }
      // Server errors are surfaced from `mutation.error` below.
    }
  }

  const serverError = mutation.error
    ? t(uploadErrorMessageKey(mutation.error))
    : null;
  const errorMessage = preflightError ?? serverError;

  const isBusy = mutation.isPending;
  const stageLabel = stageLabelKey(progress.stage);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) resetTransient();
        onOpenChange(next);
      }}
    >
      <DialogContent
        className="max-w-xl"
        data-testid="source-select-dialog"
        onInteractOutside={(e) => {
          if (isBusy) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>{t("source.title")}</DialogTitle>
          <DialogDescription>
            {t("source.subtitle", { project: project.name })}
          </DialogDescription>
        </DialogHeader>

        <div
          role="radiogroup"
          aria-label={t("source.method_legend")}
          className="grid grid-cols-3 gap-2"
          data-testid="source-method-group"
        >
          {METHODS.map(({ value, icon: Icon, testid }) => {
            const disabled = value === "git" && !hasGitUrl;
            const active = method === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={disabled || isBusy}
                onClick={() => {
                  resetTransient();
                  setMethod(value);
                }}
                data-testid={testid}
                data-active={active ? "true" : "false"}
                className={cn(
                  "flex flex-col items-center gap-1.5 rounded-md border p-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                  active
                    ? "border-primary bg-primary/5 text-foreground"
                    : "border-input hover:bg-accent",
                )}
              >
                <Icon className="h-5 w-5" aria-hidden />
                <span>{t(`source.method.${value}`)}</span>
              </button>
            );
          })}
        </div>

        <div className="min-h-[7rem]" data-testid="source-method-panel">
          {method === "git" ? (
            <GitPanel gitUrl={project.git_url} />
          ) : null}

          {method === "upload" ? (
            <UploadPanel
              fileInputRef={fileInputRef}
              selectedFile={selectedFile}
              onPick={handleFilePick}
              disabled={isBusy}
            />
          ) : null}

          {method === "folder" ? (
            <FolderPanel
              folderInputRef={folderInputRef}
              inspection={folderInspection}
              onPick={handleFolderPick}
              disabled={isBusy}
            />
          ) : null}
        </div>

        {isBusy ? (
          <div className="space-y-1.5" data-testid="source-progress">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span aria-live="polite" data-testid="source-progress-stage">
                {t(stageLabel)}
              </span>
              <span className="font-mono">
                {t("source.percent", { value: progress.percent })}
              </span>
            </div>
            <Progress
              value={progress.percent}
              aria-label={t(stageLabel)}
              data-testid="source-progress-bar"
            />
          </div>
        ) : null}

        {errorMessage ? (
          <Alert variant="destructive" data-testid="source-error">
            <AlertDescription aria-live="polite">{errorMessage}</AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              resetTransient();
              onOpenChange(false);
            }}
            disabled={isBusy}
            data-testid="source-cancel"
          >
            {t("source.cancel")}
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            data-testid="source-submit"
          >
            {isBusy ? t("source.starting") : t("source.start")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function stageLabelKey(stage: ScanStage): string {
  switch (stage) {
    case "zipping":
      return "source.stage.zipping";
    case "uploading":
      return "source.stage.uploading";
    case "triggering":
      return "source.stage.triggering";
    default:
      return "source.stage.idle";
  }
}

function GitPanel({ gitUrl }: { gitUrl: string | null }) {
  const { t } = useTranslation("scans");
  if (!gitUrl) {
    return (
      <Alert data-testid="source-git-missing">
        <AlertDescription>{t("source.git.missing")}</AlertDescription>
      </Alert>
    );
  }
  return (
    <div
      className="rounded-md border bg-muted/40 p-3 text-sm"
      data-testid="source-git-panel"
    >
      <p className="mb-1 text-xs text-muted-foreground">
        {t("source.git.label")}
      </p>
      <p className="break-all font-mono text-xs">{gitUrl}</p>
    </div>
  );
}

interface UploadPanelProps {
  fileInputRef: React.RefObject<HTMLInputElement>;
  selectedFile: File | null;
  onPick: (event: React.ChangeEvent<HTMLInputElement>) => void;
  disabled: boolean;
}

function UploadPanel({
  fileInputRef,
  selectedFile,
  onPick,
  disabled,
}: UploadPanelProps) {
  const { t } = useTranslation("scans");
  return (
    <div className="space-y-2" data-testid="source-upload-panel">
      <label
        htmlFor="source-zip-input"
        className="block text-xs font-medium text-muted-foreground"
      >
        {t("source.upload.label")}
      </label>
      <input
        ref={fileInputRef}
        id="source-zip-input"
        type="file"
        accept=".zip,application/zip"
        onChange={onPick}
        disabled={disabled}
        data-testid="source-zip-input"
        className="block w-full cursor-pointer rounded-md border border-input bg-background text-sm file:mr-3 file:border-0 file:bg-muted file:px-3 file:py-2 file:text-sm file:font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
      />
      <p className="text-xs text-muted-foreground">
        {t("source.upload.hint", { max: "100 MiB" })}
      </p>
      {selectedFile ? (
        <p
          className="text-xs text-foreground"
          data-testid="source-upload-selected"
        >
          {t("source.upload.selected", {
            name: selectedFile.name,
            size: formatBytes(selectedFile.size),
          })}
        </p>
      ) : null}
    </div>
  );
}

interface FolderPanelProps {
  folderInputRef: React.RefObject<HTMLInputElement>;
  inspection: FolderInspection | null;
  onPick: (event: React.ChangeEvent<HTMLInputElement>) => void;
  disabled: boolean;
}

function FolderPanel({
  folderInputRef,
  inspection,
  onPick,
  disabled,
}: FolderPanelProps) {
  const { t } = useTranslation("scans");
  return (
    <div className="space-y-2" data-testid="source-folder-panel">
      <label
        htmlFor="source-folder-input"
        className="block text-xs font-medium text-muted-foreground"
      >
        {t("source.folder.label")}
      </label>
      <input
        ref={folderInputRef}
        id="source-folder-input"
        type="file"
        // webkitdirectory is non-standard; React forwards it as a DOM attr.
        // @ts-expect-error — webkitdirectory not in the typed input props.
        webkitdirectory=""
        directory=""
        multiple
        onChange={onPick}
        disabled={disabled}
        data-testid="source-folder-input"
        className="block w-full cursor-pointer rounded-md border border-input bg-background text-sm file:mr-3 file:border-0 file:bg-muted file:px-3 file:py-2 file:text-sm file:font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
      />
      <p className="text-xs text-muted-foreground">
        {t("source.folder.hint", { max: "100 MiB" })}
      </p>

      {inspection && !inspection.isEmpty ? (
        <div
          className="space-y-1 text-xs"
          data-testid="source-folder-summary"
        >
          <p className="text-foreground">
            {t("source.folder.selected", {
              count: inspection.files.length,
              size: formatBytes(inspection.totalBytes),
            })}
          </p>
          {inspection.exceedsMax ? (
            <p
              className="text-risk-critical"
              data-testid="source-folder-too-large"
              aria-live="polite"
            >
              {t("source.folder.too_large", { max: "100 MiB" })}
            </p>
          ) : null}
          {inspection.noisyDirectories.length > 0 ? (
            <p
              className="text-risk-medium"
              data-testid="source-folder-noisy"
              aria-live="polite"
            >
              {t("source.folder.noisy", {
                dirs: inspection.noisyDirectories.join(", "),
              })}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
