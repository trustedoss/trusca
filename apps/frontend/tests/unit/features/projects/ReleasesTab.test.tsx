/**
 * ReleasesTab — unit tests (feature #28 Phase 1, release snapshot viewing).
 *
 * Validates: rows render from mocked data (label / date / severity / gate),
 * the release-label fallback when `release` is null, the empty state, the
 * error alert, and that "View snapshot" invokes the parent callback with the
 * row's scan id.
 *
 * We mock the wire layer so the component renders without a backend — mirrors
 * LicensesTab.test.tsx / OverviewTab.test.tsx mocking style.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ReleaseListResponse,
  ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";
import { ReleasesTab } from "@/features/projects/components/ReleasesTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/releasesApi", async () => {
  return {
    listProjectReleases: vi.fn(),
  };
});

import { listProjectReleases } from "@/features/projects/api/releasesApi";

const mockedList = vi.mocked(listProjectReleases);

function snapshot(
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

function listResponse(
  items: ReleaseSnapshot[],
  total = items.length,
): ReleaseListResponse {
  return { items, total, page: 1, size: 50 };
}

function renderTab(onViewSnapshot = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReleasesTab projectId="proj-1" onViewSnapshot={onViewSnapshot} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, onViewSnapshot };
}

describe("ReleasesTab", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while loading", () => {
    mockedList.mockReturnValue(new Promise(() => {})); // never resolves
    renderTab();
    expect(screen.getByTestId("releases-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no successful scans exist", async () => {
    mockedList.mockResolvedValueOnce(listResponse([]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("releases-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("releases-empty").textContent).toContain(
      "No successful scans yet",
    );
  });

  it("renders rows with label, severity counts and gate from mocked data", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        snapshot("scan-aaa", {
          release: "v1.2.3",
          risk_score: 80,
          severity_summary: { critical: 10, high: 3, medium: 0, low: 5 },
          gate_status: "fail",
        }),
        snapshot("scan-bbb", {
          release: "v1.2.2",
          risk_score: 10,
          severity_summary: { critical: 0, high: 0, medium: 0, low: 0 },
          gate_status: "pass",
        }),
      ]),
    );
    renderTab();

    await waitFor(() => {
      expect(screen.getAllByTestId("release-row")).toHaveLength(2);
    });

    // Release label rendered.
    const labels = screen
      .getAllByTestId("release-row-label")
      .map((el) => el.textContent);
    expect(labels).toEqual(expect.arrayContaining(["v1.2.3", "v1.2.2"]));

    // Severity counts: critical 10 + high 3 + low 5 surfaced; zero buckets omitted.
    const firstRow = screen.getAllByTestId("release-row")[0];
    expect(firstRow.querySelector('[data-testid="release-severity-critical"]')?.textContent).toContain("10");
    expect(firstRow.querySelector('[data-testid="release-severity-high"]')?.textContent).toContain("3");
    expect(
      firstRow.querySelector('[data-testid="release-severity-medium"]'),
    ).toBeNull();

    // Gate badges paired with a label (not color-only).
    expect(screen.getByTestId("release-gate-fail").textContent).toContain(
      "Fail",
    );
    expect(screen.getByTestId("release-gate-pass").textContent).toContain(
      "Pass",
    );
  });

  it("falls back to the date when the release label is null", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        snapshot("scan-ccc", {
          release: null,
          created_at: "2026-05-22T10:00:00Z",
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("release-row-label")).toBeInTheDocument();
    });
    // Not the em-dash and not blank — a formatted date string with the year.
    const label = screen.getByTestId("release-row-label").textContent ?? "";
    expect(label).not.toBe("");
    expect(label).not.toBe("—");
    expect(label).toContain("2026");
  });

  it("renders gate em-dash when gate_status is null", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([snapshot("scan-ddd", { gate_status: null })]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("release-gate-none")).toBeInTheDocument();
    });
  });

  it("renders the risk score (and em-dash when null) with a 'no findings' chip", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        // risk 0 (info tone) + all-zero severity → "no findings" chip.
        snapshot("scan-zero", {
          risk_score: 0,
          severity_summary: { critical: 0, high: 0, medium: 0, low: 0 },
        }),
        // risk null → em-dash; moderate severity exercises medium/low tones.
        snapshot("scan-null", {
          risk_score: null,
          severity_summary: { critical: 0, high: 0, medium: 4, low: 2 },
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("release-row")).toHaveLength(2);
    });
    const risks = screen
      .getAllByTestId("release-row-risk")
      .map((el) => el.textContent);
    expect(risks).toEqual(expect.arrayContaining(["0", "—"]));
    // All-zero severity surfaces the explicit "no findings" chip (not blank).
    expect(screen.getByTestId("release-row-severity-none")).toBeInTheDocument();
    // Medium / low buckets surface for the second row.
    expect(screen.getByTestId("release-severity-medium").textContent).toContain(
      "4",
    );
    expect(screen.getByTestId("release-severity-low").textContent).toContain(
      "2",
    );
  });

  it("clicking 'View snapshot' invokes onViewSnapshot with the row scan id", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([snapshot("scan-eee", { release: "v2.0.0" })]),
    );
    const { onViewSnapshot } = renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("release-row-view")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("release-row-view"));
    expect(onViewSnapshot).toHaveBeenCalledWith("scan-eee");
  });

  it("renders the RFC 7807 detail in an alert on error", async () => {
    mockedList.mockRejectedValueOnce(
      new ProblemError("forbidden", {
        status: 403,
        title: "Forbidden",
        detail: "Release access denied — surfaced verbatim.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("releases-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("releases-error").textContent).toContain(
      "Release access denied — surfaced verbatim.",
    );
  });
});
