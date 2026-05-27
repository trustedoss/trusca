/**
 * SortableColumnHeader — W4-B-prep shared sort primitive.
 *
 * Verifies the unset → asc → desc → unset click cycle, the aria-sort
 * contract, and that only the active column gets the active style. URL state
 * lives in the caller; this primitive just emits `next`.
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

  it("renders as a button with aria-sort=none and order=none when unsorted", () => {
    setup(null);
    const btn = screen.getByTestId("column-header-name");
    expect(btn.tagName.toLowerCase()).toBe("button");
    expect(btn).toHaveAttribute("aria-sort", "none");
    expect(btn).toHaveAttribute("data-sort-order", "none");
  });

  it("reflects aria-sort=ascending + data-sort-order=asc when active asc", () => {
    setup({ key: "name", order: "asc" });
    const btn = screen.getByTestId("column-header-name");
    expect(btn).toHaveAttribute("aria-sort", "ascending");
    expect(btn).toHaveAttribute("data-sort-order", "asc");
  });

  it("reflects aria-sort=descending when active desc", () => {
    setup({ key: "name", order: "desc" });
    const btn = screen.getByTestId("column-header-name");
    expect(btn).toHaveAttribute("aria-sort", "descending");
    expect(btn).toHaveAttribute("data-sort-order", "desc");
  });

  it("treats the header as unsorted when another column owns the active sort", () => {
    setup({ key: "severity", order: "asc" });
    const btn = screen.getByTestId("column-header-name");
    expect(btn).toHaveAttribute("aria-sort", "none");
    expect(btn).toHaveAttribute("data-sort-order", "none");
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
