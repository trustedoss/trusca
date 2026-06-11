import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Composite skeletons — W12-D.
 *
 * The base `Skeleton` is a single bar. Loading tables previously rendered one
 * full-width bar per row (`<td colSpan>`), which does not anticipate the
 * column rhythm the real rows will have. `TableRowsSkeleton` renders proper
 * per-column cells with varied widths so the loading state has the same shape
 * as the populated table — the content "settles in" instead of reflowing.
 *
 * Rows are `aria-hidden`; the table itself already carries `aria-busy` so
 * assistive tech announces "busy" without reading skeleton cells.
 */
export interface TableRowsSkeletonProps {
  /** Number of placeholder rows. Defaults to 5. */
  rows?: number;
  /**
   * One Tailwind width class per column (e.g. `["w-40", "w-16", "w-20"]`),
   * sized to roughly match the real column content so the skeleton reads as
   * the same table.
   */
  columns: string[];
  /** Optional extra classes on each `<tr>`. */
  className?: string;
}

export function TableRowsSkeleton({
  rows = 5,
  columns,
  className,
}: TableRowsSkeletonProps) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr
          key={`skeleton-row-${r}`}
          aria-hidden="true"
          className={cn("border-b", className)}
        >
          {columns.map((width, c) => (
            <td key={`skeleton-cell-${c}`} className="px-3 py-2 first:px-6">
              <Skeleton className={cn("h-4", width)} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
