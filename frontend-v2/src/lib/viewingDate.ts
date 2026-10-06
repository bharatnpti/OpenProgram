/**
 * The console's viewing date: which day every dated read asks the backend for.
 *
 * It lives in the URL as `?asOf=YYYY-MM-DD`, and only for a past day: today is
 * the absence of the param, so a plain link always opens on today. Everything
 * here is pure so the rules can be checked without a browser.
 */

export const VIEWING_DATE_PARAM = "asOf";

export type NavigationKind = "POP" | "PUSH" | "REPLACE";

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

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
 * Admin edits current configuration, and Reports shows today's report built
 * live with the current set-up, so neither follows a past date.
 */
const CURRENT_ONLY = ["/admin", "/reports"];

export function viewingDateApplies(pathname: string): boolean {
  return !CURRENT_ONLY.some((root) => pathname === root || pathname.startsWith(`${root}/`));
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

/**
 * Which past day this screen shows (null for today), and the `asOf` value its
 * URL should carry.
 *
 * In-app links don't know about the date, so a link followed while looking at
 * a past day (PUSH or REPLACE without the param) carries that day forward,
 * unless the navigation chose today. Back and forward (POP) restore exactly
 * what that history entry said, so an entry without the param is today.
 */
export function resolveViewingDate(input: {
  raw: string | null;
  carried: string | null;
  navigation: NavigationKind;
  choseToday: boolean;
  today: string;
  applies: boolean;
}): { past: string | null; param: string | null } {
  if (!input.applies) return { past: null, param: null };
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

/** "Fri 25 Sep", with the year when it is not this year. */
export function formatDayLabel(isoDate: string, today: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  const parts = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).formatToParts(new Date(year, month - 1, day));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  const label = `${part("weekday")} ${part("day")} ${part("month")}`;
  return isoDate.slice(0, 4) === today.slice(0, 4) ? label : `${label} ${part("year")}`;
}
