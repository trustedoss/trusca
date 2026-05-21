/**
 * SourceSelectDialog — unit tests (feat/zip-upload).
 *
 * We mock the useTriggerScan hook so the dialog's UI behavior is the unit
 * under test: method selection, the .zip extension guard, folder inspection
 * warnings (noisy dirs / too large), the missing-git-url guidance, and the
 * happy path that hands a scan back through onScanStarted + maps a server
 * 507 to the quota error copy.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SourceSelectDialog } from "@/features/scan/SourceSelectDialog";
import type { ProjectPublic, ScanPublic } from "@/lib/projectsApi";
import { ProblemError } from "@/lib/problem";
import { SOURCE_ARCHIVE_MAX_BYTES } from "@/lib/zipFolder";

// ---- hook mock -------------------------------------------------------------
const mutateAsync = vi.fn();
let hookState: { isPending: boolean; error: unknown } = {
  isPending: false,
  error: null,
};
vi.mock("@/hooks/useTriggerScan", async () => {
  const actual = await vi.importActual<
    typeof import("@/hooks/useTriggerScan")
  >("@/hooks/useTriggerScan");
  return {
    ...actual,
    useTriggerScan: () => ({
      mutateAsync,
      reset: vi.fn(),
      isPending: hookState.isPending,
      error: hookState.error,
    }),
  };
});

function project(overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  return {
    id: "proj-1",
    team_id: "team-1",
    name: "Demo",
    slug: "demo",
    description: null,
    git_url: "https://github.com/example/demo",
    default_branch: "main",
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...overrides,
  };
}

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

function dirFile(relPath: string, size: number): File {
  const file = new File([new Uint8Array(size)], relPath.split("/").pop() ?? relPath);
  Object.defineProperty(file, "webkitRelativePath", {
    value: relPath,
    configurable: true,
  });
  return file;
}

function renderDialog(props: Partial<Parameters<typeof SourceSelectDialog>[0]> = {}) {
  const onScanStarted = props.onScanStarted ?? vi.fn();
  const onOpenChange = props.onOpenChange ?? vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SourceSelectDialog
        open
        onOpenChange={onOpenChange}
        project={props.project ?? project()}
        onScanStarted={onScanStarted}
      />
    </QueryClientProvider>,
  );
  return { onScanStarted, onOpenChange };
}

describe("SourceSelectDialog", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    hookState = { isPending: false, error: null };
  });

  it("renders the three source-method radios", () => {
    renderDialog();
    expect(screen.getByTestId("source-method-git")).toBeInTheDocument();
    expect(screen.getByTestId("source-method-upload")).toBeInTheDocument();
    expect(screen.getByTestId("source-method-folder")).toBeInTheDocument();
    // Git is default-selected when the project has a git_url.
    expect(screen.getByTestId("source-method-git")).toHaveAttribute(
      "data-active",
      "true",
    );
  });

  it("shows guidance and disables git when the project has no git_url", () => {
    renderDialog({ project: project({ git_url: null }) });
    // Upload becomes the default active method.
    expect(screen.getByTestId("source-method-upload")).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(screen.getByTestId("source-method-git")).toBeDisabled();
  });

  it("rejects a non-zip file with an inline error", async () => {
    renderDialog();
    await userEvent.click(screen.getByTestId("source-method-upload"));
    const input = screen.getByTestId("source-zip-input");
    // applyAccept:false bypasses the input's accept filter so the component's
    // own extension guard (drag-drop / programmatic paths in the browser) is
    // the unit under test, not the browser's native filter.
    await userEvent.upload(
      input,
      new File([new Uint8Array(4)], "notes.txt", { type: "text/plain" }),
      { applyAccept: false },
    );
    expect(await screen.findByTestId("source-error")).toBeInTheDocument();
    expect(screen.getByTestId("source-submit")).toBeDisabled();
  });

  it("triggers a scan from a valid .zip and hands the scan to the parent", async () => {
    mutateAsync.mockResolvedValue(fakeScan());
    const { onScanStarted } = renderDialog();
    await userEvent.click(screen.getByTestId("source-method-upload"));
    await userEvent.upload(
      screen.getByTestId("source-zip-input"),
      new File([new Uint8Array(8)], "src.zip", { type: "application/zip" }),
    );
    expect(screen.getByTestId("source-upload-selected")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("source-submit"));
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ method: "upload" }),
      );
    });
    expect(onScanStarted).toHaveBeenCalled();
  });

  it("warns about noisy directories and a too-large folder selection", async () => {
    renderDialog();
    await userEvent.click(screen.getByTestId("source-method-folder"));
    const input = screen.getByTestId("source-folder-input");
    await userEvent.upload(input, [
      dirFile("app/node_modules/x.js", SOURCE_ARCHIVE_MAX_BYTES + 1),
    ]);
    expect(await screen.findByTestId("source-folder-noisy")).toBeInTheDocument();
    expect(screen.getByTestId("source-folder-too-large")).toBeInTheDocument();
    // Over-cap selection must not be submittable.
    expect(screen.getByTestId("source-submit")).toBeDisabled();
  });

  it("submits the git path with no file and closes on success", async () => {
    mutateAsync.mockResolvedValue(fakeScan());
    const { onScanStarted, onOpenChange } = renderDialog();
    // Git is the default active method when the project has a git_url.
    await userEvent.click(screen.getByTestId("source-submit"));
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ method: "git" });
    });
    expect(onScanStarted).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("maps a client-side FolderZipError to the empty-folder copy", async () => {
    const { FolderZipError } = await import("@/lib/zipFolder");
    mutateAsync.mockRejectedValue(new FolderZipError("empty", "no files"));
    renderDialog();
    await userEvent.click(screen.getByTestId("source-method-folder"));
    await userEvent.upload(
      screen.getByTestId("source-folder-input"),
      [dirFile("app/src/index.ts", 10)],
    );
    await userEvent.click(screen.getByTestId("source-submit"));
    expect(await screen.findByTestId("source-error")).toBeInTheDocument();
  });

  it("maps a server 507 to the quota error copy", async () => {
    hookState = {
      isPending: false,
      error: new ProblemError("quota", {
        status: 507,
        title: "Insufficient Storage",
        detail: "quota",
        problem: {
          type: "about:blank",
          title: "Insufficient Storage",
          status: 507,
          detail: "quota",
        },
      }),
    };
    renderDialog();
    expect(screen.getByTestId("source-error")).toHaveTextContent(/quota/i);
  });
});
