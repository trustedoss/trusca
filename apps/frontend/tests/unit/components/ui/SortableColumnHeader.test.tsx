/**
 * SortableColumnHeader — W4-B-prep shared sort primitive.
 *
 * Verifies the unset → asc → desc → unset click cycle, the announced sort
 * state, and that only the active column gets the active style. URL state
 * lives in the caller; this primitive just emits `next`.
 *
 * G0-5 — four of these tests used to assert `aria-sort` on the button. That
 * attribute is invalid on a `button` (allowed only on columnheader /
 * rowheader), so the assertions kept an axe `aria-allowed-attr` violation
 * frozen in the ratchet: the tests were green *because* they asserted the
 * defect. They now assert what actually reaches a screen reader — the
 * accessible name — and that no `aria-sort` comes back.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  SortableColumnHeader,
  nextSortState,
  type SortState,
} from "@/components/ui/sortable-column-header";

describe("nextSortState", () => {
  it("returns asc on first click of an unsorted column", () => {
    expect(nextSortState("name", null)).toEqual({ key: "name", order: "asc" });
  });

  it("returns asc when switching to a different column", () => {
    expect(
      nextSortState("name", { key: "severity", order: "desc" }),
    ).toEqual({ key: "name", order: "asc" });
  });

  it("returns desc after asc on the same column", () => {
    expect(nextSortState("name", { key: "name", order: "asc" })).toEqual({
      key: "name",
      order: "desc",
    });
  });

  it("clears (null) after desc on the same column", () => {
    expect(nextSortState("name", { key: "name", order: "desc" })).toBeNull();
  });
});

describe("SortableColumnHeader", () => {
  function setup(currentSort: SortState | null) {
    const onSort = vi.fn();
    render(
      <SortableColumnHeader
        column="name"
        label="Component"
        currentSort={currentSort}
        onSort={onSort}
      />,
    );
    return { onSort };
  }

  it("renders as a button and announces 'not sorted' when unsorted", () => {
    setup(null);
    const btn = screen.getByTestId("column-header-name");
    expect(btn.tagName.toLowerCase()).toBe("button");
    expect(btn).toHaveAccessibleName(
      "Component — not sorted — click to sort ascending",
    );
    expect(btn).toHaveAttribute("data-sort-order", "none");
  });

  it("announces 'sorted ascending' + data-sort-order=asc when active asc", () => {
    setup({ key: "name", order: "asc" });
    const btn = screen.getByTestId("column-header-name");
    expect(btn).toHaveAccessibleName(
      "Component — sorted ascending — click to sort descending",
    );
    expect(btn).toHaveAttribute("data-sort-order", "asc");
  });

  it("announces 'sorted descending' when active desc", () => {
    setup({ key: "name", order: "desc" });
    const btn = screen.getByTestId("column-header-name");
    expect(btn).toHaveAccessibleName(
      "Component — sorted descending — click to clear sort",
    );
    expect(btn).toHaveAttribute("data-sort-order", "desc");
  });

  it("treats the header as unsorted when another column owns the active sort", () => {
    setup({ key: "severity", order: "asc" });
    const btn = screen.getByTestId("column-header-name");
    expect(btn).toHaveAccessibleName(
      "Component — not sorted — click to sort ascending",
    );
    expect(btn).toHaveAttribute("data-sort-order", "none");
  });

  // The regression guard. `aria-sort` on a `button` is invalid ARIA, and the
  // callers are div tables with no columnheader to move it to, so the correct
  // state for this primitive is "no aria-sort at all". Without this the axe
  // ratchet is the only thing standing between a re-added attribute and main,
  // and it only runs on the two screens that happen to be in the scan set —
  // ComponentsTab uses this same primitive and is not scanned.
  it("carries no aria-sort in any state (invalid on role=button)", () => {
    for (const state of [
      null,
      { key: "name", order: "asc" as const },
      { key: "name", order: "desc" as const },
    ]) {
      const { unmount } = render(
        <SortableColumnHeader
          column="name"
          label="Component"
          currentSort={state}
          onSort={vi.fn()}
        />,
      );
      expect(screen.getByTestId("column-header-name")).not.toHaveAttribute(
        "aria-sort",
      );
      unmount();
    }
  });

  it("cycles unsorted → asc on first click", async () => {
    const user = userEvent.setup();
    const { onSort } = setup(null);
    await user.click(screen.getByTestId("column-header-name"));
    expect(onSort).toHaveBeenCalledWith({ key: "name", order: "asc" });
  });

  it("cycles asc → desc on a second click", async () => {
    const user = userEvent.setup();
    const { onSort } = setup({ key: "name", order: "asc" });
    await user.click(screen.getByTestId("column-header-name"));
    expect(onSort).toHaveBeenCalledWith({ key: "name", order: "desc" });
  });

  it("cycles desc → null (unsorted) on a third click", async () => {
    const user = userEvent.setup();
    const { onSort } = setup({ key: "name", order: "desc" });
    await user.click(screen.getByTestId("column-header-name"));
    expect(onSort).toHaveBeenCalledWith(null);
  });

  it("respects a custom testId override", () => {
    const onSort = vi.fn();
    render(
      <SortableColumnHeader
        column="name"
        label="Component"
        currentSort={null}
        onSort={onSort}
        testId="my-custom-header"
      />,
    );
    expect(screen.getByTestId("my-custom-header")).toBeInTheDocument();
    expect(screen.queryByTestId("column-header-name")).not.toBeInTheDocument();
  });
});
