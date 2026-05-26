import { Box, FileArchive, FolderOpen, GitBranch } from "lucide-react";
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
import { Input } from "@/components/ui/input";
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

/**
 * Top-level scan kind. `source` runs cdxgen + ORT + DT on a source tree;
 * `container` runs Trivy on a Docker image reference. Mirrors the backend
 * `ScanKind` enum (apps/backend/schemas/scan.py).
 */
type ScanKind = "source" | "container";

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

interface KindMeta {
  value: ScanKind;
  icon: typeof GitBranch;
  testid: string;
}

const KINDS: KindMeta[] = [
  { value: "source", icon: GitBranch, testid: "scan-kind-source" },
  { value: "container", icon: Box, testid: "scan-kind-container" },
];

export function SourceSelectDialog({
  open,
  onOpenChange,
  project,
  onScanStarted,
}: SourceSelectDialogProps) {
  const { t } = useTranslation("scans");
  const hasGitUrl = Boolean(project.git_url);
  const [kind, setKind] = useState<ScanKind>("source");
  const [method, setMethod] = useState<SourceMethod>(
    hasGitUrl ? "git" : "upload",
  );
  const [imageRef, setImageRef] = useState("");
  const [imageRefTouched, setImageRefTouched] = useState(false);
  const [release, setRelease] = useState("");
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

  const trimmedImageRef = imageRef.trim();

  const canSubmit = useMemo(() => {
    if (mutation.isPending) return false;
    if (kind === "container") return trimmedImageRef.length > 0;
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
  }, [
    kind,
    trimmedImageRef,
    method,
    hasGitUrl,
    selectedFile,
    folderInspection,
    mutation.isPending,
  ]);

  const trimmedRelease = release.trim();

  async function handleSubmit() {
    setPreflightError(null);
    // Threaded into every trigger branch's metadata.release. Omitted (undefined)
    // when empty so the hook never puts an empty key on the wire; the backend
    // validates the charset and is the source of truth (a 422 surfaces below).
    const releaseArg = trimmedRelease.length > 0 ? trimmedRelease : undefined;
    try {
      let scan: ScanPublic;
      if (kind === "container") {
        if (trimmedImageRef.length === 0) {
          setImageRefTouched(true);
          setPreflightError(
            t("container.errors.image_required", {
              defaultValue: "Enter an image reference to scan.",
            }),
          );
          return;
        }
        scan = await mutation.mutateAsync({
          method: "container",
          imageRef: trimmedImageRef,
          release: releaseArg,
        });
      } else if (method === "git") {
        scan = await mutation.mutateAsync({ method: "git", release: releaseArg });
      } else if (method === "upload" && selectedFile) {
        scan = await mutation.mutateAsync({
          method: "upload",
          file: selectedFile,
          release: releaseArg,
        });
      } else if (method === "folder" && folderInspection) {
        scan = await mutation.mutateAsync({
          method: "folder",
          folderFiles: folderInspection.files,
          rootName: folderRoot ?? project.slug,
          release: releaseArg,
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
        if (!next) {
          resetTransient();
          setImageRef("");
          setImageRefTouched(false);
          setRelease("");
        }
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
            {kind === "container"
              ? t("container.subtitle", {
                  defaultValue:
                    "Scan a container image for {{project}} with Trivy.",
                  project: project.name,
                })
              : t("source.subtitle", { project: project.name })}
          </DialogDescription>
        </DialogHeader>

        <div
          role="radiogroup"
          aria-label={t("kind.legend", { defaultValue: "Scan type" })}
          className="grid grid-cols-2 gap-2"
          data-testid="scan-kind-group"
        >
          {KINDS.map(({ value, icon: Icon, testid }) => {
            const active = kind === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={isBusy}
                onClick={() => {
                  resetTransient();
                  setImageRefTouched(false);
                  setKind(value);
                }}
                data-testid={testid}
                data-active={active ? "true" : "false"}
                className={cn(
                  "flex items-center justify-center gap-2 rounded-md border p-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                  active
                    ? "border-primary bg-primary/5 text-foreground"
                    : "border-input hover:bg-accent",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
                <span>
                  {value === "source"
                    ? t("kind.source", { defaultValue: "Source" })
                    : t("kind.container", { defaultValue: "Container" })}
                </span>
              </button>
            );
          })}
        </div>

        {kind === "source" ? (
          <>
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
          </>
        ) : (
          <ContainerPanel
            imageRef={imageRef}
            onChange={(value) => {
              setPreflightError(null);
              setImageRef(value);
            }}
            onBlur={() => setImageRefTouched(true)}
            invalid={imageRefTouched && trimmedImageRef.length === 0}
            disabled={isBusy}
          />
        )}

        <ReleaseField
          value={release}
          onChange={setRelease}
          disabled={isBusy}
        />

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
      {/* P2 #9 — native `<input type="file">` renders its own button label in
          the OS locale ("파일 선택" on a Korean macOS regardless of app i18n).
          Hide it visually + drive a Button label we control via react-i18next.
          The hidden input keeps `id` so the `<label htmlFor>` association
          still works for screen readers / form labelling. */}
      <input
        ref={fileInputRef}
        id="source-zip-input"
        type="file"
        accept=".zip,application/zip"
        onChange={onPick}
        disabled={disabled}
        data-testid="source-zip-input"
        className="sr-only"
      />
      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled}
          data-testid="source-zip-pick-button"
        >
          {t("source.upload.choose_file", { defaultValue: "Choose file" })}
        </Button>
        <span
          className="truncate text-xs text-muted-foreground"
          data-testid="source-zip-filename"
        >
          {selectedFile
            ? selectedFile.name
            : t("source.upload.no_file_chosen", {
                defaultValue: "No file chosen",
              })}
        </span>
      </div>
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
      {/* P2 #9 — see UploadPanel for rationale: hide the native input so its
          OS-locale "Choose Files / 파일 선택" label is not shown, and drive
          the visible action through an i18n'd Button instead. */}
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
        className="sr-only"
      />
      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => folderInputRef.current?.click()}
          disabled={disabled}
          data-testid="source-folder-pick-button"
        >
          {t("source.folder.choose_folder", { defaultValue: "Choose folder" })}
        </Button>
        <span
          className="truncate text-xs text-muted-foreground"
          data-testid="source-folder-status"
        >
          {inspection && !inspection.isEmpty
            ? t("source.folder.files_count", {
                defaultValue: "{{count}} file(s) selected",
                count: inspection.files.length,
              })
            : t("source.folder.no_folder_chosen", {
                defaultValue: "No folder chosen",
              })}
        </span>
      </div>
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

interface ReleaseFieldProps {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}

/**
 * Optional release/version label (feature #18). Applies to every scan kind, so
 * it lives below the kind/method selectors. The backend validates the charset
 * (ref-safe, ≤100 chars) and is the source of truth — we only trim + omit when
 * empty here; a malformed value surfaces as a 422 in the dialog error alert.
 */
function ReleaseField({ value, onChange, disabled }: ReleaseFieldProps) {
  const { t } = useTranslation("scans");
  return (
    <div className="space-y-1.5" data-testid="scan-release-field">
      <label
        htmlFor="scan-release-input"
        className="block text-xs font-medium text-muted-foreground"
      >
        {t("release.label")}
      </label>
      <Input
        id="scan-release-input"
        type="text"
        inputMode="text"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        maxLength={100}
        placeholder={t("release.placeholder")}
        aria-describedby="scan-release-hint"
        data-testid="scan-release-input"
        className="font-mono"
      />
      <p
        id="scan-release-hint"
        className="text-xs text-muted-foreground"
        data-testid="scan-release-hint"
      >
        {t("release.hint")}
      </p>
    </div>
  );
}

interface ContainerPanelProps {
  imageRef: string;
  onChange: (value: string) => void;
  onBlur: () => void;
  invalid: boolean;
  disabled: boolean;
}

function ContainerPanel({
  imageRef,
  onChange,
  onBlur,
  invalid,
  disabled,
}: ContainerPanelProps) {
  const { t } = useTranslation("scans");
  return (
    <div className="min-h-[7rem] space-y-2" data-testid="source-container-panel">
      <label
        htmlFor="scan-image-ref-input"
        className="block text-xs font-medium text-muted-foreground"
      >
        {t("container.label", { defaultValue: "Container image" })}
      </label>
      <Input
        id="scan-image-ref-input"
        type="text"
        inputMode="text"
        autoComplete="off"
        spellCheck={false}
        value={imageRef}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        disabled={disabled}
        placeholder={t("container.placeholder", {
          defaultValue: "alpine:3.19",
        })}
        aria-invalid={invalid}
        aria-describedby="scan-image-ref-hint"
        data-testid="scan-image-ref-input"
        className="font-mono"
      />
      <p
        id="scan-image-ref-hint"
        className="text-xs text-muted-foreground"
        data-testid="container-hint"
      >
        {t("container.hint", {
          defaultValue:
            "Trivy scans the image's OS packages for vulnerabilities, e.g. ghcr.io/org/app:1.2.3.",
        })}
      </p>
      {invalid ? (
        <p
          className="text-xs text-risk-critical"
          data-testid="container-error"
          aria-live="polite"
        >
          {t("container.errors.image_required", {
            defaultValue: "Enter an image reference to scan.",
          })}
        </p>
      ) : null}
    </div>
  );
}
