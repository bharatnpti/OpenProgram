import type { ScopeDeliveryResponse } from "../../api/schema";
import { dateChangeLine } from "../overall/overallWords";

type DateChange = ScopeDeliveryResponse["commitment"]["changes"][number];

/** How many changes show before the rest fold away. */
const SHOWN = 3;

/**
 * How a date moved, newest first: each change with who made it and the reason
 * they typed. The project's table says it already ("How the date moved"); a
 * pod's note was stored and shown nowhere.
 */
export function DateHistory({ changes }: { changes: DateChange[] }) {
  if (changes.length === 0) return null;
  const newest = [...changes].reverse();
  const older = newest.slice(SHOWN);
  return (
    <div className="mt-2">
      <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        How the date moved
      </p>
      <ul className="mt-1 grid gap-0.5 text-[12px] text-grey-body">
        {newest.slice(0, SHOWN).map((change) => (
          <li key={change.changed_at}>{dateChangeLine(change)}</li>
        ))}
      </ul>
      {older.length > 0 ? (
        <details className="mt-1 text-[12px] text-grey-body">
          <summary className="cursor-pointer font-bold">{older.length} earlier</summary>
          <ul className="mt-1 grid gap-0.5">
            {older.map((change) => (
              <li key={change.changed_at}>{dateChangeLine(change)}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
