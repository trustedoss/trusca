/**
 * SourceFileViewer — unit tests (G3.3).
 *
 * The viewer renders one file's content line-by-line with per-line license
 * highlighting and handles the binary / truncated / 404 / no-selection states.
 * Virtuoso is stubbed to render every row so highlight assertions are visible
 * without a real scroll viewport.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { describe, expect, it, vi } from "vitest";

import type { SourceFileResponse } from "@/features/projects/api/sourceTreeApi";
import { SourceFileViewer } from "@/features/projects/components/SourceFileViewer";
import { ProblemError } from "@/lib/problem";

const getSourceFileRaw = vi.fn();
vi.mock("@/features/projects/api/sourceTreeApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/sourceTreeApi")
  >("@/features/projects/api/sourceTreeApi");
  return {
    ...actual,
    getSourceFileRaw: (...args: unknown[]) => getSourceFileRaw(...args),
  };
});

vi.mock("react-virtuoso", () => ({
  Virtuoso: <T,>({
    data,
    itemContent,
  }: {
    data: T[];
    itemContent: (index: number, item: T) => React.ReactNode;
  }) => (
    <div data-testid="virtuoso-stub">
      {data.map((item, idx) => (
        <div key={idx}>{itemContent(idx, item)}</div>
      ))}
    </div>
  ),
}));

const triggerBlobDownload = vi.fn();
vi.mock("@/lib/download", async () => {
  const actual = await vi.importActual<typeof import("@/lib/download")>(
    "@/lib/download",
  );
  return {
    ...actual,
    triggerBlobDownload: (...args: unknown[]) => triggerBlobDownload(...args),
  };
});

function file(overrides: Partial<SourceFileResponse> = {}): SourceFileResponse {
  return {
    scan_id: "scan-1",
    path: "src/main.py",
    byte_size: 42,
    truncated: false,
    encoding: "utf-8",
    content: "line one\nline two\nline three",
    license_matches: [],
    ...overrides,
  };
}

function renderViewer(props: Partial<React.ComponentProps<typeof SourceFileViewer>> = {}) {
  return render(
    <SourceFileViewer
      projectId="project-1"
      projectName="Demo"
      selectedPath="src/main.py"
      data={file()}
      isLoading={false}
      isError={false}
      error={null}
      {...props}
    />,
  );
}

describe("SourceFileViewer", () => {
  it("prompts to select a file when nothing is selected", () => {
    renderViewer({ selectedPath: null, data: undefined });
    expect(
      screen.getByTestId("source-file-empty-selection"),
    ).toBeInTheDocument();
  });

  it("renders content line-by-line with line numbers", () => {
    renderViewer();
    const lines = screen.getAllByTestId("source-line");
    expect(lines).toHaveLength(3);
    expect(lines.map((l) => l.getAttribute("data-line"))).toEqual([
      "1",
      "2",
      "3",
    ]);
  });

  it("tints + chips only the lines inside a license match range", () => {
    renderViewer({
      data: file({
        license_matches: [
          { spdx_id: "MIT", start_line: 2, end_line: 2, score: 99.5 },
        ],
      }),
    });
    const lines = screen.getAllByTestId("source-line");
    expect(lines[0]).toHaveAttribute("data-highlighted", "false");
    expect(lines[1]).toHaveAttribute("data-highlighted", "true");
    expect(lines[2]).toHaveAttribute("data-highlighted", "false");
    // The highlighted line carries a license chip + an spdx+score tooltip.
    const chip = screen.getByTestId("source-line-license-chip");
    expect(chip).toHaveAttribute("data-spdx-ids", "MIT");
    expect(lines[1].getAttribute("title")).toContain("MIT");
    expect(lines[1].getAttribute("title")).toContain("100%");
  });

  it("shows the binary message and renders no source lines for a binary file", () => {
    renderViewer({
      data: file({ encoding: "binary", content: null }),
    });
    expect(screen.getByTestId("source-file-binary")).toBeInTheDocument();
    expect(screen.queryByTestId("source-line")).not.toBeInTheDocument();
  });

  it("offers a raw full-file download for a binary file (G3.3)", async () => {
    getSourceFileRaw.mockClear();
    triggerBlobDownload.mockClear();
    getSourceFileRaw.mockResolvedValue({
      blob: new Blob([new Uint8Array([1, 2, 3])]),
      filename: "main.py",
    });
    renderViewer({
      data: file({ encoding: "binary", content: null }),
    });
    await userEvent.click(screen.getByTestId("source-file-binary-download"));
    expect(getSourceFileRaw).toHaveBeenCalledWith("project-1", {
      path: "src/main.py",
    });
    expect(triggerBlobDownload).toHaveBeenCalledTimes(1);
    const [blob, filename] = triggerBlobDownload.mock.calls[0];
    expect(blob).toBeInstanceOf(Blob);
    expect(filename).toBe("main.py");
  });

  it("shows the truncated banner and downloads the FULL file via the raw path", async () => {
    getSourceFileRaw.mockClear();
    triggerBlobDownload.mockClear();
    getSourceFileRaw.mockResolvedValue({
      blob: new Blob(["the whole file"]),
      filename: "main.py",
    });
    renderViewer({
      data: file({ truncated: true, content: "partial\ncontent" }),
    });
    expect(
      screen.getByTestId("source-file-truncated-banner"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("source-file-download"));
    // The button hits the raw endpoint (full member), NOT the capped viewer
    // bytes already in `data.content`.
    expect(getSourceFileRaw).toHaveBeenCalledWith("project-1", {
      path: "src/main.py",
    });
    expect(triggerBlobDownload).toHaveBeenCalledTimes(1);
    const [blob, filename] = triggerBlobDownload.mock.calls[0];
    expect(blob).toBeInstanceOf(Blob);
    expect(filename).toBe("main.py");
  });

  it("renders the 're-scan to enable' empty state on a 404", () => {
    renderViewer({
      data: undefined,
      isError: true,
      error: new ProblemError("no source", {
        status: 404,
        title: "Not Found",
        detail: "no preserved source",
        problem: null,
      }),
    });
    expect(screen.getByTestId("source-file-not-found")).toBeInTheDocument();
    expect(screen.queryByTestId("source-file-error")).not.toBeInTheDocument();
  });

  it("surfaces a non-404 error verbatim in a destructive alert", () => {
    renderViewer({
      data: undefined,
      isError: true,
      error: new ProblemError("boom", {
        status: 500,
        title: "Server Error",
        detail: "internal failure — surfaced verbatim",
        problem: null,
      }),
    });
    expect(screen.getByTestId("source-file-error").textContent).toContain(
      "internal failure — surfaced verbatim",
    );
  });

  it("renders skeletons while loading", () => {
    renderViewer({ data: undefined, isLoading: true });
    expect(screen.getByTestId("source-file-loading")).toBeInTheDocument();
  });
});
