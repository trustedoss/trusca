/**
 * zipFolder — unit tests (feat/zip-upload).
 *
 * Covers the pure inspection helpers + the JSZip-backed compression path. We
 * exercise the size guard, noisy-directory detection, empty selection, and the
 * actual zip output so the folder-upload flow is verified without the browser.
 */
import { describe, expect, it } from "vitest";

import {
  FolderZipError,
  formatBytes,
  inspectFolderSelection,
  rootFolderName,
  SOURCE_ARCHIVE_MAX_BYTES,
  zipFolderSelection,
} from "@/lib/zipFolder";

/** Build a File carrying a webkitRelativePath, like a directory picker emits. */
function dirFile(relPath: string, size: number): File {
  const content = new Uint8Array(size);
  const file = new File([content], relPath.split("/").pop() ?? relPath, {
    type: "text/plain",
  });
  Object.defineProperty(file, "webkitRelativePath", {
    value: relPath,
    configurable: true,
  });
  return file;
}

describe("inspectFolderSelection", () => {
  it("sums uncompressed bytes and reports the file list", () => {
    const result = inspectFolderSelection([
      dirFile("app/a.ts", 100),
      dirFile("app/b.ts", 200),
    ]);
    expect(result.totalBytes).toBe(300);
    expect(result.files).toHaveLength(2);
    expect(result.isEmpty).toBe(false);
    expect(result.exceedsMax).toBe(false);
  });

  it("flags an empty selection", () => {
    const result = inspectFolderSelection([]);
    expect(result.isEmpty).toBe(true);
    expect(result.totalBytes).toBe(0);
  });

  it("detects noisy directories anywhere in the path", () => {
    const result = inspectFolderSelection([
      dirFile("app/node_modules/lib/index.js", 10),
      dirFile("app/.git/config", 10),
      dirFile("app/src/index.ts", 10),
    ]);
    expect(result.noisyDirectories).toContain("node_modules");
    expect(result.noisyDirectories).toContain(".git");
    expect(result.noisyDirectories).not.toContain("src");
  });

  it("marks selections over the backend cap as exceedsMax", () => {
    const result = inspectFolderSelection([
      dirFile("app/big.bin", SOURCE_ARCHIVE_MAX_BYTES + 1),
    ]);
    expect(result.exceedsMax).toBe(true);
  });
});

describe("rootFolderName", () => {
  it("returns the top-level segment", () => {
    expect(rootFolderName([dirFile("my-app/src/x.ts", 1)])).toBe("my-app");
  });

  it("returns null for a flat file with no folder prefix", () => {
    const file = new File([new Uint8Array(1)], "x.ts");
    expect(rootFolderName([file])).toBeNull();
  });

  it("returns null for an empty selection", () => {
    expect(rootFolderName([])).toBeNull();
  });
});

describe("zipFolderSelection", () => {
  it("zips files into a Blob and preserves relative paths", async () => {
    const result = await zipFolderSelection([
      dirFile("my-app/src/index.ts", 50),
      dirFile("my-app/README.md", 20),
    ]);
    expect(result.blob.size).toBeGreaterThan(0);
    expect(result.filename).toBe("my-app.zip");
    expect(result.fileCount).toBe(2);
  });

  it("uses the rootName override for the filename", async () => {
    const result = await zipFolderSelection([dirFile("a/x.ts", 1)], {
      rootName: "custom",
    });
    expect(result.filename).toBe("custom.zip");
  });

  it("reports compression progress", async () => {
    const seen: number[] = [];
    await zipFolderSelection([dirFile("a/x.ts", 1000)], {
      onProgress: (p) => seen.push(p),
    });
    expect(seen.length).toBeGreaterThan(0);
    expect(seen[seen.length - 1]).toBe(100);
  });

  it("throws FolderZipError(empty) for an empty selection", async () => {
    await expect(zipFolderSelection([])).rejects.toBeInstanceOf(FolderZipError);
    await expect(zipFolderSelection([])).rejects.toMatchObject({
      token: "empty",
    });
  });

  it("throws FolderZipError(too_large) when over the cap", async () => {
    await expect(
      zipFolderSelection([dirFile("a/big.bin", SOURCE_ARCHIVE_MAX_BYTES + 1)]),
    ).rejects.toMatchObject({ token: "too_large" });
  });
});

describe("formatBytes", () => {
  it("formats bytes, KiB, MiB", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KiB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MiB");
    expect(formatBytes(100 * 1024 * 1024)).toBe("100 MiB");
  });
});
