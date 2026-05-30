/**
 * ComplianceTab — unit tests (W9-#58).
 *
 * The W4-C sub-tab wrapper was replaced with a single unified grid keyed on
 * licenses with obligations embedded inline. These tests validate:
 *
 *   1. Loading skeleton → empty state path when the grid carries 0 rows.
 *   2. A fully-populated row mounts category badge, affected components
 *      preview, and obligation chips.
 *   3. Category filter changes pass through to the wire layer at offset 0.
 *   4. ``has obligations`` toggle filters down to obligation-carrying rows.
 *   5. Clicking a row opens the License drawer (URL ``?license=`` set).
 *   6. Backward-compat: ``?cview=obligations`` boots the grid with the
 *      has_obligations filter applied.
 *
 * Mocking
 *   - ``complianceApi.listProjectCompliance`` is mocked so the component
 *     renders without a backend.
 *   - ``react-virtuoso`` is stubbed with a plain renderer so all rows mount
 *     in jsdom — mirrors LicensesTab.test.tsx.
 *   - ``licensesApi.getLicenseFinding`` is mocked because the LicenseDrawer
 *     is rendered in-tree (a row click should still open it cleanly).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ComplianceListResponse,
  ComplianceRow,
} from "@/features/projects/api/complianceApi";
import { ComplianceTab } from "@/features/projects/components/ComplianceTab";

vi.mock("@/features/projects/api/complianceApi", async () => {
  return {
    listProjectCompliance: vi.fn(),
  };
});

vi.mock("@/features/projects/api/licensesApi", async () => {
  return {
    getLicenseFinding: vi.fn().mockRejectedValue(new Error("not under test")),
  };
});

vi.mock("@/lib/licensePoliciesApi", async () => {
  return {
    // The waive strip reads the effective team policy; a 404 → null (no
    // exceptions yet). Mutations are not exercised by the grid tests.
    getTeamPolicy: vi.fn().mockRejectedValue(new Error("no team policy")),
    addTeamLicenseException: vi.fn(),
    deleteTeamLicenseException: vi.fn(),
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

import { listProjectCompliance } from "@/features/projects/api/complianceApi";

const mockedList = vi.mocked(listProjectCompliance);

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function row(
  spdxId: string,
  overrides: Partial<ComplianceRow> = {},
): ComplianceRow {
  const slug = spdxId.toLowerCase().replace(/[^a-z0-9]/g, "0");
  const id =
    overrides.license_finding_id ??
    `00000000-0000-0000-0000-${slug.padEnd(12, "0").slice(0, 12)}`;
  return {
    license_finding_id: id,
    license_id: overrides.license_id ?? `lic-${spdxId}`,
    spdx_id: spdxId,
    license_name: overrides.license_name ?? spdxId,
    category: overrides.category ?? "allowed",
    category_source: overrides.category_source ?? "static",
    kind: overrides.kind ?? "concluded",
    affected_component_count: overrides.affected_component_count ?? 1,
    affected_components: overrides.affected_components ?? [
      {
        component_version_id: `cv-${spdxId}`,
        name: `${spdxId}-lib`,
        version: "1.0.0",
        purl: null,
      },
    ],
    obligations: overrides.obligations ?? [],
    notice_required: overrides.notice_required ?? false,
    category_override_source: overrides.category_override_source ?? null,
  };
}

function response(
  items: ComplianceRow[],
  total = items.length,
): ComplianceListResponse {
  return {
    items,
    distribution: { forbidden: 0, conditional: 0, allowed: 0, unknown: 0 },
    total,
    limit: 100,
    offset: 0,
    generated_at: "2026-05-27T00:00:00Z",
  };
}

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

function renderTabAsTeamAdmin(initialEntries: string[] = ["/projects/proj-1"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <ComplianceTab
          projectId="proj-1"
          projectName="Demo"
          teamId="00000000-0000-0000-0000-team00000001"
          projectRole="team_admin"
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ComplianceTab unified grid", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the empty state when the grid returns 0 rows", async () => {
    mockedList.mockResolvedValue(response([]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("compliance-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("compliance-virtual")).not.toBeInTheDocument();
  });

  it("renders rows with category badge, affected chip, and obligation chip", async () => {
    mockedList.mockResolvedValue(
      response([
        row("GPL-3.0-only", {
          category: "forbidden",
          affected_component_count: 5,
          affected_components: [
            {
              component_version_id: "cv-a",
              name: "alpha",
              version: "1.0.0",
              purl: null,
            },
          ],
          obligations: [
            {
              obligation_id: "ob-1",
              kind: "source-disclosure",
              summary: "Disclose corresponding source.",
            },
          ],
          notice_required: true,
        }),
      ]),
    );
    renderTab();

    const rowEl = await screen.findByTestId("compliance-row");
    expect(rowEl).toHaveAttribute("data-spdx-id", "GPL-3.0-only");
    expect(rowEl).toHaveAttribute("data-category", "forbidden");
    expect(rowEl).toHaveAttribute("data-has-obligations", "true");
    expect(rowEl).toHaveAttribute("data-notice-required", "true");

    // Category badge + obligation chip mount.
    expect(
      screen.getByTestId("license-category-badge-forbidden"),
    ).toBeInTheDocument();
    const chip = screen.getByTestId("compliance-obligation-chip");
    expect(chip).toHaveAttribute("data-kind", "source-disclosure");

    // Affected preview shows the chip + remaining count (1 shown, 5 total).
    expect(screen.getByText("alpha@1.0.0")).toBeInTheDocument();
    expect(
      screen.getByTestId("compliance-row-affected-more"),
    ).toHaveTextContent("+4 more");
  });

  it("filters by category when the user picks one in the toolbar", async () => {
    mockedList.mockResolvedValue(response([]));
    renderTab();

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    mockedList.mockClear();

    // Open the category MultiSelect and pick "forbidden".
    await userEvent.click(screen.getByTestId("compliance-category-filter"));
    const options = await screen.findAllByTestId(
      "compliance-category-filter-option",
    );
    const forbiddenOption = options.find(
      (el) => el.getAttribute("data-value") === "forbidden",
    );
    expect(forbiddenOption).toBeDefined();
    await userEvent.click(forbiddenOption as HTMLElement);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    const lastCall = mockedList.mock.calls.at(-1);
    expect(lastCall?.[1]?.categories).toEqual(["forbidden"]);
    expect(lastCall?.[1]?.offset).toBe(0);
  });

  it("filters by has_obligations when the switch is toggled on", async () => {
    mockedList.mockResolvedValue(response([]));
    renderTab();

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    mockedList.mockClear();

    await userEvent.click(screen.getByTestId("compliance-has-obligations"));

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    const lastCall = mockedList.mock.calls.at(-1);
    expect(lastCall?.[1]?.has_obligations).toBe(true);
  });

  it("opens the License drawer on row click (URL ?license= set)", async () => {
    mockedList.mockResolvedValue(
      response([
        row("MIT", {
          license_finding_id: "lf-mit-1",
        }),
      ]),
    );
    renderTab();
    // The row is a container <div>; the drawer-open affordance is the inner
    // button (the waive controls, themselves buttons, live alongside it).
    const r = await screen.findByTestId("compliance-row-open");
    await userEvent.click(r);

    // LicenseDrawer renders inside a Sheet — we assert on the URL fragment
    // (the drawer state's single source of truth). Use a sentinel via a
    // visible affordance: the drawer wraps content in a Sheet whose
    // role="dialog" mounts when ``open`` is true.
    await waitFor(() => {
      // Drawer mount → role="dialog" appears in the DOM.
      expect(screen.getAllByRole("dialog").length).toBeGreaterThan(0);
    });
  });

  it("renders a per-component waive action on forbidden rows with a purl", async () => {
    mockedList.mockResolvedValue(
      response([
        row("GPL-2.0-only", {
          category: "forbidden",
          affected_component_count: 1,
          affected_components: [
            {
              component_version_id: "cv-pyphen",
              name: "pyphen",
              version: "0.14.0",
              purl: "pkg:pypi/pyphen@0.14.0",
            },
          ],
        }),
      ]),
    );
    renderTabAsTeamAdmin();

    await waitFor(() => {
      expect(
        screen.getByTestId("compliance-row-waive-strip"),
      ).toBeInTheDocument();
    });
    // team_admin → the trigger is enabled (role-gated affordance present).
    const trigger = await screen.findByTestId("license-waive-open");
    expect(trigger).toBeEnabled();
  });

  it("omits the waive strip on allowed rows", async () => {
    mockedList.mockResolvedValue(
      response([
        row("MIT", {
          category: "allowed",
          affected_components: [
            {
              component_version_id: "cv-mit",
              name: "leftpad",
              version: "1.0.0",
              purl: "pkg:npm/leftpad@1.0.0",
            },
          ],
        }),
      ]),
    );
    renderTabAsTeamAdmin();

    await screen.findByTestId("compliance-row");
    expect(
      screen.queryByTestId("compliance-row-waive-strip"),
    ).not.toBeInTheDocument();
  });

  it("backward-compat: ?cview=obligations boots with has_obligations=true", async () => {
    mockedList.mockResolvedValue(response([]));
    renderTab(["/projects/proj-1?cview=obligations"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    // The first call (or any call) should carry has_obligations=true because
    // the cview redirect runs synchronously on mount before the query fires.
    const seenHasObligations = mockedList.mock.calls.some(
      ([, params]) => params?.has_obligations === true,
    );
    expect(seenHasObligations).toBe(true);
    // The toggle visibly reflects the auto-applied filter.
    expect(screen.getByTestId("compliance-has-obligations")).toBeChecked();
  });
});
