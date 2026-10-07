import { createContext, useContext } from "react";

import { dayWords } from "../lib/viewingDate";

export type ViewingDateValue = {
  /** The past day picked in the header, or null for today. Kept in the URL on every screen. */
  chosen: string | null;
  /** The day this screen's reads ask for: the chosen day, or null (today, and always on Admin). */
  asOf: string | null;
  /** Today: the server's day once a read has said it, else `guessToday`. */
  today: string;
  /** The chosen day as people read it ("Mon 28 Sept"), or null for today. */
  label: string | null;
  /** A past day is shown on this screen, so nothing may be changed. */
  readOnly: boolean;
  /** Why changes are off, in one line; null while they are allowed. */
  reason: string | null;
  /** Pick a past day, or go back to today with null. Invalid and future days are ignored. */
  setViewingDate: (next: string | null) => void;
};

export const ViewingDateContext = createContext<ViewingDateValue | null>(null);

export function useViewingDate(): ViewingDateValue {
  const context = useContext(ViewingDateContext);
  if (!context) {
    throw new Error("useViewingDate must be used within ViewingDateProvider");
  }
  return context;
}

/**
 * For every control that changes something: while a past day is shown it is
 * off, and `reason` says why in plain words (put it in the button's `title`,
 * or beside the button). Outside the provider nothing is read-only.
 *
 *   const { readOnly, reason } = useReadOnly();
 *   <Pill disabled={readOnly || save.isPending} title={reason ?? undefined}>Save</Pill>
 *
 * The API client refuses a change sent anyway, with the same reason, so a
 * button that forgets this fails safe with a toast instead of writing.
 */
export function useReadOnly(): { readOnly: boolean; reason: string | null } {
  const context = useContext(ViewingDateContext);
  return context?.readOnly
    ? { readOnly: true, reason: context.reason }
    : { readOnly: false, reason: null };
}

/** "today", or "on Mon 28 Sept" while this screen shows a past day: for "Check-ins today". */
export function useDayWords(): string {
  const context = useContext(ViewingDateContext);
  return dayWords(context?.asOf ? context.label : null);
}

/**
 * The day this screen's numbers are for: the past day chosen, else today as
 * the server counts it. For the date at the top of Today.
 */
export function useShownDay(): string | null {
  const context = useContext(ViewingDateContext);
  return context ? (context.chosen ?? context.today) : null;
}
