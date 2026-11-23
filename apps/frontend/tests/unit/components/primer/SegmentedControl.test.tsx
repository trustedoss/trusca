/**
 * The SegmentedControl wrapper turns Primer's index-based control into a
 * value-based one. Screens and the Playwright harness depend on: the right
 * option reads as pressed (aria-pressed and data-active agree), a click or a
 * key press hands back the option's value (not its index), each option keeps
 * its test id, and the group carries the accessible name it was given.
 */
import { TableIcon, WorkflowIcon } from "@primer/octicons-react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SegmentedControl } from "@/components/primer/SegmentedControl";

const OPTIONS = [
  { value: 7, label: "7 days", testId: "w-7" },
  { value: 30, label: "30 days", testId: "w-30" },
  { value: 90, label: "90 days", testId: "w-90" },
] as const;

function Controlled({ onChange }: { onChange?: (v: number) => void }) {
  const [value, setValue] = useState<number>(30);
  return (
    <SegmentedControl
      aria-label="Window"
      data-testid="group"
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
      options={OPTIONS.map((o) => ({ ...o }))}
    />
  );
}

describe("SegmentedControl", () => {
  it("marks only the selected option as pressed and active", () => {
    render(<Controlled />);
    expect(screen.getByTestId("w-30")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("w-30")).toHaveAttribute("data-active", "true");
    for (const id of ["w-7", "w-90"]) {
      expect(screen.getByTestId(id)).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByTestId(id)).toHaveAttribute("data-active", "false");
    }
  });

  it("hands back the option's value on click, not its index", async () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "90 days" }));
    expect(onChange).toHaveBeenCalledWith(90);
    expect(screen.getByTestId("w-90")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("w-30")).toHaveAttribute("aria-pressed", "false");
  });

  it("selects with the keyboard: Tab to an option, Enter or Space picks it", async () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByTestId("w-7")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenLastCalledWith(7);
    await user.tab();
    expect(screen.getByTestId("w-30")).toHaveFocus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenLastCalledWith(30);
  });

  it("names the group and keeps the group's test id", () => {
    render(<Controlled />);
    const group = screen.getByTestId("group");
    expect(group).toHaveAttribute("aria-label", "Window");
    expect(screen.getByRole("list", { name: "Window" })).toBe(group);
  });

  it("does not report a disabled option", async () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        aria-label="Kind"
        value="a"
        onChange={onChange}
        options={[
          { value: "a", label: "A" },
          { value: "b", label: "B", disabled: true },
        ]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "B" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("handles non-string values such as null and booleans", async () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl<boolean | null>
        aria-label="Dependency type"
        value={null}
        onChange={onChange}
        options={[
          { value: null, label: "All", testId: "all" },
          { value: true, label: "Direct", testId: "direct" },
          { value: false, label: "Transitive", testId: "transitive" },
        ]}
      />,
    );
    expect(screen.getByTestId("all")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByTestId("transitive"));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("renders an icon-only segment named by its label", async () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        aria-label="View"
        value="table"
        onChange={onChange}
        options={[
          { value: "table", label: "Table", icon: TableIcon, testId: "v-table" },
          { value: "graph", label: "Graph", icon: WorkflowIcon, testId: "v-graph" },
        ]}
      />,
    );
    const graph = screen.getByTestId("v-graph");
    expect(graph.textContent?.trim()).toBe("");
    expect(graph).toHaveAccessibleName("Graph");
    expect(screen.getByTestId("v-table")).toHaveAttribute("data-active", "true");
    expect(graph).toHaveAttribute("data-active", "false");
    await userEvent.click(graph);
    expect(onChange).toHaveBeenCalledWith("graph");
  });
});
