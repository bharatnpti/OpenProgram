// "What changed" on the Project page: the picked row's events over the
// period Why the date moved reads, dated, oldest first. Only a scope event
// (requirements added, a bug counted as one, removed, reopened) carries a chip:
// the working days its day's step moved the date, from that day's kept
// breakdown, split by item count when several share a day (the server's
// `working_days`). A blocker or a requirement past its date is evidence and has
// no chip; a committed date's move is said once, under Why the date moved.
// Team speed is one line for the period, never per event. The footer checks
// that the chips and team speed make the waterfall's total, and says what does
// not: re-estimates, days that kept no breakdown, reconstructed days.
// Type imports and runtime imports by their .ts path, so `node --test` runs it.
import type { DeliveryImpactResponse, Measure } from "../../api/schema";

import { shortDay } from "../overall/timeline.ts";
import { MINUS, signed } from "../overall/waterfall.ts";

type Marker = DeliveryImpactResponse["markers"][number];

/** The scope events: the only ones a chip is drawn on. */
export const SCOPE_KINDS = new Set(["scope_added", "bug", "scope_removed", "reopened"]);
/** Said under Why the date moved, with who and why; not repeated here. */
const SAID_ELSEWHERE = new Set(["committed_date_changed"]);
/** The most keys a line names before "and N more". */
const KEYS_SHOWN = 3;
/** Below this many working days a difference is rounding. */
const ROUNDING = 0.05;

export type ChangeChip =
  /** Working days on the measure read; `reconstructed` when the day was rebuilt afterwards. */
  | { kind: "days"; value: number; text: string; reconstructed: boolean }
  /** A scope event whose day kept no breakdown that can be read. */
  | { kind: "unknown"; text: string };

export type ChangeLine = {
  key: string;
  /** The marker kind: scope_added, bug, scope_removed, reopened, late, blocker_opened, blocker_closed. */
  kind: string;
  day: string;
  /** "12 Jun". */
  dayLabel: string;
  /** "41 requirements added, 38 without the CR label". */
  text: string;
  /** "CHK-101, CHK-102 and 39 more"; empty when the marker named none. */
  keys: string;
  /** It pushed the date (a red dot on the timeline): an unlabelled addition, a bug, reopened, a blocker. */
  pushed: boolean;
  /** Only a scope event has one. */
  chip: ChangeChip | null;
};

export type ChangeFooter = {
  /** The chips added up, on the measure read. */
  events: number;
  /** Team speed for the period: how fast the team finishes, rounding, days not taken apart. */
  team: number;
  /** What neither says: re-estimated requirements, which join a cause's bar but no event. */
  rest: number;
  /** The waterfall's total: the date's whole move over the period. */
  total: number;
  /** The chips and team speed alone make the total, to rounding. */
  adds: boolean;
  /** "Scope events +15 · team speed +9 · re-estimates +2 = moved 26 working days". */
  text: string;
  /** Plain notes on what is not known or rebuilt: each a sentence. */
  notes: string[];
};

export type WhatChanged = { lines: ChangeLine[]; footer: ChangeFooter | null };

const noun = (count: number, one: string, many = `${one}s`) =>
  `${count} ${count === 1 ? one : many}`;

/** "+11 working days", "−6 working days", "0 working days", on the 85% or 50% date. */
export function chipWords(value: number): string {
  const said = signed(value);
  const one = Math.abs(Math.round(value * 10) / 10) === 1;
  return `${said} working ${one ? "day" : "days"}`;
}

function keyList(markers: Marker[]): string {
  const keys = [...new Set(markers.flatMap((marker) => marker.items.map((item) => item.key)))];
  const total = markers.reduce((sum, marker) => sum + marker.count, 0);
  const shown = keys.slice(0, KEYS_SHOWN);
  if (shown.length === 0) return "";
  const more = total - shown.length;
  return more > 0 ? `${shown.join(", ")} and ${more} more` : shown.join(", ");
}

/** A day's markers of one line's kind added up into its chip; null when none is drawn. */
function chipOf(markers: Marker[]): ChangeChip | null {
  const dated = markers.filter((marker) => marker.taken_apart !== null);
  if (dated.length === 0) return null;
  if (dated.some((marker) => marker.taken_apart === false || marker.working_days === null)) {
    return { kind: "unknown", text: "not known" };
  }
  const value = dated.reduce((sum, marker) => sum + (marker.working_days ?? 0), 0);
  return {
    kind: "days",
    value,
    text: chipWords(value),
    reconstructed: dated.some((marker) => marker.reconstructed),
  };
}

function addedWords(markers: Marker[], changeLabel: string): string {
  const total = markers.reduce((sum, marker) => sum + marker.count, 0);
  const without = markers
    .filter((marker) => !marker.change_label)
    .reduce((sum, marker) => sum + marker.count, 0);
  const added = `${noun(total, "requirement")} added`;
  if (without === 0) return `${added} with the ${changeLabel} label`;
  if (without === total) return `${added} without the ${changeLabel} label`;
  return `${added}, ${without} without the ${changeLabel} label`;
}

