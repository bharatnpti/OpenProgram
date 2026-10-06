// Pure rules for the console's viewing date, imports with extensions only so
// `node --test` can run them. The same rules as frontend-v2's lib/viewingDate.ts,
// except that Admin keeps the chosen day in the URL instead of dropping it.
import { formatDay } from "./format.ts";

/**
 * The viewing date lives in the URL as `?asOf=YYYY-MM-DD`, and only for a past
 * day: today is the absence of the param, so a plain link always opens on today.
 */
export const VIEWING_DATE_PARAM = "asOf";

export type NavigationKind = "POP" | "PUSH" | "REPLACE";

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Today's date as the console counts it: the UTC calendar day, as frontend-v2
 * does, so both consoles agree on which day is "today".
 */
export function todayIso(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/**
 * The past day a `?asOf=` value names, or null for today.
 *
 * Null covers every value that is not a real past day: absent or empty, not
 * YYYY-MM-DD, not on the calendar (2026-02-30), today itself, or later than
 * today. ISO dates order the same as strings, so the comparison is a string one.
 */
export function parseViewingDate(raw: string | null, today: string): string | null {
  if (!raw) return null;
  const match = ISO_DATE.exec(raw);
  if (!match) return null;
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return raw < today ? raw : null;
}

/**
 * Router state marking a navigation that chose today on purpose.
 *
 * "Back to today" drops the param exactly as an in-app link does, so without
 * the mark it would be carried straight back to the past day it left.
 */
export const TODAY_CHOSEN_STATE = "viewingDateToday";

export function choseToday(state: unknown): boolean {
  return (
    typeof state === "object" &&
    state !== null &&
    (state as Record<string, unknown>)[TODAY_CHOSEN_STATE] === true
  );
}

export function markTodayChosen(state: unknown): Record<string, unknown> {
  const current = typeof state === "object" && state !== null ? state : {};
  return { ...current, [TODAY_CHOSEN_STATE]: true };
}

/**
 * Which past day is chosen (null for today), and the `asOf` value the URL
 * should carry.
 *
 * In-app links don't know about the date, so a link followed while a past day
 * is chosen (PUSH or REPLACE without the param) carries that day forward,
 * unless the navigation chose today. Back and forward (POP) restore exactly
 * what that history entry said, so an entry without the param is today.
 */
export function resolveViewingDate(input: {
  raw: string | null;
  carried: string | null;
  navigation: NavigationKind;
  choseToday: boolean;
  today: string;
}): { past: string | null; param: string | null } {
  if (input.raw !== null) {
    const past = parseViewingDate(input.raw, input.today);
    return { past, param: past };
  }
  if (input.navigation === "POP" || input.choseToday) return { past: null, param: null };
  const past = parseViewingDate(input.carried, input.today);
  return { past, param: past };
}

/** A copy of `search` with the viewing date set, or removed for today. */
export function withViewingDateParam(search: string, past: string | null): string {
  const params = new URLSearchParams(search);
  if (past) {
    params.set(VIEWING_DATE_PARAM, past);
  } else {
    params.delete(VIEWING_DATE_PARAM);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** "Fri 25 Sept", the way every v3 screen writes a day, with the year when it isn't this year's. */
export function formatDayLabel(isoDate: string, today: string): string {
  const day = formatDay(isoDate);
  return isoDate.slice(0, 4) === today.slice(0, 4) ? day : `${day} ${isoDate.slice(0, 4)}`;
}

/** Admin edits the current configuration, so it never reads or writes a past day. */
export function showsCurrentState(pathname: string): boolean {
  return pathname === "/admin" || pathname.startsWith("/admin/");
}

/** One line on why a change is off while a past day is being viewed. */
export function readOnlyReason(label: string): string {
  return `You're viewing ${label}. Go back to today to make changes.`;
}

/** "today" or "on Fri 25 Sept", for headings such as "Check-ins today". */
export function dayWords(label: string | null): string {
  return label ? `on ${label}` : "today";
}

/**
 * What the banner adds on a screen whose content does not follow the chosen
 * day, because the backend has no past-day read for it.
 */
export function viewingDateNote(pathname: string): string {
  if (showsCurrentState(pathname)) {
    return "Admin always shows the current configuration, and changes here apply now.";
  }
  if (/^\/reports\/[^/]+\/daily$/.test(pathname)) {
    return "Day reports are built live, so the report below is today's. Nothing can be changed while you look back.";
  }
  if (pathname === "/coordination") {
    return "Requests and briefs are shown as they are now. Nothing can be changed while you look back.";
  }
  if (pathname === "/chat") {
    return "The conversation is shown as it is now. Nothing can be sent while you look back.";
  }
  return "Read-only: nothing can be changed while you look back.";
}
