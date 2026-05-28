/**
 * ComparePage — unit tests (feature #28 Phase 2, release compare view).
 *
 * Validates the diff screen end to end against a mocked wire layer:
 *   - the summary delta strip renders base→target for risk / severity / gate /
 *     component count,
 *   - the Components section groups added / removed / changed,
 *   - the Vulnerabilities section groups introduced / resolved,
 *   - the Licenses delta table renders per category and flags a newly
 *     introduced prohibited license,
 *   - changing the Base selector rewrites `?base=` (and refetches),
 *   - the empty-diff state renders when there are no differences,
 *   - the truncated hint renders when `truncated` is true,
 *   - the Releases-tab Compare button is disabled with a single release.
 *
 * Mocks the diff + releases wire functions (mirrors ReleasesTab.test.tsx /
 * ProjectDetailPage.test.tsx style). Radix DropdownMenu needs a few DOM APIs
 * jsdom omits — polyfilled below.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useSearchParams,
} from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectDiff } from "@/features/projects/api/diffApi";
import type {
  ReleaseListResponse,
  ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";
import { ComparePage } from "@/features/projects/ComparePage";
import { ReleasesTab } from "@/features/projects/components/ReleasesTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/diffApi", async () => {
  return { getProjectDiff: vi.fn() };
});
vi.mock("@/features/projects/api/releasesApi", async () => {
  return { listProjectReleases: vi.fn() };
});

import { getProjectDiff } from "@/features/projects/api/diffApi";
import { listProjectReleases } from "@/features/projects/api/releasesApi";

const mockedDiff = vi.mocked(getProjectDiff);
const mockedReleases = vi.mocked(listProjectReleases);

beforeAll(() => {
  // Radix DropdownMenu uses these DOM APIs that jsdom does not implement.
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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function snapshot(
  scanId: string,
  overrides: Partial<ReleaseSnapshot> = {},
): ReleaseSnapshot {
  return {
    scan_id: scanId,
    release: null,
    created_at: "2026-05-22T10:00:00Z",
    risk_score: 50,
    severity_summary: { critical: 0, high: 0, medium: 0, low: 0 },
    gate_status: "pass",
    component_count: 10,
    ...overrides,
  };
}

function releaseList(
  items: ReleaseSnapshot[],
  total = items.length,
): ReleaseListResponse {
  return { items, total, page: 1, size: 50 };
}

const THREE_RELEASES = releaseList([
  snapshot("scan-c", { release: "v0.3" }),
  snapshot("scan-b", { release: "v0.2" }),
  snapshot("scan-a", { release: "v0.1" }),
]);

function diff(overrides: Partial<ProjectDiff> = {}): ProjectDiff {
  return {
    base: { scan_id: "scan-a", release: "v0.1", created_at: "2026-05-20T00:00:00Z" },
    target: { scan_id: "scan-b", release: "v0.2", created_at: "2026-05-22T00:00:00Z" },
    summary: {
      risk_score: { base: 20, target: 80 },
      severity: {
        critical: { base: 0, target: 10 },
        high: { base: 1, target: 3 },
        medium: { base: 2, target: 2 },
        low: { base: 5, target: 1 },
      },
      gate: { base: "pass", target: "fail" },
      component_count: { base: 40, target: 88 },
    },
    components: {
      added: [
        { name: "left-pad", namespace: null, purl: "pkg:npm/left-pad@1.3.0", version: "1.3.0" },
      ],
      removed: [
        { name: "log4j", namespace: "org.apache", purl: "pkg:maven/org.apache/log4j@1.2.17", version: "1.2.17" },
      ],
      changed: [
        {
          name: "lodash",
          namespace: null,
          purl: "pkg:npm/lodash",
          base_version: "4.17.20",
          target_version: "4.17.21",
        },
      ],
    },
    vulnerabilities: {
      introduced: [
        { cve_id: "CVE-2024-0001", severity: "critical", component_name: "left-pad", component_version: "1.3.0" },
      ],
      resolved: [
        { cve_id: "CVE-2021-44228", severity: "critical", component_name: "log4j", component_version: "1.2.17" },
      ],
    },
    licenses: {
      category_delta: {
        prohibited: { base: 0, target: 2 },
        conditional: { base: 1, target: 1 },
        permissive: { base: 30, target: 60 },
        unknown: { base: 0, target: 0 },
      },
    },
    truncated: false,
    ...overrides,
  };
}

/** An all-empty diff — base === target / no differences. */
function emptyDiff(): ProjectDiff {
  return diff({
    summary: {
      risk_score: { base: 50, target: 50 },
      severity: {
        critical: { base: 0, target: 0 },
        high: { base: 0, target: 0 },
        medium: { base: 0, target: 0 },
        low: { base: 0, target: 0 },
      },
      gate: { base: "pass", target: "pass" },
      component_count: { base: 10, target: 10 },
    },
    components: { added: [], removed: [], changed: [] },
    vulnerabilities: { introduced: [], resolved: [] },
    licenses: {
      category_delta: {
        prohibited: { base: 0, target: 0 },
        conditional: { base: 0, target: 0 },
        permissive: { base: 10, target: 10 },
        unknown: { base: 0, target: 0 },
      },
    },
    truncated: false,
  });
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function SearchProbe() {
  const [params] = useSearchParams();
  return (
    <span
      data-testid="search-probe"
      data-base={params.get("base") ?? ""}
      data-target={params.get("target") ?? ""}
    />
  );
}

function renderCompare(initialPath: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/projects/:id/compare"
            element={
              <>
                <ComparePage />
                <SearchProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderReleasesTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReleasesTab projectId="proj-1" onViewSnapshot={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const COMPARE_PATH = "/projects/proj-1/compare?base=scan-a&target=scan-b";

describe("ComparePage", () => {
  beforeEach(() => {
    mockedDiff.mockReset();
    mockedReleases.mockReset();
    mockedReleases.mockResolvedValue(THREE_RELEASES);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the summary delta strip (risk / severity / gate / components)", async () => {
    mockedDiff.mockResolvedValue(diff());
    renderCompare(COMPARE_PATH);

    await waitFor(() => {
      expect(screen.getByTestId("compare-summary")).toBeInTheDocument();
    });

    // Risk score 20 → 80, an increase (worse) — direction marker present.
    const risk = screen.getByTestId("compare-risk-delta");
    expect(risk.textContent).toContain("20");
    expect(risk.textContent).toContain("80");
    expect(risk).toHaveAttribute("data-delta", "60");

    // Severity critical 0 → 10.
    const crit = screen.getByTestId("compare-severity-critical-delta");
    expect(crit.textContent).toContain("0");
    expect(crit.textContent).toContain("10");
    expect(crit).toHaveAttribute("data-delta", "10");

    // Gate pass → fail, both pills rendered with their labels (not color-only).
    expect(screen.getByTestId("compare-gate-base")).toHaveAttribute("data-gate", "pass");
    expect(screen.getByTestId("compare-gate-target")).toHaveAttribute("data-gate", "fail");
    expect(screen.getByTestId("compare-gate-base").textContent).toContain("Pass");
    expect(screen.getByTestId("compare-gate-target").textContent).toContain("Fail");

    // Component count 40 → 88.
    const comps = screen.getByTestId("compare-components-delta");
    expect(comps.textContent).toContain("40");
    expect(comps.textContent).toContain("88");
  });

  it("renders component added / removed / changed groups from the diff", async () => {
    mockedDiff.mockResolvedValue(diff());
    renderCompare(COMPARE_PATH);

    await waitFor(() => {
      expect(screen.getByTestId("compare-components")).toBeInTheDocument();
    });

    const removed = screen.getByTestId("compare-components-removed");
    expect(removed).toHaveAttribute("data-count", "1");
    // namespace is prefixed onto the name.
    expect(within(removed).getByTestId("compare-components-removed-row").textContent).toContain(
      "org.apache/log4j",
    );

    const added = screen.getByTestId("compare-components-added");
    expect(added).toHaveAttribute("data-count", "1");
    expect(within(added).getByTestId("compare-components-added-row").textContent).toContain(
      "left-pad",
    );

    const changed = screen.getByTestId("compare-components-changed");
    expect(changed).toHaveAttribute("data-count", "1");
    const changedRow = within(changed).getByTestId("compare-components-changed-row");
    expect(changedRow.textContent).toContain("lodash");
    expect(changedRow.textContent).toContain("4.17.20");
    expect(changedRow.textContent).toContain("4.17.21");
  });

  it("renders introduced / resolved vulnerabilities and a license delta with a 'New' prohibited flag", async () => {
    mockedDiff.mockResolvedValue(diff());
    renderCompare(COMPARE_PATH);

    await waitFor(() => {
      expect(screen.getByTestId("compare-vulnerabilities")).toBeInTheDocument();
    });

    const introduced = screen.getByTestId("compare-vulns-introduced");
    expect(introduced).toHaveAttribute("data-count", "1");
    expect(within(introduced).getByTestId("compare-vulns-introduced-row").textContent).toContain(
      "CVE-2024-0001",
    );

    const resolved = screen.getByTestId("compare-vulns-resolved");
    expect(resolved).toHaveAttribute("data-count", "1");
    expect(within(resolved).getByTestId("compare-vulns-resolved-row").textContent).toContain(
      "CVE-2021-44228",
    );

    // License delta: prohibited goes 0 → 2, so the "introduced" flag fires.
    const prohibited = screen.getByTestId("compare-license-prohibited");
    expect(prohibited).toHaveAttribute("data-introduced", "true");
    expect(
      within(prohibited).getByTestId("compare-license-prohibited-introduced"),
    ).toBeInTheDocument();
    // conditional 1 → 1 is unchanged → no introduced flag.
    expect(screen.getByTestId("compare-license-conditional")).toHaveAttribute(
      "data-introduced",
      "false",
    );
  });

  it("changing the Base selector rewrites ?base= and refetches", async () => {
    mockedDiff.mockResolvedValue(diff());
    renderCompare(COMPARE_PATH);

    await waitFor(() => {
      expect(screen.getByTestId("compare-summary")).toBeInTheDocument();
    });
    expect(screen.getByTestId("search-probe")).toHaveAttribute("data-base", "scan-a");

    // Open the base selector and pick v0.3 (scan-c).
    await userEvent.click(screen.getByTestId("compare-selector-base"));
    const menu = await screen.findByTestId("compare-selector-base-menu");
    const items = within(menu).getAllByTestId("compare-selector-base-item");
    const v03 = items.find((el) => el.getAttribute("data-scan-id") === "scan-c");
    expect(v03).toBeTruthy();
    await userEvent.click(v03 as HTMLElement);

    await waitFor(() => {
      expect(screen.getByTestId("search-probe")).toHaveAttribute("data-base", "scan-c");
    });
    // Target is preserved.
    expect(screen.getByTestId("search-probe")).toHaveAttribute("data-target", "scan-b");
    // A new base triggers a refetch with the new pair.
    await waitFor(() => {
      expect(mockedDiff).toHaveBeenCalledWith("proj-1", { base: "scan-c", target: "scan-b" });
    });
  });

  it("renders the empty-diff state when there are no differences", async () => {
    mockedDiff.mockResolvedValue(emptyDiff());
    renderCompare("/projects/proj-1/compare?base=scan-a&target=scan-a");

    await waitFor(() => {
      expect(screen.getByTestId("compare-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("compare-empty").textContent).toContain(
      "No differences between these versions.",
    );
    // The component / vuln sections are suppressed when there are no diffs.
    expect(screen.queryByTestId("compare-components")).not.toBeInTheDocument();
    expect(screen.queryByTestId("compare-vulnerabilities")).not.toBeInTheDocument();
    // The license table still renders (deltas can be all-zero but informative).
    expect(screen.getByTestId("compare-licenses")).toBeInTheDocument();
  });

  it("shows the truncated hint when the diff is truncated", async () => {
    mockedDiff.mockResolvedValue(diff({ truncated: true }));
    renderCompare(COMPARE_PATH);

    await waitFor(() => {
      expect(screen.getByTestId("compare-truncated")).toBeInTheDocument();
    });
    expect(screen.getByTestId("compare-truncated").textContent).toContain(
      "capped at 1000 items",
    );
  });

  it("surfaces the localized 404 error when a scan id is invalid", async () => {
    mockedDiff.mockRejectedValue(
      new ProblemError("not found", {
        status: 404,
        title: "Not Found",
        detail: "scan not found",
        problem: null,
      }),
    );
    renderCompare(COMPARE_PATH);

    await waitFor(() => {
      expect(screen.getByTestId("compare-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("compare-error").textContent).toContain(
      "could not be found",
    );
  });

  it("does not fetch and shows a prompt when params are missing", async () => {
    mockedDiff.mockResolvedValue(diff());
    renderCompare("/projects/proj-1/compare");

    await waitFor(() => {
      expect(screen.getByTestId("compare-missing-params")).toBeInTheDocument();
    });
    expect(mockedDiff).not.toHaveBeenCalled();
  });
});

describe("ReleasesTab — Compare entry point", () => {
  beforeEach(() => {
    mockedReleases.mockReset();
  });

  it("disables Compare with a hint when only one release exists", async () => {
    mockedReleases.mockResolvedValue(releaseList([snapshot("scan-only", { release: "v1" })]));
    renderReleasesTab();

    await waitFor(() => {
      expect(screen.getByTestId("releases-compare-button")).toBeInTheDocument();
    });
    const button = screen.getByTestId("releases-compare-button");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Need at least two successful scans");
  });

  it("enables Compare when at least two releases exist", async () => {
    mockedReleases.mockResolvedValue(THREE_RELEASES);
    renderReleasesTab();

    await waitFor(() => {
      expect(screen.getByTestId("releases-compare-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("releases-compare-button")).not.toBeDisabled();
  });
});
