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
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SbomConformancePanel } from "@/features/scan/SbomConformancePanel";
import type {
  RegulatoryCrosswalk,
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

  it("renders G7 missing offender chips only when the warn row carries them (#447 v2)", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            // models cluster warn row with missing model names (offenders).
            g7Check({
              id: "g7-model-hash-value",
              label: "Model hash",
              cluster: "models",
              status: "warn",
              source: "auto",
              missing: ["gpt-oss-20b", "llama-3-8b"],
            }),
            // A satisfied row with no missing → no missing list.
            g7Check({
              id: "g7-model-license",
              label: "Model license",
              cluster: "models",
              status: "pass",
              evidence: ["Apache-2.0"],
            }),
          ],
        })}
      />,
    );
    const missing = screen.getByTestId("check-g7-model-hash-value-missing");
    // The two offender names render as mono chips (plus the "Missing:" label li).
    expect(missing.textContent).toContain("gpt-oss-20b");
    expect(missing.textContent).toContain("llama-3-8b");
    // The satisfied row has no missing block.
    expect(
      screen.queryByTestId("check-g7-model-license-missing"),
    ).not.toBeInTheDocument();
    // Evidence and missing are distinct surfaces on the same panel.
    expect(
      screen.getByTestId("check-g7-model-license-evidence").textContent,
    ).toContain("Apache-2.0");
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

  // --- Regulatory field checks + crosswalk (feat/sbom-conformance-crosswalk) -

  it("renders the 5 regulatory field checks in the base table without a G7 section", () => {
    // The new checks have no cluster and no g7- prefix; `file-properties` may
    // carry source="na" and must STILL render as a base row (never leak into
    // the G7 section or its tally).
    render(
      <SbomConformancePanel
        conformance={conformance({
          checks: [
            check({ id: "purl" }),
            check({
              id: "hash-algorithm",
              label: "Hash algorithm strength",
              required: false,
              status: "warn",
            }),
            check({
              id: "file-properties",
              label: "File-level properties",
              required: false,
              status: "warn",
              source: "na",
            }),
          ],
        })}
      />,
    );
    const table = screen.getByTestId("conformance-checks-table");
    expect(
      within(table).getByTestId("check-hash-algorithm"),
    ).toBeInTheDocument();
    expect(
      within(table).getByTestId("check-file-properties"),
    ).toBeInTheDocument();
    // Backend-supplied labels render (no FE check_id.* localization for them).
    expect(
      within(table).getByTestId("check-hash-algorithm").textContent,
    ).toContain("Hash algorithm strength");
    // No G7 section — the source="na" regulatory row is not a G7 check.
    expect(
      screen.queryByTestId("conformance-g7-section"),
    ).not.toBeInTheDocument();
  });

  /** A crosswalk rollup as the backend emits it (two frameworks). */
  function crosswalk(
    overrides: Partial<RegulatoryCrosswalk> = {},
  ): RegulatoryCrosswalk {
    return {
      disclaimer:
        "Informational mapping — not legal advice or a compliance determination.",
      disclaimer_ko: "참고용 매핑입니다. 규제 준수 판정이 아닙니다.",
      frameworks: [
        {
          id: "bsi-tr-03183-2",
          title: "BSI Technical Guideline TR-03183 Part 2",
          title_ko: "BSI 기술 지침 TR-03183 2부",
          short: "BSI TR-03183-2",
          short_ko: "BSI TR-03183-2",
          source: "https://example.invalid/bsi",
          total: 9,
          present: 6,
          gap: 2,
          review: 1,
          elements: [
            {
              id: "hash",
              label: "File hashes",
              status: "warn",
              source: null,
              detail: "40% (2/5)",
              refs: ["Section 5.2.2"],
            },
            {
              id: "purl",
              label: "Package URLs",
              status: "pass",
              source: null,
              detail: "100% (5/5)",
              refs: ["Section 5.2.4"],
            },
          ],
        },
        {
          id: "ntia-minimum",
          title: "NTIA Minimum Elements for an SBOM",
          title_ko: "NTIA SBOM 최소 요소",
          short: "NTIA minimum",
          short_ko: "NTIA 최소 요소",
          source: "https://example.invalid/ntia",
          total: 5,
          present: 5,
          gap: 0,
          review: 0,
          elements: [
            {
              id: "timestamp",
              label: "Document timestamp",
              status: "pass",
              source: null,
              detail: "",
              refs: ["§2"],
            },
          ],
        },
      ],
      ...overrides,
    };
  }

  it("does not render the crosswalk section when regulatory_crosswalk is null", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({ regulatory_crosswalk: null })}
      />,
    );
    expect(
      screen.queryByTestId("conformance-crosswalk-section"),
    ).not.toBeInTheDocument();
  });

  it("renders one framework row per framework with the present/gap/review tally", () => {
    render(
      <SbomConformancePanel
        conformance={conformance({ regulatory_crosswalk: crosswalk() })}
      />,
    );
    const section = screen.getByTestId("conformance-crosswalk-section");
    // Localized section title + informational intro (not a verdict).
    expect(section.textContent).toContain("Regulatory crosswalk");
    expect(section.textContent).toContain("not a compliance determination");

    const rows = within(section)
      .getAllByTestId(/^crosswalk-framework-/)
      .filter((r) => {
        const id = r.getAttribute("data-testid") ?? "";
        return !id.endsWith("-toggle") && !id.endsWith("-elements");
      });
    expect(rows).toHaveLength(2);

    // Rollup numbers surface as data attributes (locale-independent).
    const bsi = screen.getByTestId("crosswalk-framework-bsi-tr-03183-2");
    expect(bsi).toHaveAttribute("data-total", "9");
    expect(bsi).toHaveAttribute("data-present", "6");
    expect(bsi).toHaveAttribute("data-gap", "2");
    expect(bsi).toHaveAttribute("data-review", "1");
    // Locale-aware short name + the visible tally text.
    expect(bsi.textContent).toContain("BSI TR-03183-2");
    expect(bsi.textContent).toContain("6 / 9 present");
    // Gap/review badges only where the count is > 0.
    expect(
      screen.getByTestId("crosswalk-bsi-tr-03183-2-gap").textContent,
    ).toContain("2");
    expect(
      screen.getByTestId("crosswalk-bsi-tr-03183-2-review").textContent,
    ).toContain("1");
    expect(
      screen.queryByTestId("crosswalk-ntia-minimum-gap"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("crosswalk-ntia-minimum-review"),
    ).not.toBeInTheDocument();

    // The backend disclaimer renders as small muted text at the end.
    expect(
      screen.getByTestId("conformance-crosswalk-disclaimer").textContent,
    ).toBe(
      "Informational mapping — not legal advice or a compliance determination.",
    );
  });

  it("expands a framework row to its mapped elements (label, status badge, refs, detail)", async () => {
    const user = userEvent.setup();
    render(
      <SbomConformancePanel
        conformance={conformance({ regulatory_crosswalk: crosswalk() })}
      />,
    );
    // Collapsed by default.
    expect(
      screen.queryByTestId("crosswalk-framework-bsi-tr-03183-2-elements"),
    ).not.toBeInTheDocument();

    const toggle = screen.getByTestId(
      "crosswalk-framework-bsi-tr-03183-2-toggle",
    );
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const elements = screen.getByTestId(
      "crosswalk-framework-bsi-tr-03183-2-elements",
    );
    const hashRow = within(elements).getByTestId(
      "crosswalk-element-bsi-tr-03183-2-hash",
    );
    expect(hashRow.textContent).toContain("File hashes");
    expect(hashRow.textContent).toContain("40% (2/5)");
    expect(hashRow.textContent).toContain("Section 5.2.2");
    // Status is a badge (text + dot), not color alone.
    expect(
      within(hashRow).getByTestId("conformance-check-status"),
    ).toHaveAttribute("data-status", "warn");

    // Collapses again on a second activation.
    await user.click(toggle);
    expect(
      screen.queryByTestId("crosswalk-framework-bsi-tr-03183-2-elements"),
    ).not.toBeInTheDocument();
  });
});
