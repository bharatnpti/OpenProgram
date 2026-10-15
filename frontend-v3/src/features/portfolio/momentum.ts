// A node's health over a window of days, in words, pure: its direction and
// where it stands now. Type imports only, so `node --test` runs it as written.
import type { NodeTrendResponse } from "../../api/schema";

/**
 * Direction of travel over the reported days only: an `unknown` day carries
 * score 0, below red, so it is not a value on the health scale and comparing
 * it as one would manufacture a direction. Says where it stands now, because
 * a programme that is red all month draws a flat line at the bottom.
 */
export function momentum(points: NodeTrendResponse["points"] | undefined): {
  label: string;
  values: (number | null)[];
  /** Days in the window, and how many of them reported a status. */
  days: number;
  reportedDays: number;
} {
  const reported = (points ?? []).filter((p) => p.rag !== "unknown");
  const values = (points ?? []).map((p) => (p.rag === "unknown" ? null : (p.score - 1) / 2));
  const counts = { days: (points ?? []).length, reportedDays: reported.length };
  if (reported.length < 2) return { label: "not enough reported days", values, ...counts };
  const first = reported[0].score;
  const last = reported[reported.length - 1];
  const direction = last.score > first ? "improving" : last.score < first ? "sliding" : "steady";
  return { label: `${direction}, now ${last.rag}`, values, ...counts };
}

/**
 * The line beside the 30 days: the window, and how much of it the direction
 * rests on. "30 days" beside a line that is empty for most of them says more
 * than it knows, so the days that reported are counted. The history may hold
 * fewer entries than the window has days (it keeps working days only).
 */
export function momentumNote(
  m: { label: string; days: number; reportedDays: number },
  windowDays = 30,
): string {
  return m.reportedDays > 0 && m.reportedDays < m.days
    ? `${windowDays} days · ${m.reportedDays} ${m.reportedDays === 1 ? "day" : "days"} reported · ${m.label}`
    : `${windowDays} days · ${m.label}`;
}
