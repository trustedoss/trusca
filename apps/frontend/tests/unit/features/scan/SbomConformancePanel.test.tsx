/**
 * SbomConformancePanel — unit tests (feat/model3-conformance-panel).
 *
 * The panel is pure presentational, so these tests render it directly with a
 * `SbomConformanceRead` fixture (no query/wire layer to mock). We assert the
 * accessibility-critical behaviors: the result badge pairs a tone with the
 * `data-result` attribute AND a visible label (color is not the only signal),
 * every check row renders with its localized label + status badge, the
 * `missing` list collapses to "+N more" past five entries, and a null coverage
 * metric renders an em-dash rather than "null%".
 *
 * i18n is the real instance (initialized once in tests/setup.ts, default `en`),
 * matching the GateResultCard / SeverityBadge test convention.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SbomConformancePanel } from "@/features/scan/SbomConformancePanel";
import type {
  SbomConformanceCheck,
  SbomConformanceRead,
} from "@/lib/projectsApi";

function check(
  overrides: Partial<SbomConformanceCheck> = {},
): SbomConformanceCheck {
  return {
    id: "purl",
    label: "Package URLs",
    required: true,
    status: "pass",
    detail: "",
    missing: [],
    ...overrides,
  };
}

function conformance(
  overrides: Partial<SbomConformanceRead> = {},
): SbomConformanceRead {
  return {
    scan_id: "11111111-1111-1111-1111-111111111111",
    project_id: "22222222-2222-2222-2222-222222222222",
    source_format: "cyclonedx",
    result: "pass",
    n_fail: 0,
    n_warn: 0,
    component_count: 412,
    purl_coverage_pct: 96,
    license_coverage_pct: 88,
    hash_coverage_pct: 73,
    checks: [check()],
    ...overrides,
  };
}

describe("SbomConformancePanel", () => {
  it.each([
    ["pass", "success"],
    ["warn", "medium"],
    ["fail", "critical"],
  ] as const)(
    "renders the %s result badge with data-result + a visible label",
    (result, _tone) => {
      render(
        <SbomConformancePanel conformance={conformance({ result })} />,
      );
      const badge = screen.getByTestId("conformance-badge");
      expect(badge).toHaveAttribute("data-result", result);
      // Color is never the only signal — the badge carries a text label too.
      expect(badge.textContent?.trim().length ?? 0).toBeGreaterThan(0);
      // A decorative dot accompanies the label (aria-hidden span).
      expect(badge.querySelector("span[aria-hidden]")).toBeTruthy();
    },
  );

  it("renders each check row with a localized label and a status badge", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            check({ id: "timestamp", status: "pass" }),
            check({ id: "purl", status: "warn", required: true }),
            check({ id: "hash", status: "fail", required: false }),
          ],
        })}
      />,
    );
    const table = screen.getByTestId("conformance-checks-table");
    expect(within(table).getByTestId("check-timestamp")).toBeInTheDocument();
    expect(within(table).getByTestId("check-purl")).toBeInTheDocument();
    expect(within(table).getByTestId("check-hash")).toBeInTheDocument();

    // Localized canonical label (en) renders rather than the raw id.
    const purlRow = screen.getByTestId("check-purl");
    expect(purlRow.textContent).toContain("Package URLs");
    expect(purlRow).toHaveAttribute("data-required", "true");

    // required vs recommended is shown as text, not just inferred.
    expect(screen.getByTestId("check-hash")).toHaveAttribute(
      "data-required",
      "false",
    );

    // Each row owns a status badge with its own data-status.
    const statuses = within(table).getAllByTestId("conformance-check-status");
    expect(statuses).toHaveLength(3);
    expect(statuses.map((b) => b.getAttribute("data-status"))).toEqual([
      "pass",
      "warn",
      "fail",
    ]);
  });

  it("falls back to the backend label for an unknown check id", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            check({ id: "future-check", label: "Some future axis" }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("check-future-check").textContent).toContain(
      "Some future axis",
    );
  });

  it("collapses the missing list to '+N more' past five entries", () => {
    const missing = ["a", "b", "c", "d", "e", "f", "g"]; // 7 → 5 shown + 2 more
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [check({ id: "purl", status: "warn", missing })],
        })}
      />,
    );
    const list = screen.getByTestId("check-purl-missing");
    // 5 visible chips + 1 overflow chip = 6 list items.
    expect(within(list).getAllByRole("listitem")).toHaveLength(6);
    const more = screen.getByTestId("check-purl-missing-more");
    expect(more.textContent).toContain("2");
  });

  it("does not render an overflow chip at or below the limit", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            check({ id: "purl", status: "warn", missing: ["a", "b", "c"] }),
          ],
        })}
      />,
    );
    expect(
      screen.queryByTestId("check-purl-missing-more"),
    ).not.toBeInTheDocument();
  });

  // --- G7 AI SBOM minimum elements (feat/g7-conformance) -------------------

  /** A G7 advisory check as the backend emits it (required=false, pass|warn). */
  function g7Check(
    overrides: Partial<SbomConformanceCheck> = {},
  ): SbomConformanceCheck {
    return check({
      id: "g7-model-name",
      label: "Model name",
      required: false,
      status: "pass",
      cluster: "models",
      source: "auto",
      role: "model-producer",
      evidence: null,
      ...overrides,
    });
  }

  it("does not render the G7 section for a core-only verdict", () => {
    // The 9 core checks carry null/absent G7 fields — their render is frozen.
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [check({ id: "purl" }), check({ id: "hash" })],
        })}
      />,
    );
    expect(
      screen.queryByTestId("conformance-g7-section"),
    ).not.toBeInTheDocument();
  });

  it("renders G7 checks below the base table, grouped by cluster in canonical order", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            check({ id: "purl" }),
            // Deliberately out of canonical order (models before metadata).
            g7Check({ id: "g7-model-name", cluster: "models" }),
            g7Check({
              id: "g7-meta-author",
              label: "SBOM author",
              cluster: "metadata",
            }),
          ],
        })}
      />,
    );
    const section = screen.getByTestId("conformance-g7-section");
    // Localized section title + localized cluster titles.
    expect(section.textContent).toContain("G7 AI SBOM minimum elements");
    const clusters = within(section).getAllByTestId(/^g7-cluster-/);
    expect(clusters.map((c) => c.getAttribute("data-testid"))).toEqual([
      "g7-cluster-metadata",
      "g7-cluster-models",
    ]);
    // The base table still holds only the base check.
    const table = screen.getByTestId("conformance-checks-table");
    expect(within(table).queryByTestId("check-g7-model-name")).toBeNull();
    expect(
      within(section).getByTestId("check-g7-model-name"),
    ).toBeInTheDocument();
  });

  it("shows the computed tally headline with advisory and review counts", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            g7Check({ id: "g7-a", status: "pass", source: "auto" }),
            g7Check({ id: "g7-b", status: "warn", source: "inferred" }),
            g7Check({
              id: "g7-c",
              status: "warn",
              source: "na",
              cluster: "slp",
            }),
          ],
        })}
      />,
    );
    // present 1 / autoTotal 2 (the na check is excluded from the base).
    expect(screen.getByTestId("conformance-g7-tally").textContent).toBe(
      "1 / 2 present",
    );
    expect(
      screen.getByTestId("conformance-g7-advisory").textContent,
    ).toContain("1");
    expect(screen.getByTestId("conformance-g7-review").textContent).toContain(
      "1",
    );
    // The human-review explanation renders because review > 0.
    expect(
      screen.getByTestId("conformance-g7-section").textContent,
    ).toContain("require human review");
  });

  it("pairs each G7 row with a status badge and a source badge (color never alone)", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            g7Check({ id: "g7-a", source: "auto" }),
            g7Check({ id: "g7-b", source: "inferred" }),
            g7Check({ id: "g7-c", source: "declared" }),
            g7Check({ id: "g7-d", source: "na", status: "warn" }),
          ],
        })}
      />,
    );
    const section = screen.getByTestId("conformance-g7-section");
    const sources = within(section).getAllByTestId("g7-source-badge");
    expect(sources.map((b) => b.getAttribute("data-source"))).toEqual([
      "auto",
      "inferred",
      "declared",
      "na",
    ]);
    // Every source badge carries a visible text label, not just a tint.
    for (const badge of sources) {
      expect(badge.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    }
    expect(
      within(section).getAllByTestId("conformance-check-status"),
    ).toHaveLength(4);
  });

  it("renders evidence chips and a guidance link when available", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            g7Check({
              id: "g7-model-license",
              label: "Model license",
              evidence: ["Apache-2.0", "MIT"],
            }),
            g7Check({
              id: "g7-slp-data-flow",
              label: "System data flow",
              cluster: "slp",
              status: "warn",
              source: "na",
            }),
          ],
        })}
      />,
    );
    // Evidence values render as mono chips.
    const chips = within(
      screen.getByTestId("check-g7-model-license-evidence"),
    ).getAllByRole("listitem");
    expect(chips.map((c) => c.textContent)).toEqual(["Apache-2.0", "MIT"]);
    // Guidance link only for ids the vendored table knows.
    const link = screen.getByTestId("check-g7-model-license-guidance");
    expect(link).toHaveAttribute("href", expect.stringContaining("https://"));
    expect(
      screen.queryByTestId("check-g7-slp-data-flow-guidance"),
    ).not.toBeInTheDocument();
  });

  it("renders an em-dash for a null coverage metric", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          purl_coverage_pct: 96,
          license_coverage_pct: null,
          hash_coverage_pct: null,
        })}
      />,
    );
    expect(screen.getByTestId("conformance-purl-coverage").textContent).toBe(
      "96%",
    );
    expect(
      screen.getByTestId("conformance-license-coverage").textContent,
    ).toBe("—");
    expect(screen.getByTestId("conformance-hash-coverage").textContent).toBe(
      "—",
    );
  });
});
