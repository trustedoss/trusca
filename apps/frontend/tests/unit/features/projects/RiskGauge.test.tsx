/**
 * RiskGauge — unit tests (PR #10).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskGauge } from "@/features/projects/components/RiskGauge";

describe("RiskGauge", () => {
  it("clamps a negative score to zero and labels it none", () => {
    render(<RiskGauge score={-50} />);
    const gauge = screen.getByTestId("risk-gauge");
    expect(gauge).toHaveAttribute("data-score", "0");
    expect(screen.getByTestId("risk-gauge-label").textContent?.toLowerCase()).toContain(
      "no risk",
    );
  });

  it("clamps a >100 score to 100 and labels it critical", () => {
    render(<RiskGauge score={250} />);
    expect(screen.getByTestId("risk-gauge")).toHaveAttribute(
      "data-score",
      "100",
    );
    expect(screen.getByTestId("risk-gauge-label").textContent?.toLowerCase()).toContain(
      "critical",
    );
  });

  it("renders the numeric value", () => {
    render(<RiskGauge score={42} />);
    expect(screen.getByTestId("risk-gauge-value").textContent).toContain("42");
  });

  it("uses medium severity tone in the 25-49 range", () => {
    render(<RiskGauge score={30} />);
    expect(
      screen.getByTestId("risk-gauge-label").textContent?.toLowerCase(),
    ).toContain("medium");
  });

  it("uses high severity tone in the 50-74 range", () => {
    render(<RiskGauge score={60} />);
    expect(
      screen.getByTestId("risk-gauge-label").textContent?.toLowerCase(),
    ).toContain("high");
  });
});
