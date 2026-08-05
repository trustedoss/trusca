/**
 * The governance band.
 *
 * The band's job is to be trustworthy at a glance, so the tests are about the
 * three ways a glance can be misled:
 *
 *   - a gate that never ran rendering like a gate that passed;
 *   - a failed request rendering like a clean project;
 *   - a trend line drawn through one data point.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectGovernance } from "@/features/projects/api/governance";
import { GovernanceBand } from "@/features/projects/components/GovernanceBand";

const apiGet = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get: apiGet } }));

function band(overrides: Partial<ProjectGovernance> = {}): ProjectGovernance {
  return {
    project_id: "p-1",
    scanned: true,
    risk_score: 42,
    gate: {
      status: "pass",
      critical_cve_count: 0,
      forbidden_license_count: 0,
      epss_gate_count: 0,
      malicious_component_count: 0,
      scan_id: "s-1",
    },
    kev_sla: { overdue: 0, due_soon: 0 },
    pending_approvals: 0,
    trend: [],
    ...overrides,
  };
}

function renderBand() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GovernanceBand projectId="p-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("GovernanceBand", () => {
  it("does not report a verdict for a project nobody has scanned", async () => {
    apiGet.mockResolvedValue({
      data: band({
        scanned: false,
        risk_score: 0,
        gate: {
          status: null,
          critical_cve_count: 0,
          forbidden_license_count: 0,
          epss_gate_count: 0,
          malicious_component_count: 0,
          scan_id: null,
        },
      }),
    });

    renderBand();

    const gate = await screen.findByTestId("governance-gate");
    // Not "Pass" — the two states this band must never blur.
    expect(gate.textContent).toContain("Never scanned");
    expect(gate).toHaveAttribute("data-tone", "neutral");
    expect(screen.getByTestId("governance-risk").textContent).toContain("—");
  });

  it("marks a blocked gate and links to what blocked it", async () => {
    apiGet.mockResolvedValue({
      data: band({
        gate: {
          status: "fail",
          critical_cve_count: 3,
          forbidden_license_count: 1,
          epss_gate_count: 0,
          malicious_component_count: 0,
          scan_id: "s-1",
        },
      }),
    });

    renderBand();

    const gate = await screen.findByTestId("governance-gate");
    expect(gate).toHaveAttribute("data-tone", "danger");
    expect(gate).toHaveAttribute("href", "/projects/p-1?tab=vulnerabilities");
    // The reason is text, not only a tint.
    expect(gate.textContent).toContain("3 critical");
    expect(gate.textContent).toContain("1 forbidden");
  });

  it("names the condition that actually blocked, not every condition", async () => {
    apiGet.mockResolvedValue({
      data: band({
        gate: {
          status: "fail",
          critical_cve_count: 0,
          forbidden_license_count: 0,
          epss_gate_count: 3,
          malicious_component_count: 0,
          scan_id: "s-1",
        },
      }),
    });

    renderBand();

    const gate = await screen.findByTestId("governance-gate");
    // An EPSS-only failure used to read "0 critical · 0 forbidden licences" —
    // every number true, the sentence useless.
    expect(gate.textContent).not.toContain("0 critical");
    expect(gate.textContent).toContain("EPSS");
  });

  it("separates overdue KEV deadlines from ones still in hand", async () => {
    apiGet.mockResolvedValue({
      data: band({ kev_sla: { overdue: 2, due_soon: 1 } }),
    });

    renderBand();

    const kev = await screen.findByTestId("governance-kev");
    expect(kev).toHaveAttribute("data-tone", "danger");
    expect(kev.textContent).toContain("3");
    expect(kev.textContent).toContain("2 overdue");
  });

  it("does not draw a trend through a single scan", async () => {
    apiGet.mockResolvedValue({
      data: band({
        trend: [{ scan_id: "s-1", scanned_at: "2026-07-01T00:00:00Z", critical: 4 }],
      }),
    });

    renderBand();

    await screen.findByTestId("governance-band");
    // One point is a dot, and a line through it is an invented slope.
    expect(screen.queryByTestId("governance-trend-spark")).toBeNull();
    expect(screen.getByTestId("governance-trend").textContent).toContain(
      "Not enough scans",
    );
  });

  it("reports the direction of the trend in words as well as a line", async () => {
    apiGet.mockResolvedValue({
      data: band({
        trend: [
          { scan_id: "s-1", scanned_at: "2026-07-01T00:00:00Z", critical: 1 },
          { scan_id: "s-2", scanned_at: "2026-07-02T00:00:00Z", critical: 5 },
        ],
      }),
    });

    renderBand();

    const trend = await screen.findByTestId("governance-trend");
    expect(trend).toHaveAttribute("data-delta", "4");
    expect(trend.textContent).toContain("+4");
    expect(screen.getByTestId("governance-trend-spark")).toBeInTheDocument();
  });

  it("says nothing rather than something false when the request fails", async () => {
    apiGet.mockRejectedValue(new Error("boom"));

    renderBand();

    expect(await screen.findByTestId("governance-band-error")).toBeInTheDocument();
    // No band means no "Pass", no zero KEV count — an absent answer, not a
    // reassuring one.
    expect(screen.queryByTestId("governance-band")).toBeNull();
    expect(screen.queryByTestId("governance-gate")).toBeNull();
  });
});
