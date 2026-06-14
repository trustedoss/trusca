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
