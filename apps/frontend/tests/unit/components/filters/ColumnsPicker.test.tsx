/**
 * ColumnsPicker — unit tests (W9 #52).
 *
 * Validates the "Columns" affordance that toggles per-column visibility on
 * the Components and Vulnerabilities tabs:
 *   - the trigger label comes from `filters.columns.trigger` (NOT a
 *     hardcoded string), tracks the active i18n language,
 *   - opening the dropdown renders one checkbox row per column with
 *     `data-testid="<base>-option-<id>"`,
 *   - toggling an optional column fires `onChange` with the next visible set,
 *   - required columns render disabled-but-checked and the user cannot turn
 *     them off via click,
 *   - changes persist to localStorage under the supplied `storageKey`,
 *   - `loadInitialVisibility` round-trips a saved visible set on the next
 *     render (= reload survival), with required ids always re-unioned even
 *     after a stale localStorage entry tried to hide them.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ColumnsPicker,
  loadInitialVisibility,
} from "@/components/filters/ColumnsPicker";
import i18n from "@/lib/i18n";

const COLUMNS = [
  { id: "cve_id", label: "CVE ID", required: true },
  { id: "component", label: "Component" },
  { id: "severity", label: "Severity", required: true },
  { id: "cvss", label: "CVSS" },
  { id: "epss", label: "EPSS" },
];

const STORAGE_KEY = "column-visibility:test";

function setup(visible: string[] = COLUMNS.map((c) => c.id)) {
  const onChange = vi.fn();
  render(
    <ColumnsPicker
      columns={COLUMNS}
      visibleColumns={new Set(visible)}
      onChange={onChange}
      storageKey={STORAGE_KEY}
    />,
  );
  return { onChange };
}

describe("ColumnsPicker", () => {
  beforeEach(() => {
    // Reset localStorage between tests so persistence cases don't leak.
    window.localStorage.clear();
  });

  it("renders the trigger with the EN i18n label by default", () => {
    setup();
    expect(screen.getByTestId("columns-picker-trigger")).toHaveTextContent(
      "Columns",
    );
  });

  it("renders the trigger with the active language i18n label", async () => {
    await i18n.changeLanguage("ko");
    try {
      setup();
      expect(screen.getByTestId("columns-picker-trigger")).toHaveTextContent(
        "컬럼",
      );
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("renders one row per column with testid + data-required", async () => {
    setup();
    await userEvent.click(screen.getByTestId("columns-picker-trigger"));
    const cveRow = await waitFor(() =>
      screen.getByTestId("columns-picker-option-cve_id"),
    );
    // Required columns surface a hint label so the user understands why the
    // checkbox is disabled.
    expect(cveRow).toHaveAttribute("data-required", "true");
    expect(
      screen.getByTestId("columns-picker-required-hint-cve_id"),
    ).toHaveTextContent("Always visible");
    // Optional columns expose data-required=false.
    expect(
      screen.getByTestId("columns-picker-option-cvss"),
    ).toHaveAttribute("data-required", "false");
  });

  it("toggling an optional column fires onChange with the next visible set", async () => {
    const { onChange } = setup();
    await userEvent.click(screen.getByTestId("columns-picker-trigger"));
    const cvss = await waitFor(() =>
      screen.getByTestId("columns-picker-option-cvss"),
    );
    await userEvent.click(cvss);
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as Set<string>;
    expect(next.has("cvss")).toBe(false);
    // Required ids must still be in the next set.
    expect(next.has("cve_id")).toBe(true);
    expect(next.has("severity")).toBe(true);
  });

  it("does not toggle a required column even when clicked", async () => {
    const { onChange } = setup();
    await userEvent.click(screen.getByTestId("columns-picker-trigger"));
    const cveRow = await waitFor(() =>
      screen.getByTestId("columns-picker-option-cve_id"),
    );
    await userEvent.click(cveRow);
    // Radix renders the disabled item as data-disabled; clicking is a no-op.
    expect(onChange).not.toHaveBeenCalled();
  });

  it("persists the next visible set to localStorage under storageKey", async () => {
    setup();
    await userEvent.click(screen.getByTestId("columns-picker-trigger"));
    const epss = await waitFor(() =>
      screen.getByTestId("columns-picker-option-epss"),
    );
    await userEvent.click(epss);
    const raw = window.localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const stored = JSON.parse(raw as string) as string[];
    // EPSS got toggled off → not in the stored visibility array.
    expect(stored).not.toContain("epss");
    expect(stored).toContain("cve_id");
    expect(stored).toContain("severity");
  });

  it("loadInitialVisibility returns the full set when storage is empty", () => {
    const next = loadInitialVisibility("column-visibility:does-not-exist", COLUMNS);
    expect(next.size).toBe(COLUMNS.length);
    for (const c of COLUMNS) expect(next.has(c.id)).toBe(true);
  });

  it("loadInitialVisibility re-unions required ids even if storage hid them", () => {
    // Simulate a stale entry from a previous schema where the user (or a
    // bug) somehow persisted a set missing the required identity columns.
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(["component", "cvss"]),
    );
    const next = loadInitialVisibility(STORAGE_KEY, COLUMNS);
    // Required ids are forced back in.
    expect(next.has("cve_id")).toBe(true);
    expect(next.has("severity")).toBe(true);
    // The previously-hidden optional column stays hidden.
    expect(next.has("epss")).toBe(false);
  });

  it("loadInitialVisibility filters out ids no longer in the catalog", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(["component", "cvss", "removed_column"]),
    );
    const next = loadInitialVisibility(STORAGE_KEY, COLUMNS);
    expect(next.has("removed_column")).toBe(false);
  });
});
