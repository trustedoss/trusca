/**
 * useTriggerScan — unit tests (feat/zip-upload).
 *
 * Mocks the three lower-level calls (triggerScan, uploadSourceArchive,
 * zipFolderSelection) so we verify the orchestration: which metadata each
 * source method sends, that folder zips before uploading, and that progress
 * stages fire in order.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  type TriggerScanProgress,
  useTriggerScan,
} from "@/hooks/useTriggerScan";
import type { ScanPublic } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", () => ({
  triggerScan: vi.fn(),
}));
vi.mock("@/lib/sourceArchiveApi", () => ({
  uploadSourceArchive: vi.fn(),
}));
vi.mock("@/lib/zipFolder", () => ({
  zipFolderSelection: vi.fn(),
}));

import { triggerScan } from "@/lib/projectsApi";
import { uploadSourceArchive } from "@/lib/sourceArchiveApi";
import { zipFolderSelection } from "@/lib/zipFolder";

const mockedTrigger = vi.mocked(triggerScan);
const mockedUpload = vi.mocked(uploadSourceArchive);
const mockedZip = vi.mocked(zipFolderSelection);

function fakeScan(): ScanPublic {
  return {
    id: "scan-1",
    project_id: "proj-1",
    kind: "source",
    status: "queued",
    progress_percent: 0,
    current_step: null,
    started_at: null,
    completed_at: null,
    error_message: null,
    requested_by_user_id: null,
    celery_task_id: null,
    metadata: {},
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useTriggerScan", () => {
  beforeEach(() => {
    mockedTrigger.mockReset();
    mockedUpload.mockReset();
    mockedZip.mockReset();
  });

  it("git method triggers with source_type git and no upload", async () => {
    mockedTrigger.mockResolvedValue(fakeScan());
    const { result } = renderHook(() => useTriggerScan("proj-1"), { wrapper });
    await result.current.mutateAsync({ method: "git" });
    expect(mockedUpload).not.toHaveBeenCalled();
    expect(mockedTrigger).toHaveBeenCalledWith("proj-1", {
      kind: "source",
      metadata: { source_type: "git" },
    });
  });

  it("upload method posts the file then triggers with archive_id", async () => {
    mockedUpload.mockResolvedValue({ archive_id: "arch-7" });
    mockedTrigger.mockResolvedValue(fakeScan());
    const file = new File([new Uint8Array(4)], "src.zip");
    const { result } = renderHook(() => useTriggerScan("proj-1"), { wrapper });
    await result.current.mutateAsync({ method: "upload", file });
    expect(mockedUpload).toHaveBeenCalledWith(
      "proj-1",
      file,
      expect.objectContaining({ onProgress: expect.any(Function) }),
    );
    expect(mockedTrigger).toHaveBeenCalledWith("proj-1", {
      kind: "source",
      metadata: { source_type: "upload", archive_id: "arch-7" },
    });
  });

  it("folder method zips, uploads the blob, then triggers", async () => {
    const blob = new Blob([new Uint8Array(8)]);
    mockedZip.mockResolvedValue({ blob, filename: "app.zip", fileCount: 2 });
    mockedUpload.mockResolvedValue({ archive_id: "arch-9" });
    mockedTrigger.mockResolvedValue(fakeScan());
    const folderFiles = [new File([new Uint8Array(2)], "a.ts")];
    const { result } = renderHook(() => useTriggerScan("proj-1"), { wrapper });
    await result.current.mutateAsync({
      method: "folder",
      folderFiles,
      rootName: "app",
    });
    expect(mockedZip).toHaveBeenCalled();
    expect(mockedUpload).toHaveBeenCalledWith(
      "proj-1",
      expect.any(File),
      expect.any(Object),
    );
    expect(mockedTrigger).toHaveBeenCalledWith("proj-1", {
      kind: "source",
      metadata: { source_type: "upload", archive_id: "arch-9" },
    });
  });

  it("emits progress stages in order for the upload path", async () => {
    mockedUpload.mockResolvedValue({ archive_id: "a" });
    mockedTrigger.mockResolvedValue(fakeScan());
    const stages: string[] = [];
    const onUpdate = (p: TriggerScanProgress) => stages.push(p.stage);
    const file = new File([new Uint8Array(4)], "src.zip");
    const { result } = renderHook(() => useTriggerScan("proj-1", { onUpdate }), {
      wrapper,
    });
    await result.current.mutateAsync({ method: "upload", file });
    expect(stages).toContain("uploading");
    expect(stages).toContain("triggering");
  });

  it("surfaces an error when a folder selection is missing files", async () => {
    const { result } = renderHook(() => useTriggerScan("proj-1"), { wrapper });
    await expect(
      result.current.mutateAsync({ method: "folder" }),
    ).rejects.toThrow();
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("container method triggers kind=container with the trimmed image_ref in metadata", async () => {
    mockedTrigger.mockResolvedValue({ ...fakeScan(), kind: "container" });
    const { result } = renderHook(() => useTriggerScan("proj-1"), { wrapper });
    await result.current.mutateAsync({
      method: "container",
      imageRef: "  alpine:3.19 ",
    });
    expect(mockedUpload).not.toHaveBeenCalled();
    expect(mockedTrigger).toHaveBeenCalledWith("proj-1", {
      kind: "container",
      metadata: { image_ref: "alpine:3.19" },
    });
  });

  it("rejects a container scan with a blank image_ref before any trigger", async () => {
    const { result } = renderHook(() => useTriggerScan("proj-1"), { wrapper });
    await expect(
      result.current.mutateAsync({ method: "container", imageRef: "   " }),
    ).rejects.toThrow();
    expect(mockedTrigger).not.toHaveBeenCalled();
  });
});
