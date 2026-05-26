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
import {
  MemoryRouter,
  Route,
  Routes,
  useSearchParams,
} from "react-router-dom";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type {
  ProjectOverviewResponse,
} from "@/features/projects/api/projectDetailApi";
import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import { ProblemError } from "@/lib/problem";
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
    getGateResult: vi.fn(),
  };
});

vi.mock("@/features/projects/api/releasesApi", async () => {
  return {
    listProjectReleases: vi.fn(),
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
  getGateResult,
  getProjectOverview,
  listProjectComponents,
} from "@/features/projects/api/projectDetailApi";
import { listProjectReleases } from "@/features/projects/api/releasesApi";
import type { ReleaseSnapshot } from "@/features/projects/api/releasesApi";

const mockedGetProject = vi.mocked(getProject);
const mockedOverview = vi.mocked(getProjectOverview);
const mockedListComponents = vi.mocked(listProjectComponents);
const mockedGateResult = vi.mocked(getGateResult);
const mockedListReleases = vi.mocked(listProjectReleases);

function release(
  scanId: string,
  overrides: Partial<ReleaseSnapshot> = {},
): ReleaseSnapshot {
  return {
    scan_id: scanId,
    release: null,
    created_at: "2026-05-22T10:00:00Z",
    risk_score: 80,
    severity_summary: { critical: 10, high: 0, medium: 0, low: 0 },
    gate_status: "fail",
    component_count: 42,
    ...overrides,
  };
}

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
    latest_scan_status: null,
    severity_summary: null,
    has_git_credential: false,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
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
    risk_score: 80,
    security_score: 80,
    license_score: 80,
    recent_scans: [],
    last_scan_at: null,
    last_succeeded_scan_at: null,
    vuln_data_available: true,
    current_user_role: "developer",
    has_git_credential: false,
    ...overrides,
  };
}

/**
 * Mirrors the live `?scan=` param into a testid so a test can assert the URL
 * the switcher / banner write without reaching into router internals.
 */
function ScanParamProbe() {
  const [params] = useSearchParams();
  return (
    <span data-testid="scan-param-probe">{params.get("scan") ?? ""}</span>
  );
}

