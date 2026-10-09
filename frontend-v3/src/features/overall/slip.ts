// O1, "How the date moved": the committed date as a step line over the days, the
// history forecast's 50% to 85% range as it stood each day, and the words that
// say the same for a screen reader and for the keys beside it. Pure geometry and
// wording; runtime imports by their .ts path, so `node --test` runs it as written.
import type { ForecastDayResponse, ScopeDeliveryResponse } from "../../api/schema";

import { daysBetween, formatDay } from "../../lib/format.ts";
import { thinAxisLabels, type AxisLabel } from "./charts.ts";

/** Mirrors core/domain/forecast.py MIN_SAMPLE_DAYS: the working days a forecast needs. */
export const FORECAST_NEEDS_DAYS = 10;
/** The furthest back the chart reaches, whatever the date's own history holds. */
const MAX_SPAN_DAYS = 90;

type DateChange = ScopeDeliveryResponse["commitment"]["changes"][number];
type Change = Pick<DateChange, "target_date" | "changed_at" | "changed_by_name" | "note">;
type Day = Pick<ForecastDayResponse, "day" | "p50" | "p85">;

/** The calendar day of a change, in the viewer's own zone (the day `formatDay` prints). */
export function changeDay(changedAt: string): string {
  if (changedAt.length <= 10) return changedAt;
  const moment = new Date(changedAt);
  if (Number.isNaN(moment.getTime())) return changedAt.slice(0, 10);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}`;
}

/** How many times a dated commitment changed to another date: a move, not the first set. */
export function movesOf(changes: Change[]): number {
  const dated = changes.filter((change) => change.target_date !== null);
  return dated.filter((change, i) => i > 0 && change.target_date !== dated[i - 1].target_date)
    .length;
}

/**
 * The chart draws once there is something to draw: the date moved, or the
 * forecast has a history (two days or more with a 50% date; one day is today's
 * forecast, which the strip already shows). Until then O1 is one line.
 */
export function slipDrawable(changes: Change[], days: Day[]): boolean {
  // A move, a clear or a date set again: anything that puts a step in the line.
  const stepped = changes.some(
    (change, i) => i > 0 && change.target_date !== changes[i - 1].target_date,
  );
  return stepped || days.filter((day) => day.p50 !== null).length >= 2;
}

/** Bold words inside a sentence, as three runs: before, bold, after. */
export type Phrase = [string, string, string];

const quoted = (note: string) => (note.trim() ? `: “${note.trim()}”` : ".");

/**
 * One line per change for the keys beside the chart. "Tue 1 Dec, set by Mina
 * Patel on Mon 14 Sep: “First plan.”" then "Moved to Tue 15 Dec by Mina Patel on
 * Wed 7 Oct, 14 days later: “…”". A cleared date says so; one set again after it
 * is "set again".
 */
export function changeKeys(changes: Change[]): Phrase[] {
  let previous: string | null = null;
  let seenDate = false;
  return changes.map((change) => {
    const on = formatDay(change.changed_at);
    const who = change.changed_by_name;
    const why = quoted(change.note);
    const target = change.target_date;
    let phrase: Phrase;
    if (target === null) {
      phrase = ["", "Cleared", ` by ${who} on ${on}${why}`];
    } else if (!seenDate) {
      phrase = ["", formatDay(target), `, set by ${who} on ${on}${why}`];
    } else if (previous === null) {
      phrase = ["Set again to ", formatDay(target), ` by ${who} on ${on}${why}`];
    } else if (previous === target) {
      phrase = ["Kept at ", formatDay(target), ` by ${who} on ${on}${why}`];
    } else {
      const shift = daysBetween(previous, target);
      const by = `${Math.abs(shift)} ${Math.abs(shift) === 1 ? "day" : "days"} ${shift > 0 ? "later" : "earlier"}`;
      phrase = ["Moved to ", formatDay(target), ` by ${who} on ${on}, ${by}${why}`];
    }
    if (target !== null) seenDate = true;
    previous = target;
    return phrase;
  });
}

/**
 * The one line O1 is before it can draw: the date in force and why, or that
 * none is committed, and when the chart will draw.
 */
export function slipRecord(
  changes: Change[],
  jiraDate: string | null,
  sampleDays: number,
): { line: Phrase; note: string } {
  const last = changes[changes.length - 1];
  const line: Phrase =
    last && last.target_date
      ? [
          "",
          formatDay(last.target_date),
          `, set by ${last.changed_by_name} on ${formatDay(last.changed_at)}${quoted(last.note)}`,
        ]
      : jiraDate
        ? ["No date is committed; the Jira release date ", formatDay(jiraDate), " is used."]
        : ["", "No delivery date is committed yet.", ""];
  const has = `It has ${sampleDays}.`;
  const note =
    last && last.target_date
      ? `The chart draws once the date moves or the forecast has ${FORECAST_NEEDS_DAYS} working days of history. ${has}`
      : `The chart draws once a date is committed and moves, or the forecast has ${FORECAST_NEEDS_DAYS} working days of history. ${has}`;
  return { line, note };
}

/** "2 to 21 Dec", or "28 Nov to 21 Dec" across a month. */
export function rangeWords(from: string, to: string): string {
  const [a, b] = [formatDay(from).split(" "), formatDay(to).split(" ")];
  // formatDay is "Tue 1 Dec": the day is [1], the month [2].
  return a[2] === b[2] ? `${a[1]} to ${b[1]} ${b[2]}` : `${a[1]} ${a[2]} to ${b[1]} ${b[2]}`;
}

/**
 * What the chart shows, as a sentence: when the date was set and moved, and
 * where the forecast went and where the date sits in it. The chart's aria-label.
 */
export function slipFinding(changes: Change[], days: Day[], start: string): string {
  const parts: string[] = [`How the date moved, ${formatDay(start)} to today.`];
  const dated = changes.filter((change) => change.target_date !== null);
  if (dated.length === 0) {
    parts.push("No delivery date has been committed.");
  } else {
    const [first, ...rest] = changes;
    const moves = rest.map((change) =>
      change.target_date
        ? `moved on ${formatDay(change.changed_at)} to ${formatDay(change.target_date)}`
        : `cleared on ${formatDay(change.changed_at)}`,
    );
    const set = first.target_date
      ? `The committed date was set on ${formatDay(first.changed_at)} to ${formatDay(first.target_date)}`
      : `The committed date was cleared on ${formatDay(first.changed_at)}`;
    const shown =
      moves.length > 3
        ? [...moves.slice(0, 1), `${moves.length - 2} more times, then ${moves[moves.length - 1]}`]
        : moves;
    parts.push(`${[set, ...shown].join(", and ")}.`);
  }
  const forecast = days.filter((day) => day.p50 && day.p85);
  if (forecast.length > 0) {
    const a = forecast[0];
    const b = forecast[forecast.length - 1];
    const widthA = daysBetween(a.p50 as string, a.p85 as string);
    const widthB = daysBetween(b.p50 as string, b.p85 as string);
    const [verb, link] =
      widthB < widthA
        ? ["narrowed", "down to"]
        : widthB > widthA
          ? ["widened", "out to"]
          : ["moved", "over to"];
    const from = rangeWords(a.p50 as string, a.p85 as string);
    const to = rangeWords(b.p50 as string, b.p85 as string);
    const target = dated.length > 0 ? changes[changes.length - 1].target_date : null;
    const sits = !target
      ? ""
      : target < (b.p50 as string)
        ? "; the committed date is before it"
        : target > (b.p85 as string)
          ? "; the committed date is after it"
          : "; the committed date sits inside it";
    parts.push(
      forecast.length === 1
        ? `The forecast, drawn from ${formatDay(a.day)}, is ${from}${sits}.`
        : `The forecast, drawn from ${formatDay(a.day)}, ${verb} from ${from} ${link} ${to}${sits}.`,
    );
  } else {
    parts.push("The forecast has too little history to draw yet.");
  }
  return parts.join(" ");
}

export type SlipSize = {
  width: number;
  height: number;
  left: number;
  /** Where the plot ends; the labels at today sit right of it. */
  plotRight: number;
  top: number;
  bottom: number;
};

export const SLIP_SIZE: SlipSize = {
  width: 560,
  height: 222,
  left: 56,
  plotRight: 400,
  top: 24,
  bottom: 196,
};

export type SlipGeometry = {
  size: SlipSize;
  start: string;
  end: string;
  /** The committed date as step paths: one per run with a date, broken where it was cleared. */
  committed: string[];
  /** Where no date was committed: hatched spans along the bottom. */
  noDate: { x: number; width: number; title: string }[];
  /** One marker per change inside the window, numbered as the keys are. */
  marks: { n: number; x: number; y: number; badgeX: number; badgeY: number; title: string }[];
  /** The 50% to 85% range, one polygon per run of days that forecast. */
  bands: string[];
  bandStart: { x: number; y: number; day: string } | null;
  /** Labels at today, de-overlapped. */
  right: { y: number; text: string }[];
  yTicks: { y: number; label: string }[];
  xLabels: AxisLabel[];
  todayX: number;
};

const shortDate = (iso: string) => formatDay(iso).replace(/^\w+ /, "");

/**
 * One scale for the committed line, the forecast band and the labels. Days run
 * along x from the first change or the first forecast day (at most 90 days back)
 * to today; dates run up y, over every committed date and forecast date shown.
 */
export function slipGeometry(
  changes: Change[],
  days: Day[],
  today: string,
  size: SlipSize = SLIP_SIZE,
): SlipGeometry {
  const { left, plotRight, top, bottom } = size;
  const firstSeen = [
    ...changes.map((change) => changeDay(change.changed_at)),
    ...days.map((day) => day.day),
  ].filter((day) => day <= today);
  const earliest = firstSeen.reduce((a, b) => (b < a ? b : a), today);
  const floor = shiftDay(today, -MAX_SPAN_DAYS);
  const start = earliest < floor ? floor : earliest === today ? shiftDay(today, -7) : earliest;
  const span = Math.max(1, daysBetween(start, today));
  const x = (day: string) =>
    left + (Math.min(Math.max(daysBetween(start, day), 0), span) / span) * (plotRight - left);

  const forecastDays = days.filter((day) => day.p50 && day.p85 && day.day >= start);
  const shownTargets = changes
    .map((change, i) => ({ change, i }))
    .filter(({ change, i }) => {
      const next = changes[i + 1];
      return change.target_date !== null && (!next || changeDay(next.changed_at) >= start);
    })
    .map(({ change }) => change.target_date as string);
  const values = [
    ...shownTargets,
    ...forecastDays.flatMap((day) => [day.p50 as string, day.p85 as string]),
  ];
  const low = values.reduce((a, b) => (b < a ? b : a), values[0] ?? today);
  const high = values.reduce((a, b) => (b > a ? b : a), values[0] ?? today);
  const rangeDays = Math.max(14, daysBetween(low, high));
  const pad = Math.max(3, Math.round(rangeDays * 0.08));
  const yLow = shiftDay(low, -pad);
  const yHigh = shiftDay(low, rangeDays + pad);
  const ySpan = Math.max(1, daysBetween(yLow, yHigh));
  const y = (day: string) => bottom - (daysBetween(yLow, day) / ySpan) * (bottom - top);

  // The committed date, step by step: each change holds from its day to the next.
  const committed: string[] = [];
  const noDate: SlipGeometry["noDate"] = [];
  const marks: SlipGeometry["marks"] = [];
  let path = "";
  const firstDay = changes.length > 0 ? changeDay(changes[0].changed_at) : today;
  if (firstDay > start) {
    noDate.push({
      x: x(start),
      width: x(firstDay) - x(start),
      title:
        changes.length > 0 ? `No committed date until ${formatDay(firstDay)}` : "No committed date",
    });
  }
  changes.forEach((change, i) => {
    const from = changeDay(change.changed_at);
    const next = changes[i + 1];
    const to = next ? changeDay(next.changed_at) : today;
    if (to < start) return;
    const x0 = x(from < start ? start : from);
    const x1 = x(to);
    if (change.target_date === null) {
      if (path) committed.push(path);
      path = "";
      noDate.push({ x: x0, width: Math.max(0, x1 - x0), title: `Cleared on ${formatDay(from)}` });
    } else {
      const yy = y(change.target_date);
      path = path
        ? `${path} V${yy.toFixed(1)} H${x1.toFixed(1)}`
        : `M${x0.toFixed(1)} ${yy.toFixed(1)} H${x1.toFixed(1)}`;
    }
    if (from >= start) {
      const my = change.target_date ? y(change.target_date) : bottom - 5;
      const up = my - 16 > top;
      marks.push({
        n: i + 1,
        x: x(from),
        y: my,
        badgeX: Math.min(Math.max(x(from) - (i === 0 ? 0 : 16), left + 8), plotRight - 8),
        badgeY: up ? my - 16 : my + 16,
        title: change.target_date
          ? `${formatDay(change.target_date)}, set by ${change.changed_by_name} on ${formatDay(from)}`
          : `Cleared by ${change.changed_by_name} on ${formatDay(from)}`,
      });
    }
  });
  if (path) committed.push(path);

  // The forecast range, as it stood each day: one polygon per unbroken run.
  const bands: string[] = [];
  let run: Day[] = [];
  const flush = () => {
    if (run.length > 0) {
      const upper = run.map((day) => `${x(day.day).toFixed(1)},${y(day.p85 as string).toFixed(1)}`);
      const lower = [...run]
        .reverse()
        .map((day) => `${x(day.day).toFixed(1)},${y(day.p50 as string).toFixed(1)}`);
      bands.push([...upper, ...lower].join(" "));
    }
    run = [];
  };
  for (const day of days.filter((item) => item.day >= start)) {
    if (day.p50 && day.p85) run.push(day);
    else flush();
  }
  flush();
  const firstForecast = forecastDays[0];

  // Labels at today, top to bottom, at least 14 px apart.
  const latest = days[days.length - 1];
  const current = changes[changes.length - 1]?.target_date ?? null;
  const wanted = [
    latest?.p85 && latest.day === today
      ? { day: latest.p85, text: `p85 · ${formatDay(latest.p85)}` }
      : null,
    current ? { day: current, text: `Committed · ${formatDay(current)}` } : null,
    latest?.p50 && latest.day === today
      ? { day: latest.p50, text: `p50 · ${formatDay(latest.p50)}` }
      : null,
  ]
    .filter((item): item is { day: string; text: string } => item !== null)
    .map((item) => ({ y: y(item.day) + 4, text: item.text }))
    .sort((a, b) => a.y - b.y);
  const right: { y: number; text: string }[] = [];
  for (const label of wanted) {
    const prev = right[right.length - 1];
    right.push({ ...label, y: prev && label.y - prev.y < 14 ? prev.y + 14 : label.y });
  }

  return {
    size,
    start,
    end: today,
    committed,
    noDate,
    marks,
    bands,
    bandStart: firstForecast
      ? {
          x: x(firstForecast.day),
          y: Math.min(...forecastDays.map((day) => y(day.p85 as string))),
          day: firstForecast.day,
        }
      : null,
    right,
    yTicks: dateTicks(yLow, yHigh).map((day) => ({ y: y(day), label: shortDate(day) })),
    xLabels: thinAxisLabels(
      [
        { x: x(start), label: shortDate(start), anchor: "start" as const },
        ...monthMarks(start, today)
          .filter((day) => day !== start && daysBetween(day, today) > 3)
          .map((day) => ({ x: x(day), label: shortDate(day), anchor: "middle" as const })),
      ],
      plotRight,
    ),
    todayX: x(today),
  };
}

/** The 1st and 15th of each month inside the range, thinned to at most five. */
export function dateTicks(from: string, to: string): string[] {
  const ticks: string[] = [];
  let [year, month] = [Number(from.slice(0, 4)), Number(from.slice(5, 7))];
  for (let guard = 0; guard < 40; guard++) {
    for (const day of [1, 15]) {
      const iso = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      if (iso > from && iso < to) ticks.push(iso);
    }
    month += 1;
    if (month > 12) [year, month] = [year + 1, 1];
    if (`${year}-${String(month).padStart(2, "0")}-01` > to) break;
  }
  if (ticks.length <= 5) return ticks;
  const firsts = ticks.filter((tick) => tick.endsWith("-01"));
  // A long range: every second, third… month start, so at most five lines.
  const step = Math.ceil(firsts.length / 5);
  return firsts.filter((_, i) => i % step === 0);
}

/** The 1st, 10th and 20th of each month inside the range: where day labels go. */
export function monthMarks(from: string, to: string): string[] {
  const marks: string[] = [];
  let [year, month] = [Number(from.slice(0, 4)), Number(from.slice(5, 7))];
  for (let guard = 0; guard < 40; guard++) {
    for (const day of [1, 10, 20]) {
      const iso = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      if (iso >= from && iso <= to) marks.push(iso);
    }
    month += 1;
    if (month > 12) [year, month] = [year + 1, 1];
    if (`${year}-${String(month).padStart(2, "0")}-01` > to) break;
  }
  return marks;
}

/** An ISO day `days` later (or earlier). */
export function shiftDay(iso: string, days: number): string {
  const moment = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + days);
  return moment.toISOString().slice(0, 10);
}
