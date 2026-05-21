/**
 * ProjectListPage — unit tests (PR #9 task 2.11, updated feat/zip-upload).
 *
 * We mock the projectsApi wire layer, the ScanProgress component, and the
 * SourceSelectDialog so the tests focus on the page's behavior: loading,
 * empty/error, filter+sort, and the scan button opening the source dialog
 * which (when its scan starts) opens the progress drawer.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectListPage } from "@/features/projects/ProjectListPage";
import type {
  ProjectListResponse,
  ProjectPublic,
  ScanPublic,
} from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async () => {
  return {
    listProjects: vi.fn(),
  };
});

vi.mock("@/features/scan/ScanProgress", () => ({
  ScanProgress: ({ scanId, onClose }: { scanId: string; onClose?: () => void }) => (
    <div data-testid="scan-progress-mock" data-scan-id={scanId}>
      mock progress
      <button onClick={onClose}>close-mock</button>
    </div>
  ),
}));

// SourceSelectDialog is exercised in its own suite. Here we stub it so the
// list test can drive the scan-started callback directly without the file
// pickers / upload machinery.
vi.mock("@/features/scan/SourceSelectDialog", () => ({
  SourceSelectDialog: ({
    open,
    project,
    onScanStarted,
  }: {
    open: boolean;
    project: ProjectPublic;
    onScanStarted: (scan: ScanPublic, project: ProjectPublic) => void;
  }) =>
    open ? (
      <div data-testid="source-dialog-mock" data-project-id={project.id}>
        <button
          data-testid="source-dialog-start"
          onClick={() =>
            onScanStarted(
              {
                id: "scan-1",
                project_id: project.id,
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
              },
              project,
            )
          }
        >
          start
        </button>
      </div>
    ) : null,
}));

// react-virtuoso refuses to render items in jsdom because it relies on
// IntersectionObserver / ResizeObserver to measure the viewport. For unit
// tests we replace it with a plain map — the contract we care about
// (renders all items the parent passes through `data`) is identical.
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

import { listProjects } from "@/lib/projectsApi";

const mockedListProjects = vi.mocked(listProjects);

function project(name: string, overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  const id =
    overrides.id ??
    `00000000-0000-0000-0000-${name.padEnd(12, "0").slice(0, 12)}`;
  return {
    id,
    team_id: "team-1",
    name,
    slug: name.toLowerCase().replace(/\s+/g, "-"),
    description: null,
    git_url: `https://github.com/example/${name.toLowerCase()}`,
    default_branch: "main",
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function listResponse(items: ProjectPublic[]): ProjectListResponse {
  return { items, total: items.length, page: 1, size: 200 };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectListPage", () => {
  beforeEach(() => {
    mockedListProjects.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the list and shows project rows once data arrives", async () => {
    mockedListProjects.mockResolvedValueOnce(
      listResponse([project("Alpha"), project("Bravo")]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("project-row")).toHaveLength(2);
    });
    expect(screen.getByTestId("project-list-page")).toBeInTheDocument();
  });

  it("renders the empty state when no projects exist", async () => {
    mockedListProjects.mockResolvedValueOnce(listResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-list-empty")).toBeInTheDocument();
    });
  });

  it("renders an error alert when the list query fails", async () => {
    mockedListProjects.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-list-error")).toBeInTheDocument();
    });
  });

  it("filters rows by debounced search input", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedListProjects.mockResolvedValueOnce(
      listResponse([project("Alpha"), project("Bravo"), project("Charlie")]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("project-row")).toHaveLength(3);
    });
    const search = screen.getByTestId("project-search");
    await userEvent.type(search, "alp");
    // Wait past the 300ms debounce
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("project-row")).toHaveLength(1);
    });
    expect(screen.getAllByTestId("project-row")[0]).toHaveAttribute(
      "data-project-id",
      project("Alpha").id,
    );
  });

  it("opens the source dialog when the scan button is clicked", async () => {
    mockedListProjects.mockResolvedValueOnce(
      listResponse([project("Alpha")]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-row-scan")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("project-row-scan"));
    await waitFor(() => {
      expect(screen.getByTestId("source-dialog-mock")).toBeInTheDocument();
    });
    expect(screen.getByTestId("source-dialog-mock")).toHaveAttribute(
      "data-project-id",
      project("Alpha").id,
    );
  });

  it("opens the progress drawer once the source dialog reports a started scan", async () => {
    mockedListProjects.mockResolvedValueOnce(
      listResponse([project("Alpha")]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-row-scan")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("project-row-scan"));
    await userEvent.click(await screen.findByTestId("source-dialog-start"));
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-mock")).toBeInTheDocument();
    });
    expect(screen.getByTestId("scan-progress-mock")).toHaveAttribute(
      "data-scan-id",
      "scan-1",
    );
  });
});