function renderPage(initialPath = "/projects/proj-1") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/projects/:id"
            element={
              <>
                <ProjectDetailPage />
                <ScanParamProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectDetailPage", () => {
  beforeAll(() => {
    // Radix DropdownMenu (the header release switcher) needs these DOM APIs
    // that jsdom omits.
    if (!Element.prototype.hasPointerCapture) {
      Element.prototype.hasPointerCapture = () => false;
    }
    if (!Element.prototype.setPointerCapture) {
      Element.prototype.setPointerCapture = () => {};
    }
    if (!Element.prototype.releasePointerCapture) {
      Element.prototype.releasePointerCapture = () => {};
    }
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = () => {};
    }
  });
  beforeEach(() => {
    mockedGetProject.mockReset();
    mockedOverview.mockReset();
    mockedListComponents.mockReset();
    mockedGateResult.mockReset();
    mockedListReleases.mockReset();
    mockedListComponents.mockResolvedValue({
      items: [],
      total: 0,
      limit: 100,
      offset: 0,
    });
    // Default: two releases, newest-first. scan-latest is the live snapshot.
    mockedListReleases.mockResolvedValue({
      items: [
        release("scan-latest", { release: "v2.0.0" }),
        release("scan-old", { release: "v1.0.0" }),
      ],
      total: 2,
      page: 1,
      size: 50,
    });
    mockedGateResult.mockResolvedValue({
      gate: "fail",
      reason: null,
      critical_cve_count: 10,
      forbidden_license_count: 0,
      epss_gate_count: 0,
      epss_threshold: null,
      project_id: "proj-1",
      scan_id: "scan-latest",
      evaluated_at: "2026-05-22T10:00:00Z",
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

  it("shows an 'unavailable' breadcrumb, not a stuck 'Loading…', on a 404 (BUG-004)", async () => {
    mockedGetProject.mockRejectedValueOnce(
      new ProblemError("not_found", {
        status: 404,
        title: "Project Not Found",
        detail: "project proj-1 not found",
        problem: null,
      }),
    );
    mockedOverview.mockRejectedValueOnce(
      new ProblemError("not_found", {
        status: 404,
        title: "Project Not Found",
        detail: "not found",
        problem: null,
      }),
    );
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("project-detail-load-error"),
      ).toBeInTheDocument();
    });
    const crumb = screen.getByTestId("project-detail-breadcrumb-current");
    // The crumb must settle on the error label, not keep the loading placeholder.
    expect(crumb.textContent).toBe("Unavailable");
    expect(crumb.textContent).not.toBe("Loading…");
  });

  it("renders the W4-C 8-tab strip with every trigger enabled", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValueOnce(overview());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("project-detail-tabs")).toBeInTheDocument();
    });
    // W4-C #20/21/22 — 8 tabs total: overview / releases / components /
    // vulnerabilities / source / compliance / reports / settings. The
    // legacy quad (licenses / obligations / sbom / remediation) is absorbed
    // and must not appear as a top-level trigger.
    for (const slug of [
      "overview",
      "releases",
      "components",
      "vulnerabilities",
      "source",
      "compliance",
      "reports",
      "settings",
    ]) {
      expect(
        screen.getByTestId(`project-detail-tab-${slug}`),
      ).toBeEnabled();
    }
    expect(
      screen.queryByTestId("project-detail-tab-licenses"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("project-detail-tab-obligations"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("project-detail-tab-sbom"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("project-detail-tab-remediation"),
    ).not.toBeInTheDocument();
  });

  it("redirects ?tab=licenses to ?tab=compliance&cview=licenses", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?tab=licenses");
    await waitFor(() => {
      expect(screen.getByTestId("compliance-tab")).toBeInTheDocument();
    });
    // The licenses sub-view is the default; URL gets rewritten to canonical.
    expect(screen.getByTestId("licenses-tab")).toBeInTheDocument();
  });

  it("redirects ?tab=obligations to ?tab=compliance&cview=obligations", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?tab=obligations");
    await waitFor(() => {
      expect(screen.getByTestId("compliance-tab")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("obligations-tab")).toBeInTheDocument();
    });
  });

  it("redirects ?tab=sbom to ?tab=reports with the SBOM section anchor", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?tab=sbom");
    await waitFor(() => {
      expect(screen.getByTestId("reports-tab")).toBeInTheDocument();
    });
    expect(screen.getByTestId("reports-sbom-section")).toBeInTheDocument();
  });

  it("redirects ?tab=remediation to ?tab=vulnerabilities with the remediation panel", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?tab=remediation");
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-tab")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("vulnerabilities-remediation-panel"),
    ).toBeInTheDocument();
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

  it("renders the Releases tab trigger and switches to it", async () => {
    mockedGetProject.mockResolvedValueOnce(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("project-detail-tab-releases"),
      ).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("project-detail-tab-releases"));
    await waitFor(() => {
      expect(screen.getByTestId("releases-tab")).toBeInTheDocument();
    });
  });

  it("shows no historical banner when ?scan= equals the latest succeeded id", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?scan=scan-latest");
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("snapshot-banner")).not.toBeInTheDocument();
  });

  it("shows the historical banner when ?scan= is an older snapshot", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?scan=scan-old");
    await waitFor(() => {
      expect(screen.getByTestId("snapshot-banner")).toBeInTheDocument();
    });
    // The banner names the snapshot (release label from the releases list).
    expect(screen.getByTestId("snapshot-banner").textContent).toContain(
      "v1.0.0",
    );
  });

  it("'Back to latest' clears ?scan= and removes the banner", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?scan=scan-old");
    await waitFor(() => {
      expect(screen.getByTestId("snapshot-exit")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("snapshot-exit"));
    await waitFor(() => {
      expect(screen.queryByTestId("snapshot-banner")).not.toBeInTheDocument();
    });
  });

  it("disables vulnerability write controls in historical mode", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?scan=scan-old&tab=vulnerabilities");
    await waitFor(() => {
      expect(screen.getByTestId("snapshot-banner")).toBeInTheDocument();
    });
    // VEX import button on the Vulnerabilities toolbar is read-only-gated.
    await waitFor(() => {
      expect(screen.getByTestId("vex-import-open")).toBeInTheDocument();
    });
    const importBtn = screen.getByTestId("vex-import-open");
    expect(importBtn).toBeDisabled();
    expect(importBtn).toHaveAttribute("data-readonly-gated", "true");
  });

  it("keeps the header Scan button enabled in historical mode", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?scan=scan-old");
    await waitFor(() => {
      expect(screen.getByTestId("snapshot-banner")).toBeInTheDocument();
    });
    // Starting a new scan is always allowed, even when viewing an old snapshot.
    expect(screen.getByTestId("project-detail-scan")).toBeEnabled();
  });

  it("renders the header release switcher with the latest context label", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("release-switcher")).toBeInTheDocument();
    });
    // Live view → "Latest" context, not read-only.
    await waitFor(() => {
      expect(
        screen.getByTestId("release-switcher-label").textContent,
      ).toContain("v2.0.0");
    });
    expect(
      screen.getByTestId("release-switcher-label").textContent,
    ).toContain("Latest");
    expect(screen.getByTestId("release-switcher")).toHaveAttribute(
      "data-historical",
      "false",
    );
  });

  it("selecting a release in the header switcher pins ?scan= and shows the banner", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("release-switcher")).toBeEnabled();
    });
    await userEvent.click(screen.getByTestId("release-switcher"));
    await waitFor(() => {
      expect(screen.getAllByTestId("release-switcher-item").length).toBe(2);
    });
    const old = screen
      .getAllByTestId("release-switcher-item")
      .find((el) => el.getAttribute("data-scan-id") === "scan-old");
    await userEvent.click(old as HTMLElement);

    // URL re-anchored to the older snapshot + the read-only banner appears.
    await waitFor(() => {
      expect(screen.getByTestId("scan-param-probe").textContent).toBe(
        "scan-old",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("snapshot-banner")).toBeInTheDocument();
    });
  });

  it("selecting 'Latest' in the header switcher clears ?scan= and the banner", async () => {
    mockedGetProject.mockResolvedValue(project());
    mockedOverview.mockResolvedValue(overview());
    renderPage("/projects/proj-1?scan=scan-old");
    await waitFor(() => {
      expect(screen.getByTestId("snapshot-banner")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("release-switcher"));
    await waitFor(() => {
      expect(screen.getByTestId("release-switcher-latest")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("release-switcher-latest"));

    await waitFor(() => {
      expect(screen.getByTestId("scan-param-probe").textContent).toBe("");
    });
    await waitFor(() => {
      expect(screen.queryByTestId("snapshot-banner")).not.toBeInTheDocument();
    });
  });
});
