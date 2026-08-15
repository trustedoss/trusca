/**
 * Grid rows are real rows.
 *
 * Every virtualized grid in the project detail page claims `role="table"`.
 * That claim brings obligations: a `row` owns `cell`s, `row` is not a role a
 * `<button>` may carry, and a button spanning six columns is not a cell.
 *
 * A3 added the table roles to the compliance and obligations grids and got
 * exactly that wrong, `role="row"` with no cells under it, and one row that
 * was a `<button>`. Nothing caught it: the CI axe gate walks a fixed list of
 * representative screens and neither tab is on it, and the E2E table-semantics
 * spec asserted headers and row indices but not cells.
 *
 * So this runs axe over the rendered rows themselves. It is a unit test rather
 * than a screen scan because the defect lives in one component's markup, and
 * because it should fail in the same second the markup changes.
 */
import { render } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";

/**
 * Wrap a row in the minimum a `row` needs to be legal, so axe judges the row
 * and not its surroundings.
 */
function tableAround(row: React.ReactNode) {
  return (
    <div role="table" aria-label="t" aria-rowcount={2}>
      <div role="rowgroup">{row}</div>
    </div>
  );
}

async function violationsIn(container: HTMLElement): Promise<string[]> {
  const results = await axe.run(container, {
    runOnly: {
      type: "rule",
      values: [
        "aria-required-children",
        "aria-required-parent",
        "aria-allowed-role",
        "aria-allowed-attr",
      ],
    },
  });
  return results.violations.map((v) => `${v.id}: ${v.nodes[0]?.failureSummary ?? ""}`);
}

describe("grid row semantics", () => {
  it("accepts a row whose columns are cells", async () => {
    const { container } = render(
      tableAround(
        <div role="row" aria-rowindex={2}>
          <span role="cell">a</span>
          <span role="cell">b</span>
        </div>,
      ),
    );
    expect(await violationsIn(container)).toEqual([]);
  });

  it("rejects a row with no cells", async () => {
    // The shape A3 shipped first.
    const { container } = render(
      tableAround(
        <div role="row" aria-rowindex={2}>
          <span>a</span>
          <span>b</span>
        </div>,
      ),
    );
    expect(await violationsIn(container)).not.toEqual([]);
  });

  it("rejects a button carrying role=row", async () => {
    const { container } = render(
      tableAround(
        <button type="button" role="row" aria-rowindex={2}>
          <span role="cell">a</span>
        </button>,
      ),
    );
    expect(await violationsIn(container)).not.toEqual([]);
  });

  it("rejects a button spanning several columns inside a row", async () => {
    const { container } = render(
      tableAround(
        <div role="row" aria-rowindex={2}>
          <button type="button">
            <span role="cell">a</span>
            <span role="cell">b</span>
          </button>
        </div>,
      ),
    );
    expect(await violationsIn(container)).not.toEqual([]);
  });

  it("accepts a button inside a cell", async () => {
    // The shape both grids use now: the pointer target is the row, the
    // keyboard target is a button living in the first cell.
    const { container } = render(
      tableAround(
        <div role="row" aria-rowindex={2}>
          <span role="cell">
            <button type="button">open</button>
          </span>
          <span role="cell">b</span>
        </div>,
      ),
    );
    expect(await violationsIn(container)).toEqual([]);
  });
});

describe("the grids' own rows", () => {
  // The cases above describe the rule; these hold the two grids that broke it
  // to their actual markup. Rendering the whole tab would drag in the query
  // client, the router and four APIs, so the rows are rendered through the
  // same public components the tabs use, inside the minimum table a row needs.

  it("compliance rows pass", async () => {
    const { ComplianceGridRowForTest } = await import(
      "@/features/projects/components/ComplianceTab"
    );
    const { container } = render(
      tableAround(
        <ComplianceGridRowForTest
          row={{
            license_id: "l-1",
            license_finding_id: "lf-1",
            spdx_id: "MIT",
            license_name: "MIT License",
            category: "allowed",
            category_source: "catalog",
            category_override_source: null,
            kind: "declared",
            affected_components: [
              { component_version_id: "cv-1", name: "pkg", version: "1.0.0", purl: null },
            ],
            affected_component_count: 1,
            obligations: [],
            notice_required: false,
            conflict: null,
          }}
          rowIndex={0}
          showConflict={false}
          onSelect={() => {}}
          projectId="p1"
          teamId={null}
          projectRole="developer"
          readOnly={false}
          policy={null}
        />,
      ),
    );
    expect(await violationsIn(container)).toEqual([]);
  });

  it("obligation rows pass", async () => {
    const { ObligationRowForTest } = await import(
      "@/features/projects/components/ObligationsTab"
    );
    const { container } = render(
      tableAround(
        <ObligationRowForTest
          obligation={{
            id: "ob-1",
            license_id: "l-1",
            kind: "attribution",
            license_spdx_id: "MIT",
            license_name: "MIT License",
            license_category: "allowed",
            affected_count: 3,
            text: "Attribution required.",
            text_ko: null,
            link: null,
            updated_at: "2026-08-01T00:00:00Z",
          }}
          rowIndex={0}
          onSelect={() => {}}
        />,
      ),
    );
    expect(await violationsIn(container)).toEqual([]);
  });
});
