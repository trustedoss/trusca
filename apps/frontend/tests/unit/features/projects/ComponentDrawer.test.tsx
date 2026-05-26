/**
 * ComponentDrawer — unit tests (PR #10).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ComponentDrawer
        open={open}
        componentId={componentId}
        onOpenChange={onOpenChange}
      />
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
});
