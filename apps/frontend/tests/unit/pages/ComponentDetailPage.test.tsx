/**
 * ComponentDetailPage — unit tests (W10-E).
 *
 * Covers the full-page surface added in W10-E (route
 * `/projects/:projectId/components/:componentId`). The shared
 * `ComponentDetailBody` is verified by `ComponentDetailBody.test.tsx`; we
 * focus here on what the page wraps it with — breadcrumb crumbs, "Back to
 * Components" link, loading skeleton, not-found alert, and the W10-E
 * bi-directional affordance (location.state.from handling).
 *
 * The drawer test-suite is intentionally NOT touched, satisfying the W10-E
 * "drawer spec regression 0" requirement.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ComponentDetailResponse } from "@/features/projects/api/projectDetailApi";
import { ComponentDetailPage } from "@/features/projects/pages/ComponentDetailPage";
import type { ProjectPublic } from "@/lib/projectsApi";
import { ProblemError } from "@/lib/problem";

// --- wire layer mocks ------------------------------------------------------

vi.mock("@/lib/projectsApi", () => ({
  getProject: vi.fn(),
}));

vi.mock("@/features/projects/api/projectDetailApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/projectDetailApi")
  >("@/features/projects/api/projectDetailApi");
  return {
    ...actual,
    getComponent: vi.fn(),
  };
});

import { getComponent } from "@/features/projects/api/projectDetailApi";
import { getProject } from "@/lib/projectsApi";

const mockedGetProject = vi.mocked(getProject);
const mockedGetComponent = vi.mocked(getComponent);

// --- fixtures --------------------------------------------------------------

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const COMPONENT_ID = "22222222-2222-2222-2222-222222222222";

function makeProject(overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  return {
    id: PROJECT_ID,
    team_id: "team-1",
    name: "kwg-directory",
    slug: "kwg-directory",
    description: null,
    git_url: null,
    default_branch: "main",
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
    ...overrides,
  };
}

function makeComponent(
  overrides: Partial<ComponentDetailResponse> = {},
): ComponentDetailResponse {
  return {
    id: COMPONENT_ID,
    project_id: PROJECT_ID,
    name: "alpha",
    version: "1.0.0",
    purl: "pkg:npm/alpha@1.0.0",
    license: "MIT",
    license_category: "allowed",
    severity_max: "low",
    vulnerabilities: [],
    obligations: [],
    raw_data: { source: "cdxgen" },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    depth: null,
    direct: false,
    dependency_scope: null,
    eol_state: null,
    eol_product: null,
    eol_cycle: null,
    eol_date: null,
    eol_source: null,
    ...overrides,
  };
}

function renderPage(options?: {
  componentId?: string;
  /**
   * W10-E: when set, the MemoryRouter is primed with `location.state.from`
   * mirroring what the drawer's "Open in full view" hands off. The page's
   * "Back to Components" link should adopt this URL when it points at the
   * same project.
   */
  fromState?: string | null;
}) {
  const componentId = options?.componentId ?? COMPONENT_ID;
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const initialEntry =
    options?.fromState !== undefined
      ? {
          pathname: `/projects/${PROJECT_ID}/components/${componentId}`,
          state:
            options.fromState === null
              ? undefined
              : { from: options.fromState },
        }
      : `/projects/${PROJECT_ID}/components/${componentId}`;
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/projects/:projectId/components/:componentId"
            element={<ComponentDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ComponentDetailPage (W10-E)", () => {
  beforeEach(() => {
    mockedGetProject.mockReset();
    mockedGetComponent.mockReset();
    mockedGetProject.mockResolvedValue(makeProject());
  });

  it("renders the loading skeleton before the component resolves", async () => {
    // Never resolve the component promise — the page should stay in the
    // loading state. The breadcrumb still mounts and the loading title shows
    // so the user has feedback while the detail is in flight.
    mockedGetComponent.mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(
      await screen.findByTestId("component-detail-page-loading"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("component-detail-page-title").textContent,
    ).toContain("Loading component");
    expect(
      screen.getByTestId("component-detail-page-breadcrumb-projects"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("component-detail-page-breadcrumb-components"),
    ).toBeInTheDocument();
  });

  it("renders breadcrumb + body once the component loads", async () => {
    mockedGetComponent.mockResolvedValueOnce(makeComponent());
    renderPage();

    // Body mounts (the shared component used by the drawer too).
    await waitFor(() => {
      expect(screen.getByTestId("component-drawer-meta")).toBeInTheDocument();
    });
    // Title flips from "Loading…" to the component label (`name@version`).
    expect(
      screen.getByTestId("component-detail-page-title").textContent,
    ).toContain("alpha@1.0.0");
    // Breadcrumb shows the label in its trailing position.
    expect(
      screen.getByTestId("component-detail-page-breadcrumb-current")
        .textContent,
    ).toContain("alpha@1.0.0");
    // Project crumb resolves from the summary query.
    await waitFor(() => {
      expect(
        screen.getByTestId("component-detail-page-breadcrumb-project")
          .textContent,
      ).toContain("kwg-directory");
    });
  });

  it("'Back to Components' link targets the project's components tab", async () => {
    mockedGetComponent.mockResolvedValueOnce(makeComponent());
    renderPage();

    const back = await screen.findByTestId("component-detail-page-back-link");
    expect(back.getAttribute("href")).toBe(
      `/projects/${PROJECT_ID}?tab=components`,
    );
    // The back link does NOT carry the drawer id — landing on the page is
    // the explicit alternative to the drawer, not an attempt to re-open it.
    expect(back.getAttribute("href")).not.toContain("drawer=");
  });

  it("renders the 'Component not found' alert on a 404", async () => {
    mockedGetComponent.mockRejectedValueOnce(
      new ProblemError("not_found", {
        status: 404,
        title: "Component Not Found",
        detail: "component gone",
        problem: null,
      }),
    );
    renderPage();

    const alert = await screen.findByTestId("component-detail-page-error");
    expect(alert.textContent).toContain("Component not found");
    // The body should NOT mount when the detail query is in an error state.
    expect(
      screen.queryByTestId("component-drawer-meta"),
    ).not.toBeInTheDocument();
  });

  it("renders the 'Component not found' alert on a 403 too (existence-hide parity)", async () => {
    // Backend may existence-hide a component from another team as a 403 in
    // some contexts. The page treats 404 + 403 identically — both surface
    // "not found." copy so the UI never leaks whether the component exists.
    mockedGetComponent.mockRejectedValueOnce(
      new ProblemError("forbidden", {
        status: 403,
        title: "Forbidden",
        detail: "no access",
        problem: null,
      }),
    );
    renderPage();

    const alert = await screen.findByTestId("component-detail-page-error");
    expect(alert.textContent).toContain("Component not found");
  });

  // ─── W10-E — bi-directional affordance: page reads location.state.from ──

  it("'Back to Components' uses location.state.from when it targets the same project", async () => {
    // Simulates the drawer → page transition: the drawer stashes the
    // originating list URL (full query string with filters/pagination) in
    // `state.from`. The page should adopt that URL so the user lands back on
    // the exact view they came from.
    mockedGetComponent.mockResolvedValueOnce(makeComponent());
    renderPage({
      fromState: `/projects/${PROJECT_ID}?tab=components&severity=critical&page=2`,
    });

    const back = await screen.findByTestId("component-detail-page-back-link");
    expect(back.getAttribute("href")).toBe(
      `/projects/${PROJECT_ID}?tab=components&severity=critical&page=2`,
    );
  });

  it("'Back to Components' falls back to the default URL when no state.from is supplied", async () => {
    // Direct page visit (e.g. shared link) — no router state. The default
    // backlink points at the components tab without any preserved filter
    // state.
    mockedGetComponent.mockResolvedValueOnce(makeComponent());
    renderPage({ fromState: null });

    const back = await screen.findByTestId("component-detail-page-back-link");
    expect(back.getAttribute("href")).toBe(
      `/projects/${PROJECT_ID}?tab=components`,
    );
  });

  it("'Back to Components' rejects a cross-project state.from and falls back to the default", async () => {
    // Defensive: if some caller passes a `from` that points outside the
    // current project, we silently fall back to the default link rather
    // than redirecting the user out of the project they're viewing.
    mockedGetComponent.mockResolvedValueOnce(makeComponent());
    renderPage({
      fromState: `/projects/some-other-project?tab=components`,
    });

    const back = await screen.findByTestId("component-detail-page-back-link");
    expect(back.getAttribute("href")).toBe(
      `/projects/${PROJECT_ID}?tab=components`,
    );
  });

  it("'Back to Components' rejects a protocol-relative state.from (open-redirect guard)", async () => {
    // Defensive: `//evil.example` would be interpreted by the browser as an
    // external host. We treat the prefix `//` as untrusted and fall back.
    mockedGetComponent.mockResolvedValueOnce(makeComponent());
    renderPage({ fromState: "//evil.example/projects/" + PROJECT_ID });

    const back = await screen.findByTestId("component-detail-page-back-link");
    expect(back.getAttribute("href")).toBe(
      `/projects/${PROJECT_ID}?tab=components`,
    );
  });
});
