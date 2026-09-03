/**
 * GateResultCard — unit tests (v2.1 UI gap #1).
 *
 * Mocks the wire layer so the card's behavior is the unit under test: skeleton
 * loading, RFC 7807 error, the pass / fail badges (color paired with an icon +
 * label), the fail reason + counts, the EPSS metric appearing only when the
 * gate is configured, and the "no scan yet" neutral state.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GateResultResponse } from "@/features/projects/api/projectDetailApi";
import { GateResultCard } from "@/features/projects/components/GateResultCard";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/projectDetailApi", async () => {
  return {
    getGateResult: vi.fn(),
  };
});

import { getGateResult } from "@/features/projects/api/projectDetailApi";

const mockedGet = vi.mocked(getGateResult);

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

function gate(overrides: Partial<GateResultResponse> = {}): GateResultResponse {
  return {
    gate: "pass",
    reason: null,
    critical_cve_count: 0,
    forbidden_license_count: 0,
    epss_gate_count: 0,
    epss_threshold: null,
    malicious_component_count: 0,
    malicious_scan_assessed: true,
    project_id: PROJECT_ID,
    scan_id: "22222222-2222-2222-2222-222222222222",
    evaluated_at: "2026-05-23T00:00:00Z",
    ...overrides,
  };
}

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GateResultCard projectId={PROJECT_ID} />
    </QueryClientProvider>,
  );
}

describe("GateResultCard", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders a skeleton while the query is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {})); // never resolves
    renderCard();
    expect(screen.getByTestId("gate-card-loading")).toBeInTheDocument();
  });

  it("renders the pass badge with no reason and zeroed counts", async () => {
    mockedGet.mockResolvedValueOnce(gate());
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-card")).toHaveAttribute("data-gate", "pass");
    expect(screen.getByTestId("gate-badge-pass")).toBeInTheDocument();
    // Pass badge pairs color with a visible label (color not the only signal).
    expect(screen.getByTestId("gate-badge-pass").textContent).toMatch(/pass/i);
    expect(screen.getByTestId("gate-pass-detail")).toBeInTheDocument();
    expect(screen.queryByTestId("gate-reason")).not.toBeInTheDocument();
    expect(screen.getByTestId("gate-metric-critical")).toHaveAttribute(
      "data-value",
      "0",
    );
  });

  it("composes a localized fail reason from the counts, ignoring the backend reason (BUG-002)", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({
        gate: "fail",
        // The backend's English `reason` must NOT be rendered — the card
        // composes a localized reason from the structured counts instead.
        reason: "1 critical CVE and 2 forbidden licenses block this build.",
        critical_cve_count: 1,
        forbidden_license_count: 2,
      }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-card")).toHaveAttribute("data-gate", "fail");
    expect(screen.getByTestId("gate-badge-fail")).toBeInTheDocument();
    expect(screen.getByTestId("gate-badge-fail").textContent).toMatch(/fail/i);

    const reason = screen.getByTestId("gate-reason");
    // The backend's raw English sentence is gone.
    expect(reason.textContent).not.toContain("block this build");
    // Two positive counts → two composed clauses. Per the codebase i18n
    // convention (i18next-parser pluralSeparator: false) the keys use simple
    // {{count}} interpolation with count-neutral "(s)" text, not _one/_other.
    expect(reason).toHaveAttribute("data-reason-clauses", "2");
    expect(reason.textContent).toContain("1 open critical CVE(s)");
    expect(reason.textContent).toContain("2 forbidden license(s)");

    expect(screen.getByTestId("gate-metric-critical")).toHaveAttribute(
      "data-value",
      "1",
    );
    expect(screen.getByTestId("gate-metric-forbidden")).toHaveAttribute(
      "data-value",
      "2",
    );
  });

  it("falls back to a generic localized reason when no count is positive (BUG-002)", async () => {
    // Forward-compat: a fail with zero modeled counts (e.g. a new gate axis)
    // still renders a localized fallback, not blank.
    mockedGet.mockResolvedValueOnce(
      gate({
        gate: "fail",
        reason: "Some new gate axis blocked the build.",
        critical_cve_count: 0,
        forbidden_license_count: 0,
        epss_gate_count: 0,
      }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-reason")).toBeInTheDocument();
    });
    const reason = screen.getByTestId("gate-reason");
    expect(reason).toHaveAttribute("data-reason-clauses", "0");
    expect(reason.textContent).not.toContain("Some new gate axis");
    expect(reason.textContent).toContain("The build gate failed");
  });

  it("composes an EPSS clause when the EPSS gate trips (BUG-002)", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({
        gate: "fail",
        critical_cve_count: 0,
        forbidden_license_count: 0,
        epss_threshold: 0.5,
        epss_gate_count: 3,
      }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-reason")).toBeInTheDocument();
    });
    const reason = screen.getByTestId("gate-reason");
    expect(reason).toHaveAttribute("data-reason-clauses", "1");
    expect(reason.textContent).toContain("EPSS");
    expect(reason.textContent).toContain("0.5");
  });

  it("hides the EPSS metric when the gate is disabled and shows it when enabled", async () => {
    mockedGet.mockResolvedValueOnce(gate());
    const { unmount } = renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate-metric-epss")).not.toBeInTheDocument();
    unmount();

    mockedGet.mockResolvedValueOnce(
      gate({ epss_threshold: 0.5, epss_gate_count: 3, gate: "fail" }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-metric-epss")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-metric-epss")).toHaveAttribute(
      "data-value",
      "3",
    );
  });

  it("says the EPSS axis was not evaluated instead of rendering a zero", async () => {
    // ER43: a threshold is set and nothing on the scan carries a score, so the
    // count is 0 because the axis judged nothing. Drawing "EPSS >= 0.5 · 0"
    // here would state the opposite of what happened.
    mockedGet.mockResolvedValueOnce(
      gate({ epss_threshold: 0.5, epss_gate_count: 0, epss_outcome: "no_data" }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-epss-no-data")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate-metric-epss")).not.toBeInTheDocument();
    expect(screen.getByTestId("gate-epss-no-data").textContent).toContain("0.5");
  });

  it("keeps the EPSS metric but qualifies it on partial coverage", async () => {
    // Partial is the common state, so the count still stands: a finding above
    // the threshold blocks either way. What it cannot claim is completeness.
    mockedGet.mockResolvedValueOnce(
      gate({ epss_threshold: 0.5, epss_gate_count: 2, epss_outcome: "partial" }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-metric-epss")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-metric-epss")).toHaveAttribute(
      "data-value",
      "2",
    );
    expect(screen.getByTestId("gate-epss-partial")).toBeInTheDocument();
  });

  it("renders the plain metric when every finding carried a score", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({
        epss_threshold: 0.5,
        epss_gate_count: 0,
        epss_outcome: "evaluated",
      }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-metric-epss")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate-epss-no-data")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gate-epss-partial")).not.toBeInTheDocument();
  });

  it("renders a neutral no-scan state when scan_id is null", async () => {
    mockedGet.mockResolvedValueOnce(gate({ scan_id: null }));
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-card")).toHaveAttribute("data-gate", "none");
    expect(screen.getByTestId("gate-badge-none")).toBeInTheDocument();
    expect(screen.getByTestId("gate-no-scan")).toBeInTheDocument();
    // Must not render a misleading pass/fail badge.
    expect(screen.queryByTestId("gate-badge-pass")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gate-badge-fail")).not.toBeInTheDocument();
  });

  it("renders a localized error for a 403, not the backend English detail (BUG-002)", async () => {
    mockedGet.mockRejectedValueOnce(
      new ProblemError("forbidden", {
        status: 403,
        title: "Forbidden",
        detail: "You cannot view this project.",
        problem: null,
      }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card-error")).toBeInTheDocument();
    });
    // The backend's English detail must NOT leak through.
    expect(screen.getByTestId("gate-card-error").textContent).not.toContain(
      "You cannot view this project.",
    );
    // Localized 403 message (EN locale resolves the `forbidden` key).
    expect(screen.getByTestId("gate-card-error").textContent).toContain(
      "do not have permission",
    );
  });

  it("renders a localized error for a 404 (BUG-002)", async () => {
    mockedGet.mockRejectedValueOnce(
      new ProblemError("not found", {
        status: 404,
        title: "Project Not Found",
        detail: "project abc not found",
        problem: null,
      }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-card-error").textContent).not.toContain(
      "project abc not found",
    );
    expect(screen.getByTestId("gate-card-error").textContent).toContain(
      "no longer exists",
    );
  });

  // ER29. The KEV and end-of-life axes each report a count and an outcome, and
  // the whole reason the outcome exists is that a count of 0 means two
  // different things. These pin that the card never shows the count alone when
  // the axis could not judge.

  it("does not mention KEV or end of life when the axes are off", async () => {
    mockedGet.mockResolvedValueOnce(gate());
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-card")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate-metric-kev")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gate-metric-eol")).not.toBeInTheDocument();
  });

  it("shows the KEV count when the axis evaluated", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({ kev_gate_enabled: true, kev_outcome: "evaluated", kev_gate_count: 2 }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-metric-kev")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-metric-kev").textContent).toContain("2");
  });

  it("replaces the KEV count with a caveat when the catalog never synced", async () => {
    // A "Known-exploited · 0" row here would report an all-clear for something
    // nobody checked, which is the defect this axis exists to close.
    mockedGet.mockResolvedValueOnce(
      gate({ kev_gate_enabled: true, kev_outcome: "no_data", kev_gate_count: 0 }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-kev-no-data")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate-metric-kev")).not.toBeInTheDocument();
  });

  it("qualifies a partial KEV count instead of hiding it", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({ kev_gate_enabled: true, kev_outcome: "partial", kev_gate_count: 1 }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-metric-kev")).toBeInTheDocument();
    });
    // The count is true as far as it goes, so it stays, with the caveat beside it.
    expect(screen.getByTestId("gate-kev-partial")).toBeInTheDocument();
  });

  it("replaces the end-of-life count with a caveat when nothing was checked", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({ eol_gate_enabled: true, eol_outcome: "no_data", eol_gate_count: 0 }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-eol-no-data")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate-metric-eol")).not.toBeInTheDocument();
  });

  it("qualifies a partial end-of-life count, the ordinary state", async () => {
    mockedGet.mockResolvedValueOnce(
      gate({ eol_gate_enabled: true, eol_outcome: "partial", eol_gate_count: 3 }),
    );
    renderCard();
    await waitFor(() => {
      expect(screen.getByTestId("gate-metric-eol")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gate-eol-partial")).toBeInTheDocument();
  });
});
