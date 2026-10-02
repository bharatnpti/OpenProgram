import { createContext, useContext } from "react";

export type ViewingDateValue = {
  /** The day every dated read asks for: today, or the past day being viewed. */
  asOf: string;
  /** Today, as `todayIso` gives it. */
  today: string;
  /** Looking at a past day. Nothing may be changed while this is true. */
  isPast: boolean;
  /** The viewing date as people read it, e.g. "Fri 25 Sep". */
  label: string;
  /** This screen follows the viewing date. Admin always shows current state. */
  applies: boolean;
  /** View a past day, or go back to today with null. Invalid days are ignored. */
  setViewingDate: (next: string | null) => void;
};

export const ViewingDateContext = createContext<ViewingDateValue | null>(null);

/** One line on why a change is off while a past day is being viewed. */
export function readOnlyReason(label: string): string {
  return `You're viewing ${label}. Go back to today to make changes.`;
}

export function useViewingDate(): ViewingDateValue {
  const context = useContext(ViewingDateContext);
  if (!context) {
    throw new Error("useViewingDate must be used within ViewingDateProvider");
  }
  return context;
}
