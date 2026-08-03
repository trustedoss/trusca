/**
 * SourceTree — unit tests (G3.3).
 *
 * The tree loads each directory's children lazily (one query per expanded
 * node) and renders per-file license badges. Tests cover the root listing,
 * lazy child fetch on expand, file selection, license badges, and the 404
 * "no preserved source" signal that bubbles to the tab via onNoSource.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  SourceTreeEntry,
  SourceTreePage,
} from "@/features/projects/api/sourceTreeApi";
import { SourceTree } from "@/features/projects/components/SourceTree";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/sourceTreeApi", async () => ({
  getSourceTree: vi.fn(),
  getSourceFile: vi.fn(),
}));

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

import { getSourceTree } from "@/features/projects/api/sourceTreeApi";

const mockedTree = vi.mocked(getSourceTree);

function entry(
  name: string,
  is_dir: boolean,
  overrides: Partial<SourceTreeEntry> = {},
): SourceTreeEntry {
  return {
    name,
    path: overrides.path ?? name,
    is_dir,
    byte_size: overrides.byte_size ?? (is_dir ? 0 : 100),
    license_spdx_ids: overrides.license_spdx_ids ?? [],
  };
}

function page(entries: SourceTreeEntry[], path = ""): SourceTreePage {
  return {
    scan_id: "scan-1",
    path,
    entries,
    total: entries.length,
    page: 1,
    size: 500,
  };
}

function renderTree(
  onSelectFile = vi.fn(),
  onNoSource = vi.fn(),
  selectedPath: string | null = null,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SourceTree
        projectId="proj-1"
        selectedPath={selectedPath}
        onSelectFile={onSelectFile}
        onNoSource={onNoSource}
      />
    </QueryClientProvider>,
  );
  return { onSelectFile, onNoSource };
}

describe("SourceTree", () => {
  beforeEach(() => {
    mockedTree.mockReset();
  });

  it("lists the root directory's immediate children", async () => {
    mockedTree.mockResolvedValueOnce(
      page([
        entry("src", true, { path: "src" }),
        entry("LICENSE", false, {
          path: "LICENSE",
          license_spdx_ids: ["MIT"],
        }),
      ]),
    );
    renderTree();
    await waitFor(() => {
      expect(screen.getAllByTestId("source-tree-row")).toHaveLength(2);
    });
    // Root listing requested with the empty (root) path.
    expect(mockedTree).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ path: "" }),
    );
  });

  it("renders per-file license badges from license_spdx_ids", async () => {
    mockedTree.mockResolvedValueOnce(
      page([
        entry("LICENSE", false, {
          path: "LICENSE",
          license_spdx_ids: ["MIT", "Apache-2.0", "BSD-3-Clause"],
        }),
      ]),
    );
    renderTree();
    await waitFor(() => {
      expect(screen.getByTestId("source-tree-row")).toBeInTheDocument();
    });
    const badges = screen.getAllByTestId("source-tree-license-badge");
    // First two shown inline...
    expect(badges.map((b) => b.getAttribute("data-spdx-id"))).toEqual([
      "MIT",
      "Apache-2.0",
    ]);
    // ...and the remainder collapse into a "+N" overflow badge.
    expect(screen.getByTestId("source-tree-license-overflow").textContent).toBe(
      "+1",
    );
  });

  it("lazily fetches a directory's children only when it is expanded", async () => {
    mockedTree.mockResolvedValueOnce(
      page([entry("src", true, { path: "src" })]),
    );
    renderTree();
    await waitFor(() => {
      expect(screen.getByTestId("source-tree-row")).toBeInTheDocument();
    });
    // Only the root listing has been requested so far.
    expect(mockedTree).toHaveBeenCalledTimes(1);

    // Expanding the dir triggers a second query keyed by its path.
    mockedTree.mockResolvedValueOnce(
      page([entry("main.py", false, { path: "src/main.py" })], "src"),
    );
    const dirRow = screen.getByTestId("source-tree-row");
    await userEvent.click(dirRow);
    await waitFor(() => {
      expect(mockedTree).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ path: "src" }),
      );
    });
    // The dir row is now expanded and its lazily-loaded child has mounted —
    // proving the child query only fired on expand.
    await waitFor(() => {
      expect(dirRow.getAttribute("data-expanded")).toBe("true");
    });
    await waitFor(() => {
      expect(
        screen.getAllByTestId("source-tree-row").some(
          (row) => row.getAttribute("data-path") === "src/main.py",
        ),
      ).toBe(true);
    });
  });

  it("selects a file (not a dir) on click via onSelectFile", async () => {
    mockedTree.mockResolvedValueOnce(
      page([entry("README.md", false, { path: "README.md" })]),
    );
    const onSelectFile = vi.fn();
    renderTree(onSelectFile);
    await waitFor(() => {
      expect(screen.getByTestId("source-tree-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("source-tree-row"));
    expect(onSelectFile).toHaveBeenCalledWith("README.md");
  });

  it("signals onNoSource and renders nothing when the root is a 404", async () => {
    mockedTree.mockRejectedValueOnce(
      new ProblemError("no source", {
        status: 404,
        title: "Not Found",
        detail: "no preserved source",
        problem: null,
      }),
    );
    const onNoSource = vi.fn();
    renderTree(vi.fn(), onNoSource);
    await waitFor(() => {
      expect(onNoSource).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("source-tree-row")).not.toBeInTheDocument();
  });
});
