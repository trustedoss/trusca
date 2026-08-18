import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProjectListToolbar } from "@/features/projects/components/ProjectListToolbar";

describe("ProjectListToolbar", () => {
  function setup() {
    const onQueryChange = vi.fn();
    const onStatusChange = vi.fn();
    const onSortChange = vi.fn();
    const onDistributionChange = vi.fn();
    render(
      <ProjectListToolbar
        query=""
        onQueryChange={onQueryChange}
        status="all"
        onStatusChange={onStatusChange}
        sort="name"
        onSortChange={onSortChange}
        distribution={null}
        onDistributionChange={onDistributionChange}
      />,
    );
    return {
      onQueryChange,
      onStatusChange,
      onSortChange,
      onDistributionChange,
    };
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

  it("starts with no distribution filter, so nothing is narrowed", () => {
    // The default the whole feature rests on: most projects will have no
    // distribution model on the day this ships, and a filter that applied by
    // itself would make the portfolio look like projects had gone missing.
    setup();

    expect(screen.getByTestId("project-distribution-filter")).toHaveValue("");
  });

  it("reports a chosen model, and null when cleared", async () => {
    const { onDistributionChange } = setup();
    const select = screen.getByTestId("project-distribution-filter");

    await userEvent.selectOptions(select, "saas");
    expect(onDistributionChange).toHaveBeenLastCalledWith("saas");

    await userEvent.selectOptions(select, "");
    // Null, not "": the URL and the query key both read null as "no filter",
    // and an empty string would make a second cache entry for one view.
    expect(onDistributionChange).toHaveBeenLastCalledWith(null);
  });

  it("offers the projects that have not said how they ship", () => {
    // Equality never matches NULL, so this needs its own option. It is also
    // the question somebody asks while filling the attribute in.
    setup();

    expect(
      screen.getByRole("option", { name: /not decided/i }),
    ).toBeInTheDocument();
  });

});
