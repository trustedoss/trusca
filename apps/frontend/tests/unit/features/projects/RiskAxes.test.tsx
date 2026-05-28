/**
 * RiskAxes — unit tests (Wave 1 #34).
 *
 * The Overview risk card now shows two independent axes instead of one
 * composite score. The headline regression these tests guard: a project with
 * zero vulnerabilities but conditional licenses must NOT read as Critical.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskAxes } from "@/features/projects/components/RiskAxes";

describe("RiskAxes", () => {
  it("renders independent Security and License gauges with their scores", () => {
    render(
      <RiskAxes
        securityScore={0}
        licenseScore={45.6}
        severityDistribution={{}}
        licenseDistribution={{ conditional: 24, allowed: 100 }}
      />,
    );
    expect(screen.getByTestId("risk-axis-security")).toHaveAttribute(
      "data-score",
      "0",
    );
    expect(screen.getByTestId("risk-axis-license")).toHaveAttribute(
      "data-score",
      "45.6",
    );
    // Exactly two gauges — one per axis.
    expect(screen.getAllByTestId("risk-gauge")).toHaveLength(2);
  });

  it("surfaces the driving counts per axis", () => {
    render(
      <RiskAxes
        securityScore={80}
        licenseScore={80}
        severityDistribution={{ critical: 3, high: 8 }}
        licenseDistribution={{ forbidden: 2, conditional: 5 }}
      />,
    );
    const sec = screen.getByTestId("risk-axis-security-counts").textContent ?? "";
    const lic = screen.getByTestId("risk-axis-license-counts").textContent ?? "";
    expect(sec).toContain("3");
    expect(sec).toContain("8");
    expect(lic).toContain("2");
    expect(lic).toContain("5");
  });

  it("conditional-only project shows Security clean, not Critical (#34)", () => {
    render(
      <RiskAxes
        securityScore={0}
        licenseScore={45.6}
        severityDistribution={{}}
        licenseDistribution={{ conditional: 24 }}
      />,
    );
    // Security axis is 0 (clean) — the License axis carries the only signal,
    // capped in the Medium band. The old single gauge would have read "100".
    expect(screen.getByTestId("risk-axis-security")).toHaveAttribute(
      "data-score",
      "0",
    );
    expect(screen.getByTestId("risk-axis-license")).toHaveAttribute(
      "data-score",
      "45.6",
    );
  });
});
