/**
 * useTriggerScan — feat/zip-upload.
 *
 * Orchestrates the three source-provision paths into a single mutation the UI
 * can drive from one place:
 *
 *   1. `git`    — no body source; backend uses the project's `git_url`.
 *   2. `upload` — caller already has a `.zip` File; we POST it to
 *                 `source-archive`, then trigger the scan with the returned
 *                 `archive_id`.
 *   3. `folder` — caller passes a FileList from a `webkitdirectory` input; we
 *                 zip it client-side (lib/zipFolder), then take the `upload`
 *                 path.
 *   4. `container` — no body source; the caller supplies a Docker `image_ref`
 *                 (e.g. `alpine:3.19`) which the backend Trivy pipeline reads
 *                 from `metadata.image_ref` (apps/backend/tasks/scan_container
 *                 ::_resolve_image_ref). Triggers `kind: "container"`.
 *
 * Progress is exposed as a Zustand-free local signal via the `onUpdate`
 * callback (zip %, then upload %) so the dialog can render a single staged
 * progress bar without server state leaking into client state.
 *
 * On success the caller receives the persisted {@link ScanPublic} and wires it
 * into the existing `ScanProgress` drawer.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  type ScanPublic,
  triggerScan as triggerScanApi,
} from "@/lib/projectsApi";
import { uploadSourceArchive } from "@/lib/sourceArchiveApi";
import { zipFolderSelection } from "@/lib/zipFolder";

export type SourceMethod = "git" | "upload" | "folder";

/**
 * `container` is a distinct scan kind (Trivy on a Docker image) rather than a
 * source-provision method. It rides through the same mutation so the dialog
 * has a single submit path; the `runTrigger` switch routes it to a
 * `kind: "container"` trigger with the image reference in metadata.
 */
export type TriggerMethod = SourceMethod | "container";

export type ScanStage = "idle" | "zipping" | "uploading" | "triggering";

export interface TriggerScanInput {
  method: TriggerMethod;
  /** Required for `method: "upload"`. */
  file?: File;
  /** Required for `method: "folder"`. */
  folderFiles?: FileList | File[];
  /** Optional name used for the generated folder zip. */
  rootName?: string;
  /** Required for `method: "container"`. Docker image reference, e.g. `alpine:3.19`. */
  imageRef?: string;
  /**
   * Optional release/version label for the scan (feature #18), e.g. `v1.2.3`.
   * Threaded into `metadata.release` on every trigger branch. Trimmed by the
   * caller; the key is omitted when empty. The backend validates the charset
   * (ref-safe, ≤100 chars) and is the source of truth — a malformed value
   * surfaces as a 422 in the dialog's error alert.
   */
  release?: string;
  /**
   * Scan-log verbosity (feat/scan-log-verbosity). When `true`, the scan runs
   * cdxgen / scancode / Trivy in their debug/verbose modes so the scan-log
   * drawer renders a full diagnostic trace. Threaded into `metadata.verbosity`
   * on every trigger branch as `"verbose"`; the key is omitted when `false`
   * (the backend defaults absent → `"normal"`).
   */
  verbose?: boolean;
}

/**
 * Build the shared `metadata.release` fragment. Trims and only emits the key
 * when a non-empty value remains, so an empty/whitespace release never lands
 * on the wire.
 */
function releaseMetadata(release?: string): { release?: string } {
  const trimmed = release?.trim();
  return trimmed ? { release: trimmed } : {};
}

/**
 * Build the shared `metadata.verbosity` fragment. Only emits the key for a
 * verbose run so a normal scan's payload stays identical to the legacy shape
 * (backend treats an absent key as `"normal"`).
 */
function verbosityMetadata(verbose?: boolean): { verbosity?: "verbose" } {
  return verbose ? { verbosity: "verbose" } : {};
}

export interface TriggerScanProgress {
  stage: ScanStage;
  /** 0–100 within the current stage. */
  percent: number;
}

export interface UseTriggerScanOptions {
  onUpdate?: (progress: TriggerScanProgress) => void;
}

async function runTrigger(
  projectId: string,
  input: TriggerScanInput,
  onUpdate?: (progress: TriggerScanProgress) => void,
): Promise<ScanPublic> {
  const release = releaseMetadata(input.release);
  const verbosity = verbosityMetadata(input.verbose);

  if (input.method === "git") {
    onUpdate?.({ stage: "triggering", percent: 0 });
    return triggerScanApi(projectId, {
      kind: "source",
      metadata: { source_type: "git", ...release, ...verbosity },
    });
  }

  if (input.method === "container") {
    const imageRef = input.imageRef?.trim();
    if (!imageRef) {
      throw new Error("image reference is required for a container scan");
    }
    onUpdate?.({ stage: "triggering", percent: 0 });
    // Backend Trivy pipeline reads metadata.image_ref
    // (apps/backend/tasks/scan_container.py::_resolve_image_ref).
    return triggerScanApi(projectId, {
      kind: "container",
      metadata: { image_ref: imageRef, ...release, ...verbosity },
    });
  }

  // upload + folder both end in an `archive_id` → scan-trigger.
  let file: File | Blob;
  let filename: string | undefined;

  if (input.method === "folder") {
    if (!input.folderFiles) {
      throw new Error("folder selection is required");
    }
    onUpdate?.({ stage: "zipping", percent: 0 });
    const zipped = await zipFolderSelection(input.folderFiles, {
      rootName: input.rootName,
      onProgress: (percent) => onUpdate?.({ stage: "zipping", percent }),
    });
    file = zipped.blob;
    filename = zipped.filename;
  } else {
    if (!input.file) {
      throw new Error("file is required for upload");
    }
    file = input.file;
    filename = input.file.name;
  }

  onUpdate?.({ stage: "uploading", percent: 0 });
  // Wrap a generated Blob into a File so the backend extension check sees
  // `.zip`. An explicit File already carries its name.
  const uploadFile =
    file instanceof File
      ? file
      : new File([file], filename ?? "source-archive.zip", {
          type: "application/zip",
        });

  const { archive_id } = await uploadSourceArchive(projectId, uploadFile, {
    onProgress: (percent) => onUpdate?.({ stage: "uploading", percent }),
  });

  onUpdate?.({ stage: "triggering", percent: 100 });
  return triggerScanApi(projectId, {
    kind: "source",
    metadata: { source_type: "upload", archive_id, ...release, ...verbosity },
  });
}

/**
 * Mutation hook for the unified scan-trigger flow. Invalidates the projects
 * cache on success so the list reflects the new `latest_scan_id`.
 */
export function useTriggerScan(
  projectId: string,
  options: UseTriggerScanOptions = {},
) {
  const queryClient = useQueryClient();

  return useMutation<ScanPublic, Error, TriggerScanInput>({
    mutationFn: (input) => runTrigger(projectId, input, options.onUpdate),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
