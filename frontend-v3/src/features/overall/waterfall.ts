// "Why the 85% date moved", under the timeline for the row picked on it: a
// horizontal waterfall in working days from the date then to the date now, one
// bar per driver (pace, scope added by kind, scope removed, reopened,
// re-estimated, throughput), worst first, read from GET
// /projects/{id}/delivery/impact. The bars add up to the move. Pure geometry and
// words; type imports and runtime imports by their .ts path only, so
// `node --test` runs it as written.
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

/** "85% date" or "50% date". */
export function measureWords(measure: Measure): string {
  return measure === "p50" ? "50% date" : "85% date";
}

/** One bar's name: what moved the date. */
export function driverLabel(driver: Pick<Driver, "kind" | "group">, changeLabel: string): string {
  if (driver.kind === "scope_added") {
    switch (driver.group) {
      case "change_request":
        return "Scope added: change requests";
      case "no_label":
        return `Scope added: without the ${changeLabel} label`;
      case "bug":
        return "Scope added: bugs";
      case "moved_in":
        return "Scope added: moved in";
      case "planned":
        return "Scope added: planned";
      case "before_planning":
        return "Scope added: before planning";
      default:
        return "Scope added";
    }
  }
  return (
    {
      pace: "Pace",
      scope_removed: "Scope removed",
      reopened: "Reopened",
      re_estimated: "Re-estimated",
      throughput: "Throughput",
      rounding: "Replay rounding",
      not_attributed: "Days with no breakdown kept",
    }[driver.kind] ?? driver.kind
  );
}

export type Bar = {
  id: string;
  label: string;
  value: number;
  /** Where the bar starts and ends, in working days from the date then. */
  from: number;
  to: number;
  later: boolean;
  note: string;
  driver: Driver;
};

export type Waterfall = {
  bars: Bar[];
  /** The move the bars add up to. */
  total: number;
  /** The working days the axis spans, from its lowest to its highest point. */
  min: number;
  max: number;
};

/**
 * The bars, worst (latest) first, each starting where the one before ended, so
 * the last ends at the move. A driver of no working days on the measure read is
 * left out unless it lists items (the server drops the rest already).
 */
export function waterfall(drivers: Driver[], measure: Measure, changeLabel: string): Waterfall {
  const value = (driver: Driver) => (measure === "p50" ? driver.p50 : driver.p85);
  const ordered = drivers
    .filter((driver) => Math.abs(value(driver)) >= 0.05 || driver.items.length > 0)
    .sort((a, b) => value(b) - value(a));
  let running = 0;
  let min = 0;
  let max = 0;
  const bars = ordered.map((driver, i) => {
    const from = running;
    running += value(driver);
    min = Math.min(min, running);
    max = Math.max(max, running);
    const label = driverLabel(driver, changeLabel);
    return {
      id: `${driver.kind}:${driver.group ?? ""}:${i}`,
      label,
      value: value(driver),
      from,
      to: running,
      later: value(driver) > 0,
      note: barNote(label, driver.note),
      driver,
    };
  });
  return { bars, total: Math.round(running * 100) / 100, min, max };
}

/**
 * A bar's note under its name, without saying the name twice: a note that only
 * repeats it ("replay rounding") is left out; one that opens with it
 * ("throughput (5 days joined the window …)") keeps what is in the brackets;
 * one that ends with it ("2 requirements added, without the CR label" under
 * "Scope added: without the CR label") keeps what comes before.
 */
export function barNote(label: string, note: string): string {
  const said = note.trim();
  const name = label.toLowerCase();
  if (!said || name.includes(said.toLowerCase())) return "";
  const opened = said.match(/^(.+?)\s*\((.+)\)$/);
  if (opened && name.includes(opened[1].toLowerCase())) {
    const inner = opened[2].trim();
    return inner.charAt(0).toUpperCase() + inner.slice(1);
  }
  // The longest tail of two words or more that the name already says.
  const words = said.split(/\s+/);
  for (let cut = 1; cut <= words.length - 2; cut++) {
    if (name.includes(words.slice(cut).join(" ").toLowerCase())) {
      const kept = words
        .slice(0, cut)
        .join(" ")
        .replace(/(,|\s+as)$/i, "")
        .trim();
      return kept || said;
    }
  }
  return said;
}

