/**
 * ComponentDrawer — unit tests (PR #10, extended W10-E).
 *
 * W10-E: the drawer now uses `useLocation` / `useNavigate` for the "Open in
 * full view" affordance, so renders are wrapped in `MemoryRouter`. Two new
 * cases cover the affordance:
 *   - hidden when no `projectId` is supplied (back-compat with historic call
 *     sites that don't have a project context)
 *   - visible + navigates to the dedicated page when both ids are present
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ComponentDetailResponse } from "@/features/projects/api/projectDetailApi";
import { ComponentDrawer } from "@/features/projects/components/ComponentDrawer";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/projectDetailApi", async () => {
  return {
    getProjectOverview: vi.fn(),
    listProjectComponents: vi.fn(),
    getComponent: vi.fn(),
  };
});

import { getComponent } from "@/features/projects/api/projectDetailApi";

const mockedGet = vi.mocked(getComponent);

function detail(
  overrides: Partial<ComponentDetailResponse> = {},
): ComponentDetailResponse {
  return {
    id: "00000000-0000-0000-0000-alpha0000000",
    project_id: "proj-1",
    name: "Alpha",
    version: "1.0.0",
    purl: "pkg:npm/alpha@1.0.0",
    license: "MIT",
    license_category: "allowed",
    severity_max: "low",
    vulnerabilities: [],
    raw_data: { source: "cdxgen" },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    // W2 #31 — required wire fields. Default to graph-less ("—").
    depth: null,
    direct: false,
    dependency_scope: null,
    ...overrides,
  };
}

function renderDrawer(
  componentId: string | null,
  open = true,
  onOpenChange: (open: boolean) => void = () => {},
  options: {
    projectId?: string;
    /** Optional sink that captures the current location for navigation assertions. */
    locationRef?: { current: ReturnType<typeof useLocation> | null };
  } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  function LocationProbe() {
    const loc = useLocation();
    if (options.locationRef) {
      options.locationRef.current = loc;
    }
    return null;
  }

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/proj-1?tab=components"]}>
        <Routes>
          <Route
            path="/projects/:id"
            element={
              <>
                <ComponentDrawer
                  open={open}
                  componentId={componentId}
                  onOpenChange={onOpenChange}
                  projectId={options.projectId}
                />
                <LocationProbe />
              </>
            }
          />
          <Route
            path="/projects/:projectId/components/:componentId"
            element={<LocationProbe />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ComponentDrawer", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when closed (no fetch)", () => {
    renderDrawer("alpha-id", false);
    expect(screen.queryByTestId("component-drawer")).not.toBeInTheDocument();
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it("shows skeleton while the detail is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {})); // never resolves
    renderDrawer("alpha-id");
    expect(screen.getByTestId("component-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("component-drawer-loading")).toBeInTheDocument();
  });

  it("renders the meta panel and an empty vulns list", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("alpha-id");
    await waitFor(() => {
      expect(screen.getByTestId("component-drawer-meta")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("component-drawer-vulns").textContent,
    ).toContain("No known vulnerabilities");
    expect(
      screen.getByTestId("component-drawer-purl").textContent,
    ).toContain("pkg:npm/alpha");
  });

  it("renders one item per vulnerability", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({
        vulnerabilities: [
          {
            cve_id: "CVE-2024-1234",
            severity: "critical",
            cvss: 9.8,
            epss_score: 0.973,
            epss_percentile: 0.91,
            title: "RCE in alpha",
            description: "details",
            fixed_version: "1.0.1",
          },
          {
            cve_id: "GHSA-aaaa-bbbb-cccc",
            severity: "medium",
            cvss: 5.5,
            epss_score: null,
            epss_percentile: null,
            title: "Info leak",
            description: null,
            fixed_version: null,
          },
        ],
      }),
    );
    renderDrawer("alpha-id");
    await waitFor(() => {
      expect(screen.getAllByTestId("component-drawer-vuln")).toHaveLength(2);
    });
    expect(
      screen.getByText("CVE-2024-1234"),
    ).toBeInTheDocument();
    expect(screen.getByText("RCE in alpha")).toBeInTheDocument();
  });

  it("renders the RFC 7807 detail in an alert on error", async () => {
    mockedGet.mockRejectedValueOnce(
      new ProblemError("not found", {
        status: 404,
        title: "NotFound",
        detail: "Component not in latest scan.",
        problem: null,
      }),
    );
    renderDrawer("alpha-id");
    await waitFor(() => {
      expect(screen.getByTestId("component-drawer-error")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("component-drawer-error").textContent,
    ).toContain("Component not in latest scan.");
  });

  // W2 #31 — Type/Usage meta rows.

  it("renders a Direct + Required pair on the meta panel", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({ direct: true, depth: 1, dependency_scope: "required" }),
    );
    renderDrawer("alpha-id");
    const typeRow = await screen.findByTestId("component-drawer-dependency-type");
    const usageRow = await screen.findByTestId("component-drawer-usage");
    const typeBadge = typeRow.querySelector("[data-testid='dependency-type-badge']");
    const scopeBadge = usageRow.querySelector("[data-testid='dependency-scope-badge']");
    expect(typeBadge).toHaveAttribute("data-dependency-type", "direct");
    expect(typeBadge).toHaveAttribute("data-depth", "1");
    expect(scopeBadge).toHaveAttribute("data-dependency-scope", "required");
  });

  it("renders '—' badges when depth and scope are null", async () => {
    mockedGet.mockResolvedValueOnce(
      detail({ direct: false, depth: null, dependency_scope: null }),
    );
    renderDrawer("alpha-id");
    const typeRow = await screen.findByTestId("component-drawer-dependency-type");
    const usageRow = await screen.findByTestId("component-drawer-usage");
    expect(
      typeRow.querySelector("[data-dependency-type='unknown']"),
    ).toBeInTheDocument();
    expect(
      usageRow.querySelector("[data-dependency-scope='unknown']"),
    ).toBeInTheDocument();
  });

  it("toggles the raw_data accordion on demand", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("alpha-id");
    await waitFor(() => {
      expect(screen.getByTestId("component-drawer-raw")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("component-drawer-raw-json"),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("component-drawer-raw-toggle"));
    expect(
      screen.getByTestId("component-drawer-raw-json"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("component-drawer-raw-json").textContent,
    ).toContain("cdxgen");
  });

  // ─── W10-E — drawer → page bi-directional affordance ──────────────────

  it("'Open in full view' button is hidden when no projectId prop is supplied", async () => {
    // Historic call sites that don't pass `projectId` should keep working
    // without surfacing a dead affordance (the destination URL can't be built
    // without it).
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("alpha-id");
    await waitFor(() => {
      expect(screen.getByTestId("component-drawer-meta")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("component-drawer-open-full"),
    ).not.toBeInTheDocument();
  });

  it("'Open in full view' button renders with EN label + aria-label when projectId is supplied", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    renderDrawer("alpha-id", true, () => {}, { projectId: "proj-1" });
    const button = await screen.findByTestId("component-drawer-open-full");
    expect(button.textContent).toContain("Open in full view");
    expect(button.getAttribute("aria-label")).toBe("Open in full view");
  });

  it("clicking 'Open in full view' closes drawer + navigates to the detail page with location.state.from set", async () => {
    mockedGet.mockResolvedValueOnce(detail());
    const onOpenChange = vi.fn();
    const locationRef: { current: ReturnType<typeof useLocation> | null } = {
      current: null,
    };

    renderDrawer("alpha-id", true, onOpenChange, {
      projectId: "proj-1",
      locationRef,
    });

    const button = await screen.findByTestId("component-drawer-open-full");
    await userEvent.click(button);

    // The drawer asks the parent to close itself first — preserves the
    // existing behavior that clearing `?drawer=` is the parent's job.
    expect(onOpenChange).toHaveBeenCalledWith(false);

    // Then the router lands on the dedicated detail page.
    expect(locationRef.current?.pathname).toBe(
      "/projects/proj-1/components/alpha-id",
    );
    // And `state.from` carries the *originating* drawer URL (full query
    // string included) so the page's back-link returns to the same list.
    expect(locationRef.current?.state).toEqual({
      from: "/projects/proj-1?tab=components",
    });
  });

  it("'Open in full view' is hidden until the component id is known", async () => {
    // Guard: a drawer mounted with `componentId={null}` (e.g. transient state
    // while the URL flips) must not surface the button — the destination URL
    // would be incomplete.
    renderDrawer(null, true, () => {}, { projectId: "proj-1" });
    expect(
      screen.queryByTestId("component-drawer-open-full"),
    ).not.toBeInTheDocument();
  });
});
