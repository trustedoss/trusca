/**
 * ProjectDetailPage — sandbox demo wiring (feat/demo-sandbox-scan).
 *
 * When the read-only demo enables the sandbox carve-out
 * (`/health.demo_sandbox_scans`), the seeded "Demo Sandbox" project — and ONLY
 * that project — re-opens its two writes: the Scan button and an SBOM upload
 * entry point, fronted by an explainer that points larger projects at BomLens.
 * Every other project / write stays read-only. These tests drive the header +
 * notice; heavy children are stubbed (mirrors ProjectDetailPage.test.tsx).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectPublic } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/projectsApi")>()),
  getProject: vi.fn(),
  ingestSbom: vi.fn(),
}));

vi.mock("@/features/projects/api/useProjectOverview", () => ({
  useProjectOverview: vi.fn(() => ({
    data: { recent_scans: [], current_user_role: "developer" },
    isLoading: false,
    isError: false,
  })),
}));

vi.mock("@/features/projects/api/useLatestRelease", () => ({
  useLatestRelease: vi.fn(() => ({ data: null })),
}));

vi.mock("@/features/projects/api/useReleases", () => ({
  useReleases: vi.fn(() => ({ data: { items: [] } })),
}));

const demoState = {
  demoReadOnly: false,
  demoSandboxScans: false,
  isResolving: false,
};

vi.mock("@/hooks/useDemoMode", () => ({
  useDemoMode: vi.fn(() => demoState),
}));

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
  ScanProgress: () => <div data-testid="scan-progress-mock" />,
}));

import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import { getProject } from "@/lib/projectsApi";

const mockedGetProject = vi.mocked(getProject);
const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

function makeProject(name: string): ProjectPublic {
  return {
    id: PROJECT_ID,
    team_id: "team-1",
    name,
    slug: "p",
    description: null,
    git_url: null,
    default_branch: "main",
    declared_license: null,
    ai_usage_context: null,
    business_unit: null,
    owner_contact: null,
    distribution_model: null,
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    latest_scan_status: null,
    severity_summary: null,
    license_category_summary: null,
    created_by_user_name: null,
    has_git_credential: false,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };
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

describe("ProjectDetailPage sandbox demo wiring", () => {
  beforeEach(() => {
    mockedGetProject.mockReset();
    demoState.demoReadOnly = false;
    demoState.demoSandboxScans = false;
  });

  it("shows the sandbox notice + enables Scan on the Demo Sandbox project when the carve-out is on", async () => {
    demoState.demoReadOnly = true;
    demoState.demoSandboxScans = true;
    mockedGetProject.mockResolvedValue(makeProject("Demo Sandbox"));
    renderPage();

    const notice = await screen.findByTestId("sandbox-scan-notice");
    // Guidance copy: live cap + BomLens link + upload entry point.
    expect(notice).toHaveTextContent(/BomLens/);
    expect(screen.getByTestId("sandbox-bomlens-link")).toBeInTheDocument();
    expect(screen.getByTestId("sandbox-upload-sbom")).toBeInTheDocument();
    // Scan is re-enabled for the sandbox project.
    expect(screen.getByTestId("project-detail-scan")).toBeEnabled();
  });

  it("hides the notice and keeps Scan disabled when the carve-out is OFF (read-only regression)", async () => {
    demoState.demoReadOnly = true;
    demoState.demoSandboxScans = false;
    mockedGetProject.mockResolvedValue(makeProject("Demo Sandbox"));
    renderPage();

    await screen.findByTestId("project-detail-scan");
    expect(screen.queryByTestId("sandbox-scan-notice")).not.toBeInTheDocument();
    expect(screen.getByTestId("project-detail-scan")).toBeDisabled();
  });

  it("does not re-enable writes on a NON-sandbox project even with the carve-out on", async () => {
    demoState.demoReadOnly = true;
    demoState.demoSandboxScans = true;
    mockedGetProject.mockResolvedValue(makeProject("some-other-project"));
    renderPage();

    await screen.findByTestId("project-detail-scan");
    expect(screen.queryByTestId("sandbox-scan-notice")).not.toBeInTheDocument();
    expect(screen.getByTestId("project-detail-scan")).toBeDisabled();
  });

  it("opens the SBOM ingest dialog from the upload entry point", async () => {
    demoState.demoReadOnly = true;
    demoState.demoSandboxScans = true;
    mockedGetProject.mockResolvedValue(makeProject("Demo Sandbox"));
    renderPage();

    await userEvent.click(await screen.findByTestId("sandbox-upload-sbom"));
    await waitFor(() =>
      expect(screen.getByTestId("sbom-ingest-dialog")).toBeInTheDocument(),
    );
  });

  it("renders no sandbox notice on a normal (non-demo) deploy", async () => {
    mockedGetProject.mockResolvedValue(makeProject("Demo Sandbox"));
    renderPage();

    await screen.findByTestId("project-detail-scan");
    expect(screen.queryByTestId("sandbox-scan-notice")).not.toBeInTheDocument();
    // Writes are open normally.
    expect(screen.getByTestId("project-detail-scan")).toBeEnabled();
  });
});
