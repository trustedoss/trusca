/**
 * TableRowsSkeleton — unit tests for W12-D.
 *
 * Coverage:
 *   - Renders `rows` placeholder rows, each with one cell per `columns` entry.
 *   - Defaults to 5 rows.
 *   - Rows are aria-hidden (the table carries aria-busy).
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TableRowsSkeleton } from "@/components/ui/skeletons";

function renderRows(props: { rows?: number; columns: string[] }) {
  return render(
    <table>
      <tbody>
        <TableRowsSkeleton {...props} />
      </tbody>
    </table>,
  );
}

describe("TableRowsSkeleton", () => {
  it("renders the requested rows with one cell per column", () => {
    const { container } = renderRows({
      rows: 3,
      columns: ["w-40", "w-16", "w-20"],
    });
    const rows = container.querySelectorAll("tbody > tr");
    expect(rows).toHaveLength(3);
    expect(rows[0].querySelectorAll("td")).toHaveLength(3);
  });

  it("defaults to 5 rows", () => {
    const { container } = renderRows({ columns: ["w-20"] });
    expect(container.querySelectorAll("tbody > tr")).toHaveLength(5);
  });

  it("marks placeholder rows aria-hidden", () => {
    const { container } = renderRows({ columns: ["w-20"] });
    expect(container.querySelector("tr")?.getAttribute("aria-hidden")).toBe(
      "true",
    );
  });
});
