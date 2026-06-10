/**
 * ComponentDetailBody — unit tests (W10-E).
 *
 * Covers the surface-agnostic body extracted from `ComponentDrawer` in W10-E.
 * The drawer's own integration with this body is still verified by
 * `ComponentDrawer.test.tsx` (parity ensures both surfaces stay in sync).
 *
 * These tests focus on what the body owns directly:
 *   - meta panel renders severity / license / type / usage badges
 *   - vulnerabilities list renders one row per CVE (M-20: CVE id is a
 *     deep-link into the pre-filtered Vulnerabilities tab)
 *   - obligations section renders kind + text + license, with the link only
 *     rendered for http/https URLs (M-20 adversarial-input guard)
 *   - raw_data accordion toggles open/closed
 *
 * The body has no surface-specific state (no Sheet wrapper, no auth/loading
 * shell). M-20 added react-router `Link`s, so renders are wrapped in a
 * `MemoryRouter` (no route table needed — only hrefs are asserted).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
    obligations: [],
    raw_data: { source: "cdxgen" },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    depth: null,
    direct: false,
    dependency_scope: null,
    ...overrides,
  };
}

function renderBody(d: ComponentDetailResponse) {
  return render(
    <MemoryRouter>
      <ComponentDetailBody detail={d} />
    </MemoryRouter>,
  );
}

describe("ComponentDetailBody (W10-E)", () => {
  it("renders the meta panel and empty vulns list", () => {
    renderBody(detail());

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
    renderBody(
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

    expect(screen.getAllByTestId("component-drawer-vuln")).toHaveLength(2);
    expect(screen.getByText("CVE-2024-1234")).toBeInTheDocument();
    expect(screen.getByText("RCE in alpha")).toBeInTheDocument();
    expect(screen.getByText("Info leak")).toBeInTheDocument();
  });

  // ─── M-20 — CVE deep-link into the pre-filtered Vulnerabilities tab ─────

  it("renders each CVE id as a link to the Vulnerabilities tab pre-filtered on that id", () => {
    renderBody(
      detail({
        vulnerabilities: [
          {
            cve_id: "CVE-2024-1234",
            severity: "critical",
            cvss: 9.8,
            epss_score: null,
            epss_percentile: null,
            title: "RCE in alpha",
            description: null,
            fixed_version: null,
          },
        ],
      }),
    );

    const link = screen.getByTestId("component-drawer-vuln-link");
    expect(link).toHaveAttribute(
      "href",
      "/projects/proj-1?tab=vulnerabilities&search=CVE-2024-1234",
    );
    expect(link.textContent).toBe("CVE-2024-1234");
  });

  it("renders Type + Usage rows from depth + scope fields", () => {
    renderBody(
      detail({
        direct: true,
        depth: 1,
        dependency_scope: "required",
      }),
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
    renderBody(detail());

    const typeRow = screen.getByTestId("component-drawer-dependency-type");
    const usageRow = screen.getByTestId("component-drawer-usage");
    expect(
      typeRow.querySelector("[data-dependency-type='unknown']"),
    ).toBeInTheDocument();
    expect(
      usageRow.querySelector("[data-dependency-scope='unknown']"),
    ).toBeInTheDocument();
  });

  // ─── M-20 — Obligations section ──────────────────────────────────────────

  it("renders the obligations empty-state copy when the list is empty", () => {
    renderBody(detail());

    const section = screen.getByTestId("component-drawer-obligations");
    expect(section.textContent).toContain("Obligations (0)");
    expect(section.textContent).toContain(
      "No obligations recorded for this component's licenses.",
    );
  });

  it("renders one item per obligation with kind label + text + license", () => {
    renderBody(
      detail({
        obligations: [
          {
            id: "ob-1",
            kind: "attribution",
            text: "Retain copyright notices.",
            link: "https://example.com/attribution",
            license: "MIT",
          },
          {
            id: "ob-2",
            kind: "made-up-kind",
            text: "Free-form catalog duty.",
            link: null,
            license: "Apache-2.0",
          },
        ],
      }),
    );

    const items = screen.getAllByTestId("component-drawer-obligation");
    expect(items).toHaveLength(2);
    expect(
      screen.getByTestId("component-drawer-obligations").textContent,
    ).toContain("Obligations (2)");
    // Known kind resolves through the obligations.kind.* dictionary…
    expect(items[0].textContent).toContain("Attribution");
    expect(items[0].textContent).toContain("Retain copyright notices.");
    expect(items[0].textContent).toContain("MIT");
    // …unknown kinds fall back to the raw catalog string.
    expect(items[1].textContent).toContain("made-up-kind");
    // Only the https obligation carries an external link.
    const links = screen.getAllByTestId("component-drawer-obligation-link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute(
      "href",
      "https://example.com/attribution",
    );
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not render non-http(s) obligation links as anchors (adversarial input)", () => {
    renderBody(
      detail({
        obligations: [
          {
            id: "ob-js",
            kind: "attribution",
            // The backend does not scheme-filter; the body must refuse this.
            link: "javascript:alert(1)",
            text: "Hostile link.",
            license: "MIT",
          },
          {
            id: "ob-file",
            kind: "notice",
            link: "file:///etc/passwd",
            text: "Local scheme.",
            license: "MIT",
          },
          {
            id: "ob-junk",
            kind: "copyleft",
            link: "not a url at all",
            text: "Unparsable.",
            license: "GPL-3.0",
          },
        ],
      }),
    );

    expect(screen.getAllByTestId("component-drawer-obligation")).toHaveLength(
      3,
    );
    expect(
      screen.queryByTestId("component-drawer-obligation-link"),
    ).not.toBeInTheDocument();
  });

  it("toggles the raw_data accordion on demand", async () => {
    renderBody(detail());

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
