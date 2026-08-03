/**
 * MultiSelect — unit tests.
 *
 * Validates the reusable app-i18n checkbox dropdown that replaced the native
 * `<select multiple>` filters in the project-detail toolbars:
 *   - the trigger shows the placeholder when nothing is selected,
 *   - the trigger shows the app-i18n "{{count}} selected" label (NOT the OS
 *     locale's native "0개 선택됨") once items are selected, in the active
 *     language,
 *   - opening the dropdown renders one checkbox row per option with the right
 *     testid + data-value,
 *   - toggling a row calls onChange with the next array, preserving option
 *     order,
 *   - toggling an already-selected row removes it,
 *   - "Clear" empties the selection.
 *
 * Radix DropdownMenu's jsdom DOM-API gaps are polyfilled globally in
 * tests/setup.ts, mirroring ReleaseSwitcher.test.tsx.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MultiSelect } from "@/components/ui/multi-select";
import i18n from "@/lib/i18n";

const OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "low", label: "Low" },
];

function setup(selected: string[] = []) {
  const onChange = vi.fn();
  render(
    <MultiSelect
      testId="severity-filter"
      label="Severity"
      placeholder="All"
      options={OPTIONS}
      selected={selected}
      onChange={onChange}
    />,
  );
  return { onChange };
}

describe("MultiSelect", () => {
  it("shows the placeholder on the trigger when nothing is selected", () => {
    setup([]);
    expect(screen.getByTestId("severity-filter")).toHaveTextContent("All");
  });

  it("shows the app-i18n count label (not the OS native widget) when selected", () => {
    setup(["critical", "high"]);
    // English app language → "2 selected", never the browser's "0개 선택됨".
    expect(screen.getByTestId("severity-filter")).toHaveTextContent(
      "2 selected",
    );
  });

  it("renders the count label in the active app language, not the OS locale", async () => {
    await i18n.changeLanguage("ko");
    try {
      setup(["critical"]);
      expect(screen.getByTestId("severity-filter")).toHaveTextContent(
        "1개 선택됨",
      );
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("renders one checkbox row per option with testid + data-value", async () => {
    setup([]);
    await userEvent.click(screen.getByTestId("severity-filter"));
    const options = await waitFor(() => {
      const found = screen.getAllByTestId("severity-filter-option");
      expect(found).toHaveLength(3);
      return found;
    });
    expect(options.map((el) => el.getAttribute("data-value"))).toEqual([
      "critical",
      "high",
      "low",
    ]);
  });

  it("toggling an option calls onChange with the next array in option order", async () => {
    const { onChange } = setup(["low"]);
    await userEvent.click(screen.getByTestId("severity-filter"));
    const critical = await waitFor(() => {
      const option = screen
        .getAllByTestId("severity-filter-option")
        .find((el) => el.getAttribute("data-value") === "critical");
      if (!option) throw new Error("critical option not mounted");
      return option;
    });
    await userEvent.click(critical);
    // critical comes before low in the option order → ["critical", "low"].
    expect(onChange).toHaveBeenCalledWith(["critical", "low"]);
  });

  it("toggling an already-selected option removes it", async () => {
    const { onChange } = setup(["critical", "high"]);
    await userEvent.click(screen.getByTestId("severity-filter"));
    const critical = await waitFor(() => {
      const option = screen
        .getAllByTestId("severity-filter-option")
        .find((el) => el.getAttribute("data-value") === "critical");
      if (!option) throw new Error("critical option not mounted");
      return option;
    });
    await userEvent.click(critical);
    expect(onChange).toHaveBeenCalledWith(["high"]);
  });

  it("the Clear affordance empties the selection", async () => {
    const { onChange } = setup(["critical"]);
    await userEvent.click(screen.getByTestId("severity-filter"));
    const clear = await waitFor(() =>
      screen.getByTestId("severity-filter-clear"),
    );
    await userEvent.click(clear);
    expect(onChange).toHaveBeenCalledWith([]);
  });
});
