// "Why the date moved", under the timeline for the row picked on it (decision
// D78): the server's one sentence (where the date stands, then why), and at most
// three causes -- scope added, scope removed, team speed -- as bars from zero, red
// to the right for what pushed the date later, green to the left for what pulled
// it in, read from GET /projects/{id}/delivery/impact. The server words them;
// this module lays them out. Type imports and runtime imports by their .ts path
// only, so `node --test` runs it as written.
import type {
  DeliveryImpactResponse,
  ImpactDriverResponse,
  Measure,
  PeriodKind,
} from "../../api/schema";

import { shortDay } from "./timeline.ts";

export type Driver = ImpactDriverResponse;

/** The period chips, in the order they are offered. */
export const PERIODS: { kind: PeriodKind; label: string }[] = [
  { kind: "since_baseline", label: "Since planning" },
  { kind: "last_7_days", label: "Last 7 days" },
  { kind: "last_30_days", label: "Last 30 days" },
  { kind: "since_date_change", label: "Since the date last changed" },
];

export const MINUS = "−";

/** "+6", "−3", "0": working days, later positive. */
export function signed(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  const text = Number.isInteger(rounded) ? String(Math.abs(rounded)) : Math.abs(rounded).toFixed(1);
  if (rounded > 0) return `+${text}`;
  if (rounded < 0) return `${MINUS}${text}`;
  return "0";
}

/** "+24 days", "−1 day": what a bar's end says. */
export function daysWords(value: number): string {
  const said = signed(value);
  return `${said} ${Math.abs(Math.round(value * 10) / 10) === 1 ? "day" : "days"}`;
}

/** The two dates the Details toggle offers, in a leader's words. */
export function measureWords(measure: Measure): string {
  return measure === "p50" ? "Likely" : "At the latest likely";
}

/** One bar's name: a cause a leader knows. */
export function driverLabel(driver: Pick<Driver, "kind">): string {
  return (
    {
      scope_added: "Scope added",
      scope_removed: "Scope removed",
      team_speed: "Team speed",
    }[driver.kind] ?? "Other"
  );
}

export type Bar = {
  id: string;
  label: string;
  value: number;
  /** It pushed the date later (red, right) or pulled it in (green, left). */
  later: boolean;
  /** Its share of half the bar area, 0 to 1, from the largest bar. */
  size: number;
  note: string;
  driver: Driver;
};

/**
 * The bars, latest-pushing first, each from zero: its size is its share of the
 * largest. A cause of no working days on the measure read is left out unless it
 * lists items.
 */
export function causeBars(drivers: Driver[], measure: Measure): Bar[] {
  const value = (driver: Driver) => (measure === "p50" ? driver.p50 : driver.p85);
  const kept = drivers
    .filter((driver) => Math.abs(value(driver)) >= 0.05 || driver.items.length > 0)
    .sort((a, b) => value(b) - value(a));
  const largest = Math.max(0.0001, ...kept.map((driver) => Math.abs(value(driver))));
  return kept.map((driver, i) => ({
    id: `${driver.kind}:${i}`,
    label: driverLabel(driver),
    value: value(driver),
    later: value(driver) > 0,
    size: Math.abs(value(driver)) / largest,
    note: driver.note.trim(),
    driver,
  }));
}

/** "3 Jun then, 19 Mar now": the likely date at both ends of the period. */
export function endsLine(impact: DeliveryImpactResponse, measure: Measure): string | null {
  const from = measure === "p50" ? impact.start?.p50 : impact.start?.p85;
  const to = measure === "p50" ? impact.end?.p50 : impact.end?.p85;
  if (!from || !to) return null;
  return `${shortDay(from)} then, ${shortDay(to)} now`;
}

/** A committed date's move, short: "4 Nov → 18 Nov, by Priya on 9 Oct". */
export function committedLine(change: DeliveryImpactResponse["committed_changes"][number]): string {
  const dates = change.from_date
    ? `${shortDay(change.from_date)} → ${shortDay(change.to_date)}`
    : change.to_date
      ? `set to ${shortDay(change.to_date)}`
      : "cleared";
  return `${dates}, by ${change.by} on ${shortDay(change.on)}`;
}

/** The team's date at both ends: "4 Dec (CHK-103), unchanged", "14 Oct → 30 Oct (CHK-20)". */
export function teamLine(team: NonNullable<DeliveryImpactResponse["team_date"]>): string {
  const end = (day: string | null, key: string | null) =>
    day ? `${shortDay(day)}${key ? ` (${key})` : ""}` : "no date";
  if (team.from_date === team.to_date && team.from_key === team.to_key) {
    return `${end(team.to_date, team.to_key)}, unchanged`;
  }
  return `${team.from_date ? shortDay(team.from_date) : "no date"} → ${end(team.to_date, team.to_key)}`;
}
