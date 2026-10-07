// Pure helpers, type imports only, so `node --test` can run them directly.

/**
 * The moment to read a day from. A calendar day ("2026-10-06") is that day
 * wherever the viewer is, so it is read at its own midday. A timestamp
 * ("2026-10-06T18:37:00Z") is an instant, so its day is the viewer's local one,
 * the same day `formatTime` puts beside it: 00:07 on Wed 7 Oct in India, not
 * Tue 6 Oct.
 */
function dayMoment(iso: string): Date {
  const day = new Date(`${iso.slice(0, 10)}T12:00:00`);
  if (iso.length <= 10 || iso[10] !== "T") return day;
  const instant = new Date(iso);
  return Number.isNaN(instant.getTime()) ? day : instant;
}

/** "Tue 6 Oct" for an ISO day, or for a timestamp in the viewer's own day. */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dayMoment(iso).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

/** "6 Oct 2026" for an ISO day, or for a timestamp in the viewer's own day. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dayMoment(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** "17:30" for a timestamp, in the viewer's own time. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** "Mon–Fri" for [0,1,2,3,4]; a broken run is listed ("Mon, Wed, Fri"). Monday is 0. */
export function weekdaysLabel(days: number[]): string {
  const sorted = [...new Set(days)].filter((d) => d >= 0 && d <= 6).sort((a, b) => a - b);
  if (sorted.length === 0) return "no days";
  if (sorted.length === 7) return "every day";
  const contiguous = sorted.every((day, i) => i === 0 || day === sorted[i - 1] + 1);
  if (contiguous && sorted.length > 2) {
    return `${WEEKDAYS[sorted[0]]}–${WEEKDAYS[sorted[sorted.length - 1]]}`;
  }
  return sorted.map((d) => WEEKDAYS[d]).join(", ");
}

/** "+2", "−1", "±0", or "" when there is nothing to compare against. */
export function signedChange(change: number | null | undefined): string {
  if (change === null || change === undefined) return "";
  if (change > 0) return `+${change}`;
  if (change < 0) return `−${Math.abs(change)}`;
  return "±0";
}

/** Whole days from `from` to `to` (ISO days); negative when `to` is earlier. */
export function daysBetween(from: string, to: string): number {
  const a = Date.parse(`${from.slice(0, 10)}T12:00:00Z`);
  const b = Date.parse(`${to.slice(0, 10)}T12:00:00Z`);
  return Math.round((b - a) / 86_400_000);
}

/** Clamp a percentage to 0–100; nothing known is 0. */
export function progressWidth(percent: number | null | undefined): number {
  if (percent === null || percent === undefined || Number.isNaN(percent)) return 0;
  return Math.max(0, Math.min(100, percent));
}
