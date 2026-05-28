/**
 * ComponentDetailBody — unit tests (W10-E).
 *
 * Covers the surface-agnostic body extracted from `ComponentDrawer` in W10-E.
 * The drawer's own integration with this body is still verified by
 * `ComponentDrawer.test.tsx` (parity ensures both surfaces stay in sync).
 *
 * These tests focus on what the body owns directly:
 *   - meta panel renders severity / license / type / usage badges
 *   - vulnerabilities list renders one row per CVE
 *   - raw_data accordion toggles open/closed
 *
 * The body has no surface-specific state (no Sheet wrapper, no auth/loading
 * shell), so the tests render it standalone with a hand-built fixture.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ComponentDetailResponse } from "@/features/projects/api/projectDetailApi";
import { ComponentDetailBody } from "@/features/projects/components/ComponentDetailBody";

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
    depth: null,
    direct: false,
    dependency_scope: null,
    ...overrides,
  };
}

describe("ComponentDetailBody (W10-E)", () => {
  it("renders the meta panel and empty vulns list", () => {
    render(<ComponentDetailBody detail={detail()} />);

    // Meta panel: severity + license badges + purl row.
    expect(screen.getByTestId("component-drawer-meta")).toBeInTheDocument();
    expect(screen.getByTestId("component-drawer-purl").textContent).toContain(
      "pkg:npm/alpha@1.0.0",
    );
    // Vulns section renders the empty-state copy.
    expect(screen.getByTestId("component-drawer-vulns").textContent).toContain(
      "No known vulnerabilities",
    );
  });

  it("renders one item per vulnerability with CVE + severity + title", () => {
    render(
      <ComponentDetailBody
        detail={detail({
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
        })}
      />,
    );

    expect(screen.getAllByTestId("component-drawer-vuln")).toHaveLength(2);
    expect(screen.getByText("CVE-2024-1234")).toBeInTheDocument();
    expect(screen.getByText("RCE in alpha")).toBeInTheDocument();
    expect(screen.getByText("Info leak")).toBeInTheDocument();
  });

  it("renders Type + Usage rows from depth + scope fields", () => {
    render(
      <ComponentDetailBody
        detail={detail({
          direct: true,
          depth: 1,
          dependency_scope: "required",
        })}
      />,
    );

    const typeRow = screen.getByTestId("component-drawer-dependency-type");
    const usageRow = screen.getByTestId("component-drawer-usage");
    const typeBadge = typeRow.querySelector(
      "[data-testid='dependency-type-badge']",
    );
    const scopeBadge = usageRow.querySelector(
      "[data-testid='dependency-scope-badge']",
    );
    expect(typeBadge).toHaveAttribute("data-dependency-type", "direct");
    expect(typeBadge).toHaveAttribute("data-depth", "1");
    expect(scopeBadge).toHaveAttribute("data-dependency-scope", "required");
  });

  it("renders '—' Type + Usage badges when depth and scope are null", () => {
    render(<ComponentDetailBody detail={detail()} />);

    const typeRow = screen.getByTestId("component-drawer-dependency-type");
    const usageRow = screen.getByTestId("component-drawer-usage");
    expect(
      typeRow.querySelector("[data-dependency-type='unknown']"),
    ).toBeInTheDocument();
    expect(
      usageRow.querySelector("[data-dependency-scope='unknown']"),
    ).toBeInTheDocument();
  });

  it("toggles the raw_data accordion on demand", async () => {
    render(<ComponentDetailBody detail={detail()} />);

    // Initially the raw JSON block is not in the DOM — only the toggle.
    expect(screen.getByTestId("component-drawer-raw-toggle")).toBeInTheDocument();
    expect(
      screen.queryByTestId("component-drawer-raw-json"),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("component-drawer-raw-toggle"));

    expect(screen.getByTestId("component-drawer-raw-json")).toBeInTheDocument();
    expect(
      screen.getByTestId("component-drawer-raw-json").textContent,
    ).toContain("cdxgen");

    // Toggle again — pre block disappears.
    await userEvent.click(screen.getByTestId("component-drawer-raw-toggle"));
    expect(
      screen.queryByTestId("component-drawer-raw-json"),
    ).not.toBeInTheDocument();
  });
});
