import { CalendarDays, History } from "lucide-react";
import { useLocation } from "react-router-dom";

import { useViewingDate } from "../../app/viewingDate";
import { cn } from "../../lib/utils";
import { showsCurrentState, viewingDateNote } from "../../lib/viewingDate";

/**
 * Which day the console shows, picked in the header: the day belongs to the
 * console, not to one page, so it stays put while you move between screens.
 * A native date field, so it works by keyboard and on a phone's own picker.
 */
export function ViewingDateControl() {
  const { chosen, today, setViewingDate } = useViewingDate();
  return (
    <label
      className={cn(
        "flex h-10 flex-none items-center gap-2 rounded-full border pl-3 pr-2",
        chosen
          ? "border-rag-amber bg-rag-amber-bg text-rag-amber-deep"
          : "border-grey-border bg-white text-ink hover:border-grey-disabled",
      )}
    >
      <CalendarDays size={15} aria-hidden className="flex-none" />
      <span className="sr-only">Day shown</span>
      <input
        type="date"
        value={chosen ?? today}
        max={today}
        onChange={(event) => {
          // A half-typed or cleared value is empty; keep the day until it's whole.
          if (event.target.value) setViewingDate(event.target.value);
        }}
        className="h-8 w-[8.5rem] bg-transparent text-[13px] font-bold outline-none"
      />
    </label>
  );
}

/**
 * Says, on every screen, that a past day is chosen and what that means here:
 * read-only on the screens that show it, and current state on Admin.
 */
export function ViewingDateBanner() {
  const { chosen, label, setViewingDate } = useViewingDate();
  const { pathname } = useLocation();
  if (!chosen || !label) return null;
  const current = showsCurrentState(pathname);
  return (
    <div role="status" className="border-t border-rag-amber/30 bg-rag-amber-bg">
      <div className="mx-auto flex max-w-[1240px] flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-[14px] text-ink sm:px-8">
        <History size={16} aria-hidden className="flex-none text-rag-amber" />
        <span className="font-bold">
          {current ? `Other screens show ${label}` : `Viewing ${label}`}
        </span>
        <span className="text-grey-body">{viewingDateNote(pathname)}</span>
        <button
          type="button"
          onClick={() => setViewingDate(null)}
          className="ml-auto font-bold text-magenta hover:underline max-sm:min-h-11"
        >
          Back to today
        </button>
      </div>
    </div>
  );
}
