/**
 * Client-side folder → zip — feat/zip-upload.
 *
 * The "Select folder" upload path uses `<input type="file" webkitdirectory>`,
 * which yields a flat `FileList` whose entries carry a `webkitRelativePath`
 * (e.g. `my-app/src/index.ts`). We zip those in the browser with JSZip and
 * post the resulting Blob to the same `source-archive` endpoint the explicit
 * `.zip` path uses — so the backend sees one contract.
 *
 * Guards (mirrors backend `SOURCE_ARCHIVE_MAX_BYTES` = 100 MiB):
 *   - reject empty selections before doing any work,
 *   - sum the *uncompressed* size and refuse > 100 MiB up front (compression
 *     can only shrink it, so this is a safe early bail that avoids a doomed
 *     413 round-trip),
 *   - flag noisy directories (`node_modules`, `.git`, build outputs) so the
 *     UI can warn the user they're about to ship megabytes of vendored code.
 */
import JSZip from "jszip";

/** Backend `SOURCE_ARCHIVE_MAX_BYTES`. Kept in sync manually. */
export const SOURCE_ARCHIVE_MAX_BYTES = 100 * 1024 * 1024;

/**
 * Directory names that bloat an archive without adding signal. We surface a
 * warning (not a hard block) so the user can still proceed if they really
 * mean to include them.
 */
export const NOISY_DIRECTORIES = [
  "node_modules",
  ".git",
  "dist",
  "build",
  ".next",
  "target",
  "vendor",
  ".venv",
  "__pycache__",
] as const;

export interface FolderInspection {
  /** Files that will actually be zipped. */
  files: File[];
  /** Sum of uncompressed bytes. */
  totalBytes: number;
  /** Whether the selection is empty (nothing to upload). */
  isEmpty: boolean;
  /** Whether the uncompressed total already exceeds the backend cap. */
  exceedsMax: boolean;
  /** Noisy directory names detected in the selection (deduped). */
  noisyDirectories: string[];
}

/** The `webkitRelativePath` field is non-standard; type it explicitly. */
type DirectoryFile = File & { webkitRelativePath?: string };

function relativePath(file: File): string {
  const rel = (file as DirectoryFile).webkitRelativePath;
  return rel && rel.length > 0 ? rel : file.name;
}

/**
 * Inspect a folder selection without zipping. Pure + synchronous so the UI can
 * render warnings the instant the user picks a folder, before committing to
 * the (potentially slow) zip step.
 */
export function inspectFolderSelection(fileList: FileList | File[]): FolderInspection {
  const files = Array.from(fileList);
  let totalBytes = 0;
  const noisy = new Set<string>();

  for (const file of files) {
    totalBytes += file.size;
    const segments = relativePath(file).split("/");
    for (const dir of NOISY_DIRECTORIES) {
      if (segments.includes(dir)) {
        noisy.add(dir);
      }
    }
  }

  return {
    files,
    totalBytes,
    isEmpty: files.length === 0,
    exceedsMax: totalBytes > SOURCE_ARCHIVE_MAX_BYTES,
    noisyDirectories: [...noisy],
  };
}

export class FolderZipError extends Error {
  readonly token: "empty" | "too_large";
  constructor(token: "empty" | "too_large", message: string) {
    super(message);
    this.name = "FolderZipError";
    this.token = token;
  }
}

export interface ZipFolderOptions {
  /** Name embedded in the generated Blob's filename (`<rootName>.zip`). */
  rootName?: string;
  /** 0–100 compression progress callback (JSZip `streamFiles` metadata). */
  onProgress?: (percent: number) => void;
}

/**
 * Zip a folder selection into a single `.zip` Blob, preserving the relative
 * paths so the extracted tree on the server matches what the user picked.
 *
 * Throws {@link FolderZipError} for the two pre-flight failures (empty / too
 * large) so the caller can map them onto the same i18n keys the upload errors
 * use, instead of letting a doomed request hit the network.
 */
export async function zipFolderSelection(
  fileList: FileList | File[],
  options: ZipFolderOptions = {},
): Promise<{ blob: Blob; filename: string; fileCount: number }> {
  const inspection = inspectFolderSelection(fileList);
  if (inspection.isEmpty) {
    throw new FolderZipError("empty", "no files selected");
  }
  if (inspection.exceedsMax) {
    throw new FolderZipError("too_large", "selection exceeds the size limit");
  }

  const zip = new JSZip();
  for (const file of inspection.files) {
    zip.file(relativePath(file), file);
  }

  const blob = await zip.generateAsync(
    {
      type: "blob",
      compression: "DEFLATE",
      compressionOptions: { level: 6 },
    },
    (metadata) => {
      options.onProgress?.(Math.round(metadata.percent));
    },
  );

  // Prefer the caller-supplied name, then the selection's top-level folder,
  // then a generic fallback so the generated Blob always carries a filename.
  const rootName =
    options.rootName?.trim() || rootFolderName(inspection.files) || "source";
  return {
    blob,
    filename: `${rootName}.zip`,
    fileCount: inspection.files.length,
  };
}

/**
 * Derive the top-level folder name from a selection (the first path segment
 * of the first file's relative path). Used to name the generated zip.
 */
export function rootFolderName(fileList: FileList | File[]): string | null {
  const files = Array.from(fileList);
  if (files.length === 0) return null;
  const segments = relativePath(files[0]).split("/");
  return segments.length > 1 ? segments[0] : null;
}

/** Human-readable byte size — small dependency-free formatter for the UI. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}
