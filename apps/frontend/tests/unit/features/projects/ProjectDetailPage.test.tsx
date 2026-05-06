/**
 * ProjectDetailPage — unit tests (PR #10).
 *
 * Validates the tab strip, tab parameter sync, breadcrumb, and that the
 * Vulnerabilities / Licenses placeholder tabs are disabled until PR #11/#12.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ProjectOverviewResponse,
} from "@/features/projects/api/projectDetailApi";
import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import type { ProjectPublic } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async () => {
  return {
    getProject: vi.fn(),
    listProjects: vi.fn(),
    triggerScan: vi.fn(),
  };
});

vi.mock("@/features/projects/api/projectDetailApi", async () => {
  return {
    getProjectOverview: vi.fn(),
    listProjectComponents: vi.fn(),
    getComponent: vi.fn(),
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

import { getProject } from "@/lib/projectsApi";
import {
  getProjectOverview,
  listProjectComponents,
} from "@/features/projects/api/projectDetailApi";

const mockedGetProject = vi.mocked(getProject);
const mockedOverview = vi.mocked(getProjectOverview);
const mockedListComponents = vi.mocked(listProjectComponents);

function project(overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  return {
    id: "proj-1",
    team_id: "team-1",
    name: "Demo Project",
    slug: "demo-project",
    description: null,
    git_url: "https://github.com/example/demo",
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

function overview(
  overrides: Partial<ProjectOverviewResponse> = {},
): ProjectOverviewResponse {
  return {
    project_id: "proj-1",
    project_name: "Demo Project",
    total_components: 5,
    severity_distribution: { critical: 1, high: 1 },
    license_distribution: { allowed: 4, forbidden: 1 },
    risk_score: 60,
    recent_scans: [],
    last_scan_at: null,
    ...overrides,
  };
}

function renderPage(initialPath = "/projects/proj-1") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectDetailPage", () => {
  beforeEach(() => {
    mockedGetProject.mockReset();
    mockedOverview.mockReset();
    mockedListComponents.mockReset();
    mockedListComponents.mockResolvedValue({
      items: [],
      total: 0,
      limit: 100,
      offset: 0,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the project name in the breadcrumb once loaded", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValueOnce(overview());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-detail-title").textContent).toBe(
        "Demo Project",
      );
    });
    expect(screen.getByTestId("project-detail-id").textContent).toBe("proj-1");
  });

  it("renders the four tab triggers, with only licenses disabled (PR #11 enabled vulnerabilities)", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValueOnce(overview());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-detail-tabs")).toBeInTheDocument();
    });
    expect(screen.getByTestId("project-detail-tab-overview")).toBeEnabled();
    expect(screen.getByTestId("project-detail-tab-components")).toBeEnabled();
    // PR #11 lit up the Vulnerabilities tab; licenses remains a placeholder
    // until PR #12.
    expect(
      screen.getByTestId("project-detail-tab-vulnerabilities"),
    ).toBeEnabled();
    expect(screen.getByTestId("project-detail-tab-licenses")).toBeDisabled();
  });

  it("switches to the Components tab on click", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValueOnce(overview());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("project-detail-tab-components"));
    await waitFor(() => {
      expect(screen.getByTestId("components-tab")).toBeInTheDocument();
    });
  });

  it("hydrates the active tab from the URL ?tab=components", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValueOnce(overview());
    renderPage("/projects/proj-1?tab=components");
    await waitFor(() => {
      expect(screen.getByTestId("components-tab")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("overview-tab")).not.toBeInTheDocument();
  });

  it("renders the risk gauge in the header when overview is loaded", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValueOnce(overview({ risk_score: 60 }));
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("project-detail-risk-badge"),
      ).toBeInTheDocument();
    });
  });
});