/** A working-day position as a share of the waterfall's width, 0 to 100. */
export function percentOf(waterfall: Waterfall, days: number): number {
  const span = waterfall.max - waterfall.min;
  if (span <= 0) return 50;
  return Math.round(((days - waterfall.min) / span) * 1000) / 10;
}

/** "18 Nov → 2 Dec": the date then and now on the measure read. */
export function endsLine(impact: DeliveryImpactResponse, measure: Measure): string | null {
  const from = measure === "p50" ? impact.start?.p50 : impact.start?.p85;
  const to = measure === "p50" ? impact.end?.p50 : impact.end?.p85;
  if (!from || !to) return null;
  return `${shortDay(from)} → ${shortDay(to)}`;
}

/** "Moved 8 working days later", from the bars' total. */
export function movedWords(total: number): string {
  if (Math.abs(total) < 0.5) return "Did not move";
  const days = Math.abs(Math.round(total * 10) / 10);
  const unit = days === 1 ? "working day" : "working days";
  return `Moved ${Number.isInteger(days) ? days : days.toFixed(1)} ${unit} ${total > 0 ? "later" : "earlier"}`;
}

/** The committed date's moves: "4 Nov → 18 Nov (Priya, 9 Oct: “two changes approved”)". */
export function committedLine(change: DeliveryImpactResponse["committed_changes"][number]): string {
  const dates = change.from_date
    ? `${shortDay(change.from_date)} → ${shortDay(change.to_date)}`
    : change.to_date
      ? `set to ${shortDay(change.to_date)}`
      : "cleared";
  const note = change.note.trim() ? `: “${change.note.trim()}”` : "";
  const after =
    change.after_added.length > 0
      ? `, after ${change.after_added.join(", ")} ${change.after_added.length === 1 ? "was" : "were"} added`
      : "";
  return `${dates} (${change.by}, ${shortDay(change.on)}${note})${after}`;
}

/** The team's date at both ends: "CHK-12, 14 Oct → CHK-20, 30 Oct". */
export function teamLine(team: NonNullable<DeliveryImpactResponse["team_date"]>): string {
  const end = (day: string | null, key: string | null) =>
    day ? `${key ? `${key}, ` : ""}${shortDay(day)}` : "no date";
  if (team.from_date === team.to_date && team.from_key === team.to_key) {
    return `Unchanged: ${end(team.to_date, team.to_key)}`;
  }
  return `${end(team.from_date, team.from_key)} → ${end(team.to_date, team.to_key)}`;
}

/** A late requirement: "CHK-4, due 2 Oct, 6 working days late, In testing". */
export function lateLine(
  late: NonNullable<DeliveryImpactResponse["evidence"]>["late"][number],
  stageWord: (stage: typeof late.stage) => string,
): string {
  const kind = { due: "due", eta: "ETA", baseline_due: "due at planning" }[late.date_kind] ?? "due";
  const days =
    late.working_days_late === 1 ? "1 working day" : `${late.working_days_late} working days`;
  return `${late.key}, ${kind} ${shortDay(late.date)}, ${days} late, ${stageWord(late.stage)}`;
}

/** The days a period chip shades on the timeline's axis: the read's own start to its end. */
export function shadedPeriod(impact: DeliveryImpactResponse | undefined): {
  from: string;
  to: string;
} | null {
  if (!impact?.start || !impact.end) return null;
  return impact.start.day < impact.end.day ? { from: impact.start.day, to: impact.end.day } : null;
}
