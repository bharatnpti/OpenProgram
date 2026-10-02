import { History } from "lucide-react";

import { useViewingDate } from "../../app/viewingDate";
import { cn } from "../../lib/utils";

/**
 * The header's date picker: which day the console shows.
 *
 * One control in the header rather than one per page, because the date is the
 * console's, not a page's: it stays put while you move between screens, and the
 * header is sticky, so a past day stays in view while you scroll. Hidden where
 * the date doesn't apply (Admin).
 */
export function ViewingDateControl() {
  const { asOf, today, isPast, applies, setViewingDate } = useViewingDate();
  if (!applies) return null;
  return (
    <label
      className={cn(
        "flex h-12 shrink-0 items-center gap-2 rounded-full border pl-4 pr-3",
        isPast
          ? "border-rag-amber bg-rag-amber-bg text-rag-amber-deep"
          : "border-grey-border bg-white text-ink hover:border-grey-disabled",
      )}
    >
      <span className="hidden text-[13px] font-bold xl:inline">{isPast ? "Viewing" : "Today"}</span>
      <input
        type="date"
        aria-label="Viewing date"
        value={asOf}
        max={today}
        onChange={(event) => {
          // A half-typed or cleared value is empty; keep the date until it's whole.
          if (event.target.value) setViewingDate(event.target.value);
        }}
        className="h-8 bg-transparent text-[14px] font-bold outline-none"
      />
    </label>
  );
}

/** Says, on every screen, that a past day is being viewed and nothing can change. */
export function ViewingDateBanner() {
  const { isPast, label, setViewingDate } = useViewingDate();
  if (!isPast) return null;
  return (
    <div
      role="status"
      className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-rag-amber-bg px-5 py-3 text-[14px] text-ink"
    >
      <History size={16} className="shrink-0 text-rag-amber" />
      <span className="font-bold">Viewing {label}</span>
      <span className="text-grey-body">Read-only. Nothing can be changed while you look back.</span>
      <button
        type="button"
        onClick={() => setViewingDate(null)}
        className="ml-auto font-bold text-magenta hover:underline"
      >
        Back to today
      </button>
    </div>
  );
}
