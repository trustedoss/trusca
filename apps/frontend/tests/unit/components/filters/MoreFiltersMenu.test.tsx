/**
 * MoreFiltersMenu — unit tests (W9 #52).
 *
 * Validates the "+ Add filter" generic discovery affordance that lives next
 * to inline filter chips on the Components and Vulnerabilities tabs:
 *   - the trigger renders the `filters.more_filters.trigger` label
 *     ("Add filter" in EN, "필터 추가" in KO) and stays out of the DOM when
 *     the catalog is empty,
 *   - opening the dropdown renders one option row per available filter with
 *     `data-testid="<base>-option-<id>"` so callers can target them,
 *   - clicking an option fires `onSelect(filterId)` exactly once, and
 *   - rows for active filters carry `data-active="true"` so the user can see
 *     which facets are already turned on without re-reading the toolbar.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MoreFiltersMenu } from "@/components/filters/MoreFiltersMenu";
import i18n from "@/lib/i18n";

const OPTIONS = [
  { id: "license_category", label: "License category" },
  { id: "name_search", label: "Component name" },
  { id: "discovered_after", label: "Discovered after" },
];

function setup(options = {
  availableFilters: OPTIONS,
  activeFilterIds: new Set<string>(),
}) {
  const onSelect = vi.fn();
  render(
    <MoreFiltersMenu
      availableFilters={options.availableFilters}
      activeFilterIds={options.activeFilterIds}
      onSelect={onSelect}
    />,
  );
  return { onSelect };
}

describe("MoreFiltersMenu", () => {
  it("renders the trigger with the EN i18n label by default", () => {
    setup();
    expect(screen.getByTestId("more-filters-trigger")).toHaveTextContent(
      "Add filter",
    );
  });

  it("renders the trigger with the active language i18n label", async () => {
    await i18n.changeLanguage("ko");
    try {
      setup();
      expect(screen.getByTestId("more-filters-trigger")).toHaveTextContent(
        "필터 추가",
      );
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("returns null when the available filter catalog is empty", () => {
    const { container } = render(
      <MoreFiltersMenu
        availableFilters={[]}
        activeFilterIds={new Set()}
        onSelect={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("opens the dropdown and renders one option row per available filter", async () => {
    setup();
    await userEvent.click(screen.getByTestId("more-filters-trigger"));
    await waitFor(() => {
      const options = screen.getAllByRole("menuitem");
      expect(options).toHaveLength(3);
    });
    // Each option row exposes a stable testid for callers to target.
    expect(
      screen.getByTestId("more-filters-trigger-option-license_category"),
    ).toHaveTextContent("License category");
    expect(
      screen.getByTestId("more-filters-trigger-option-name_search"),
    ).toHaveTextContent("Component name");
  });

  it("fires onSelect with the clicked filter id", async () => {
    const { onSelect } = setup();
    await userEvent.click(screen.getByTestId("more-filters-trigger"));
    const row = await waitFor(() =>
      screen.getByTestId("more-filters-trigger-option-license_category"),
    );
    await userEvent.click(row);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("license_category");
  });

  it("marks rows for active filters with data-active=true", async () => {
    setup({
      availableFilters: OPTIONS,
      activeFilterIds: new Set(["name_search"]),
    });
    await userEvent.click(screen.getByTestId("more-filters-trigger"));
    const row = await waitFor(() =>
      screen.getByTestId("more-filters-trigger-option-name_search"),
    );
    expect(row).toHaveAttribute("data-active", "true");
    const inactive = screen.getByTestId(
      "more-filters-trigger-option-license_category",
    );
    expect(inactive).toHaveAttribute("data-active", "false");
  });

  it("uses a custom test id when provided so two menus can co-exist", async () => {
    const onSelect = vi.fn();
    render(
      <MoreFiltersMenu
        availableFilters={OPTIONS}
        activeFilterIds={new Set()}
        onSelect={onSelect}
        testId="vulns-more-filters-trigger"
      />,
    );
    expect(
      screen.getByTestId("vulns-more-filters-trigger"),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByTestId("vulns-more-filters-trigger"),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("vulns-more-filters-trigger-option-name_search"),
      ).toBeInTheDocument(),
    );
  });
});
