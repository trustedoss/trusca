/**
 * SourceTab — unit tests (G3.3).
 *
 * The tab wires the two-pane layout, the `?path=` deep-link, and the single
 * "no preserved source" empty state. The tree + file hooks are mocked so the
 * test asserts the tab's URL + composition behavior, not the children.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { SourceTab } from "@/features/projects/components/SourceTab";

// Mock the tree so we can drive onSelectFile / onNoSource from the test.
const treeProps: {
  selectedPath: string | null;
  onSelectFile: ((p: string) => void) | null;
  onNoSource: (() => void) | null;
} = { selectedPath: null, onSelectFile: null, onNoSource: null };

vi.mock("@/features/projects/components/SourceTree", () => ({
  SourceTree: (props: {
    selectedPath: string | null;
    onSelectFile: (p: string) => void;
    onNoSource?: () => void;
  }) => {
    treeProps.selectedPath = props.selectedPath;
    treeProps.onSelectFile = props.onSelectFile;
    treeProps.onNoSource = props.onNoSource ?? null;
    return (
      <div data-testid="source-tree-mock" data-selected={props.selectedPath ?? ""}>
        <button
          type="button"
          data-testid="mock-select-file"
          onClick={() => props.onSelectFile("src/app.py")}
        >
          select
        </button>
        <button
          type="button"
          data-testid="mock-no-source"
          onClick={() => props.onNoSource?.()}
        >
          no-source
        </button>
      </div>
    );
  },
}));

// The viewer is exercised in its own test; here we just confirm it mounts and
// receives the selected path.
vi.mock("@/features/projects/components/SourceFileViewer", () => ({
  SourceFileViewer: ({ selectedPath }: { selectedPath: string | null }) => (
    <div data-testid="source-viewer-mock" data-selected={selectedPath ?? ""} />
  ),
}));

vi.mock("@/features/projects/api/useSourceFile", () => ({
  useSourceFile: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

function URLProbe() {
  const [params] = useSearchParams();
  return <div data-testid="url-probe" data-path={params.get("path") ?? ""} />;
}

function renderTab(initialEntries: string[] = ["/projects/proj-1?tab=source"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <SourceTab projectId="proj-1" projectName="Demo" />
        <URLProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SourceTab", () => {
  it("renders the two-pane layout (tree + viewer)", () => {
    renderTab();
    expect(screen.getByTestId("source-tab-tree-pane")).toBeInTheDocument();
    expect(screen.getByTestId("source-tab-viewer-pane")).toBeInTheDocument();
  });

  it("mirrors the selected file into ?path= for deep-linking", async () => {
    renderTab();
    expect(screen.getByTestId("url-probe")).toHaveAttribute("data-path", "");
    await userEvent.click(screen.getByTestId("mock-select-file"));
    await waitFor(() => {
      expect(screen.getByTestId("url-probe")).toHaveAttribute(
        "data-path",
        "src/app.py",
      );
    });
  });

  it("hydrates the selected path from ?path= on first render (reload survival)", () => {
    renderTab(["/projects/proj-1?tab=source&path=docs/READ.md"]);
    expect(screen.getByTestId("source-tree-mock")).toHaveAttribute(
      "data-selected",
      "docs/READ.md",
    );
    expect(screen.getByTestId("source-viewer-mock")).toHaveAttribute(
      "data-selected",
      "docs/READ.md",
    );
  });

  it("swaps to the single 're-scan to enable' empty state on no preserved source", async () => {
    renderTab();
    await userEvent.click(screen.getByTestId("mock-no-source"));
    await waitFor(() => {
      expect(screen.getByTestId("source-no-preserved")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("source-tab-tree-pane")).not.toBeInTheDocument();
  });
});
