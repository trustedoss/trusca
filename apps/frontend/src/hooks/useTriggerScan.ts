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
  if (input.method === "git") {
    onUpdate?.({ stage: "triggering", percent: 0 });
    return triggerScanApi(projectId, {
      kind: "source",
      metadata: { source_type: "git" },
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
      metadata: { image_ref: imageRef },
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
    metadata: { source_type: "upload", archive_id },
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
