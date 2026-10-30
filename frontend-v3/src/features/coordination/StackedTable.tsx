import type { ReactNode } from "react";

/*
 * A table that is a table from tablet width up and a stack of cards on a phone:
 * each row a card, each cell a line with its column's name in front, so no
 * page scrolls sideways at 390 px. The header stays for screen readers.
 */

/** A body cell: a table cell, or a line of the row's card on a phone. */
export const stackCell =
  "border-b border-grey-border px-3 py-2.5 align-top text-[13px] max-md:block max-md:border-0 max-md:px-0 max-md:py-1";

/** A header cell; the header row is visually hidden on a phone. */
export const stackHead =
  "border-b border-grey-border bg-grey-header px-3 py-2 text-left text-[11px] font-bold uppercase tracking-wider text-grey-secondary whitespace-nowrap";

/** A row: a card on a phone. */
export const stackRow =
  "max-md:block max-md:rounded-2xl max-md:border max-md:border-grey-border max-md:bg-(--op-viz-surface) max-md:p-3";

/** The column's name in front of a cell's value, on a phone only. */
export function CellLabel({ children }: { children: string }) {
  return (
    <span className="mr-1.5 text-[11px] font-bold uppercase tracking-wider text-grey-secondary md:hidden">
      {children}
    </span>
  );
}

/** The table, its caption for screen readers, and the column names. */
export function StackedTable({
  caption,
  columns,
  children,
}: {
  caption: string;
  columns: string[];
  children: ReactNode;
}) {
  return (
    <div className="md:overflow-hidden md:rounded-2xl md:border md:border-grey-border">
      <table className="w-full border-collapse max-md:block">
        <caption className="sr-only">{caption}</caption>
        <thead className="max-md:sr-only">
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className={stackHead}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="max-md:grid max-md:gap-3">{children}</tbody>
      </table>
    </div>
  );
}
