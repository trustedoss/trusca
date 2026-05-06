import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProjectListToolbar } from "@/features/projects/components/ProjectListToolbar";

describe("ProjectListToolbar", () => {
  function setup() {
    const onQueryChange = vi.fn();
    const onStatusChange = vi.fn();
    const onSortChange = vi.fn();
    render(
      <ProjectListToolbar
        query=""
        onQueryChange={onQueryChange}
        status="all"
        onStatusChange={onStatusChange}
        sort="name"
        onSortChange={onSortChange}
      />,
    );
    return { onQueryChange, onStatusChange, onSortChange };
  }

  it("renders search, status filter, and sort controls", () => {
    setup();
    expect(screen.getByTestId("project-search")).toBeInTheDocument();
    expect(screen.getByTestId("project-status-filter")).toBeInTheDocument();
    expect(screen.getByTestId("project-sort")).toBeInTheDocument();
  });

  it("dispatches onQueryChange as the user types", async () => {
    const { onQueryChange } = setup();
    await userEvent.type(screen.getByTestId("project-search"), "a");
    expect(onQueryChange).toHaveBeenCalledWith("a");
  });

  it("dispatches onStatusChange when the filter changes", async () => {
    const { onStatusChange } = setup();
    await userEvent.selectOptions(
      screen.getByTestId("project-status-filter"),
      "running",
    );
    expect(onStatusChange).toHaveBeenCalledWith("running");
  });

  it("dispatches onSortChange when the sort changes", async () => {
    const { onSortChange } = setup();
    await userEvent.selectOptions(
      screen.getByTestId("project-sort"),
      "latest_scan",
    );
    expect(onSortChange).toHaveBeenCalledWith("latest_scan");
  });
});
