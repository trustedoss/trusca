/**
 * ProjectDetailPage — unit tests for the persistent "active scan" chip (#29).
 *
 * The reported bug: triggering a scan, then closing the progress drawer, left
 * the user with no way back to the running scan (recent_scans came back empty
 * from the backend; even once populated there was no always-visible affordance
 * in the header). The backend half is guarded by an integration test; here we
 * guard the FRONTEND half — given an overview whose recent_scans carries a
 * queued/running scan, the header renders a clickable chip that re-opens the
 * live progress drawer for that scan.
 *
 * We mock the wire/hook layer and stub the heavy children (tabs, dialogs,
 * ScanProgress, ReleaseSwitcher) so the test focuses on the header behavior —
 * mirroring the ProjectListPage.test.tsx approach.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import type { ScanSummary } from "@/features/projects/api/projectDetailApi";
import type { ProjectPublic } from "@/lib/projectsApi";

// --- wire + hooks --------------------------------------------------------

vi.mock("@/lib/projectsApi", () => ({
  getProject: vi.fn(),
}));

vi.mock("@/features/projects/api/useProjectOverview", () => ({
  useProjectOverview: vi.fn(),
}));

vi.mock("@/features/projects/api/useLatestRelease", () => ({
  useLatestRelease: vi.fn(() => ({ data: null })),
}));

vi.mock("@/features/projects/api/useReleases", () => ({
  useReleases: vi.fn(() => ({ data: { items: [] } })),
}));

vi.mock("@/hooks/useDemoMode", () => ({
  useDemoMode: vi.fn(() => ({ demoReadOnly: false })),
}));

// --- heavy children stubbed to keep the test on the header ---------------

vi.mock("@/features/projects/components/OverviewTab", () => ({
  OverviewTab: () => <div data-testid="overview-tab-mock" />,
}));

vi.mock("@/features/projects/components/ReleaseSwitcher", () => ({
  ReleaseSwitcher: () => <div data-testid="release-switcher-mock" />,
}));

vi.mock("@/features/scan/SourceSelectDialog", () => ({
  SourceSelectDialog: () => null,
}));

vi.mock("@/features/scan/ScanProgress", () => ({
  ScanProgress: ({ scanId }: { scanId: string }) => (
    <div data-testid="scan-progress-mock" data-scan-id={scanId} />
  ),
}));

import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { getProject } from "@/lib/projectsApi";

const mockedGetProject = vi.mocked(getProject);
const mockedUseOverview = vi.mocked(useProjectOverview);

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

function makeProject(): ProjectPublic {
  return {
    id: PROJECT_ID,
    team_id: "team-1",
    name: "kwg-directory",
    slug: "kwg-directory",
    description: null,
    git_url: "https://github.com/example/kwg-directory",
    default_branch: "main",
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    latest_scan_status: null,
    severity_summary: null,
    has_git_credential: false,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };
}

function scan(status: string, id: string): ScanSummary {
  return {
    id,
    kind: "source",
    status,
    progress_percent: status === "succeeded" ? 100 : 0,
    started_at: null,
    completed_at: null,
    created_at: "2026-05-26T00:00:00Z",
    release: null,
  };
}

function overviewWith(recent: ScanSummary[]) {
  return {
    data: {
      project_id: PROJECT_ID,
      project_name: "kwg-directory",
      total_components: 0,
      severity_distribution: {},
      license_distribution: {},
      risk_score: 0,
      recent_scans: recent,
      last_scan_at: null,
      last_succeeded_scan_at: null,
      current_user_role: "developer",
      has_git_credential: false,
    },
    isLoading: false,
    isError: false,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/projects/${PROJECT_ID}`]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectDetailPage active-scan chip (#29)", () => {
  beforeEach(() => {
    mockedGetProject.mockReset();
    mockedGetProject.mockResolvedValue(makeProject());
    mockedUseOverview.mockReset();
  });

  it("shows a clickable 'scan running' chip and re-opens the drawer for the in-flight scan", async () => {
    mockedUseOverview.mockReturnValue(overviewWith([scan("running", "scan-run-1")]));
    renderPage();

    const chip = await screen.findByTestId("project-detail-active-scan");
    expect(chip).toHaveAttribute("data-status", "running");
    expect(chip).toHaveAttribute("data-scan-id", "scan-run-1");

    // The drawer is not open until the chip is clicked.
    expect(screen.queryByTestId("scan-progress-mock")).not.toBeInTheDocument();

    await userEvent.click(chip);

    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-mock")).toBeInTheDocument();
    });
    expect(screen.getByTestId("scan-progress-mock")).toHaveAttribute(
      "data-scan-id",
      "scan-run-1",
    );
  });

  it("renders the chip for a queued scan too", async () => {
    mockedUseOverview.mockReturnValue(overviewWith([scan("queued", "scan-q-1")]));
    renderPage();

    const chip = await screen.findByTestId("project-detail-active-scan");
    expect(chip).toHaveAttribute("data-status", "queued");
  });

  it("does NOT render the chip when every recent scan is terminal", async () => {
    mockedUseOverview.mockReturnValue(
      overviewWith([scan("succeeded", "scan-ok-1"), scan("failed", "scan-bad-1")]),
    );
    renderPage();

    // The header (scan button) is present, proving the page rendered…
    await screen.findByTestId("project-detail-scan");
    // …but no active-scan chip, because nothing is queued/running.
    expect(screen.queryByTestId("project-detail-active-scan")).not.toBeInTheDocument();
  });
});