function eventWords(kind: string, count: number): string {
  switch (kind) {
    case "bug":
      return `${noun(count, "bug")} opened`;
    case "scope_removed":
      return `${noun(count, "requirement")} removed`;
    case "reopened":
      return `${noun(count, "requirement")} reopened`;
    case "late":
      return `${noun(count, "requirement")} passed ${count === 1 ? "its" : "their"} date while open`;
    case "blocker_opened":
      return `${noun(count, "blocker")} opened`;
    case "blocker_closed":
      return `${noun(count, "blocker")} cleared`;
    default:
      return noun(count, "change");
  }
}

const PUSHES = new Set(["bug", "reopened", "blocker_opened"]);
/** Within a day: additions first, then the rest in the timeline's order. */
const ORDER = [
  "scope_added",
  "bug",
  "reopened",
  "scope_removed",
  "blocker_opened",
  "blocker_closed",
  "late",
];

/**
 * The period's events, oldest first, one line per day and kind (a day's
 * additions with and without the label are one line), each scope event with
 * its chip; then the footer that checks the sum. `impact` is the read Why the
 * date moved draws, so both say the same period on the same measure.
 */
export function whatChanged(impact: DeliveryImpactResponse): WhatChanged {
  const byLine = new Map<string, Marker[]>();
  for (const marker of impact.markers) {
    if (SAID_ELSEWHERE.has(marker.kind)) continue;
    const key = `${marker.day}:${marker.kind}`;
    byLine.set(key, [...(byLine.get(key) ?? []), marker]);
  }
  const lines: ChangeLine[] = [...byLine.entries()]
    .map(([key, markers]) => {
      const { day, kind } = markers[0];
      const count = markers.reduce((sum, marker) => sum + marker.count, 0);
      const text =
        kind === "scope_added" ? addedWords(markers, impact.change_label) : eventWords(kind, count);
      return {
        key,
        kind,
        day,
        dayLabel: shortDay(day),
        text,
        keys: keyList(markers),
        pushed:
          PUSHES.has(kind) || (kind === "scope_added" && markers.some((m) => !m.change_label)),
        chip: SCOPE_KINDS.has(kind) ? chipOf(markers) : null,
      };
    })
    .sort((a, b) => a.day.localeCompare(b.day) || ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));
  return { lines, footer: footerOf(impact, lines) };
}

/**
 * The check under the list: the chips plus team speed against the waterfall's
 * total. They differ only by re-estimates (which join a cause's bar, but no
 * event) and rounding, and the footer says so; days that kept no breakdown and
 * days rebuilt afterwards are named. No footer while there is no move to take
 * apart (no likely date at one end).
 */
export function footerOf(impact: DeliveryImpactResponse, lines: ChangeLine[]): ChangeFooter | null {
  const total = impact.moved_working_days;
  if (total === null) return null;
  const events = lines.reduce(
    (sum, line) => sum + (line.chip?.kind === "days" ? line.chip.value : 0),
    0,
  );
  const team = impact.drivers.find((driver) => driver.kind === "team_speed")?.working_days ?? 0;
  const rest = total - events - team;
  const adds = Math.abs(rest) < ROUNDING;
  const parts = [`Scope events ${signed(events)}`, `team speed ${signed(team)}`];
  if (!adds) parts.push(`re-estimated requirements ${signed(rest)}`);
  const text = `${parts.join(" · ")} = ${movedWords(total)}${measureTail(impact.measure)}`;
  const notes: string[] = [];
  const unknown = lines.filter((line) => line.chip?.kind === "unknown").length;
  if (unknown > 0) {
    notes.push(
      `${noun(unknown, "event")} fell on ${unknown === 1 ? "a day" : "days"} that kept no breakdown, so ${unknown === 1 ? "its" : "their"} working days are not known.`,
    );
  }
  const rebuilt = lines.filter((line) => line.chip?.kind === "days" && line.chip.reconstructed);
  if (rebuilt.length > 0 || impact.reconstructed_days > 0) {
    const days = Math.max(impact.reconstructed_days, new Set(rebuilt.map((line) => line.day)).size);
    notes.push(
      `${noun(days, "day")} of this period ${days === 1 ? "was" : "were"} rebuilt afterwards, not kept on the day: ${
        rebuilt.length === 0
          ? `no event fell on ${days === 1 ? "it" : "them"}`
          : rebuilt.length === 1
            ? "its chip is marked reconstructed"
            : "their chips are marked reconstructed"
      }.`,
    );
  }
  return { events, team, rest: adds ? 0 : rest, total, adds, text, notes };
}

/** "moved 54 working days earlier", "moved 1 working day later", "did not move". */
export function movedWords(total: number): string {
  const said = signed(Math.abs(total));
  if (said === "0") return "did not move";
  const one = said === "+1" ? "day" : "days";
  return `moved ${said.replace(/^\+/, "")} working ${one} ${total > 0 ? "later" : "earlier"}`;
}

function measureTail(measure: Measure): string {
  return measure === "p50" ? " on the likely date" : " on the latest likely date";
}

/** The chip as a screen reader hears it: "plus 11 working days", "not known". */
export function chipLabel(chip: ChangeChip): string {
  if (chip.kind === "unknown") return "working days not known";
  const words = chip.text.replace(/^\+/, "plus ").replace(new RegExp(`^${MINUS}`), "minus ");
  return chip.reconstructed ? `${words}, reconstructed` : words;
}
