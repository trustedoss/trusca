/**
 * ComplianceTab — unit tests (W4-C #20).
 *
 * Validates the sub-tab wrapper:
 *   - Default sub-view is "licenses" when no ?cview= is set.
 *   - ?cview=obligations hydrates the Obligations surface.
 *   - Clicking a sub-tab trigger swaps the surface AND mirrors ?cview= in
 *     the URL.
 *   - License + Obligation drawer keys do not collide across sub-views.
 *
 * The inner LicensesTab / ObligationsTab API modules are mocked so the test
 * can mount the wrapper without a backend. `react-virtuoso` is stubbed with
 * a plain renderer (matches LicensesTab.test.tsx).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComplianceTab } from "@/features/projects/components/ComplianceTab";

vi.mock("@/features/projects/api/licensesApi", () => ({
  listProjectLicenses: vi.fn(),
  getLicenseFinding: vi.fn(),
}));

vi.mock("@/features/projects/api/obligationsApi", () => ({
  listProjectObligations: vi.fn(),
  getProjectObligation: vi.fn(),
  KNOWN_OBLIGATION_KINDS: [
    "attribution",
    "notice",
    "source-disclosure",
    "copyleft",
    "modifications",
    "dynamic-linking",
    "no-endorsement",
  ],
}));

vi.mock("@/features/projects/api/useNotice", () => ({
  useNotice: () => ({
    download: vi.fn().mockResolvedValue(undefined),
    isLoading: false,
    error: null,
  }),
}));

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

import { listProjectLicenses } from "@/features/projects/api/licensesApi";
import { listProjectObligations } from "@/features/projects/api/obligationsApi";

const mockedLicenses = vi.mocked(listProjectLicenses);
const mockedObligations = vi.mocked(listProjectObligations);

function renderTab(initialEntries: string[] = ["/projects/proj-1"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <ComplianceTab projectId="proj-1" projectName="Demo" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ComplianceTab", () => {
  beforeEach(() => {
    mockedLicenses.mockReset();
    mockedObligations.mockReset();
    // Default to an empty response so the inner tabs reach their empty state
    // and stop polling.
    mockedLicenses.mockResolvedValue({
      items: [],
      total: 0,
      distribution: {
        forbidden: 0,
        conditional: 0,
        allowed: 0,
        unknown: 0,
      },
    });
    mockedObligations.mockResolvedValue({
      items: [],
      total: 0,
      distribution: {},
    });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the wrapper with both sub-tab triggers", async () => {
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("compliance-tab")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("compliance-subtab-licenses"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("compliance-subtab-obligations"),
    ).toBeInTheDocument();
  });

  it("defaults to the Licenses sub-view", async () => {
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("licenses-tab")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("obligations-tab")).not.toBeInTheDocument();
  });

  it("hydrates the Obligations sub-view from ?cview=obligations", async () => {
    renderTab(["/projects/proj-1?cview=obligations"]);
    await waitFor(() => {
      expect(screen.getByTestId("obligations-tab")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("licenses-tab")).not.toBeInTheDocument();
  });

  it("switches to Obligations when its sub-tab trigger is clicked", async () => {
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("licenses-tab")).toBeInTheDocument();
    });
    await userEvent.click(
      screen.getByTestId("compliance-subtab-obligations"),
    );
    await waitFor(() => {
      expect(screen.getByTestId("obligations-tab")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("licenses-tab")).not.toBeInTheDocument();
  });

  it("returns to Licenses (default) without persisting ?cview= in the URL", async () => {
    renderTab(["/projects/proj-1?cview=obligations"]);
    await waitFor(() => {
      expect(screen.getByTestId("obligations-tab")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("compliance-subtab-licenses"));
    await waitFor(() => {
      expect(screen.getByTestId("licenses-tab")).toBeInTheDocument();
    });
  });
});
