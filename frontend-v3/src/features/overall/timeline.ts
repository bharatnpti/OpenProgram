// The timeline per project and pod, drawn to be read in five seconds (decision
// D78): one row per scope with a grey track from planning to its committed date,
// a dark fill for what is done, the likely finish as one segment in its verdict's
// colour, the committed date as a tick, and one dot on each day something
// changed, red when it pushed the date. Pure geometry, words and keyboard moves;
// the components draw what these return. Type imports and runtime imports by
// their .ts path only, so `node --test` runs it as written.
import type {
  TimelineMarkerResponse,
  TimelineResponse,
  TimelineRowResponse,
  Verdict,
} from "../../api/schema";

import { forecastTone } from "../../components/ui/dateStripWords.ts";
import { VERDICT_LABELS, toneForVerdict, type BadgeTone } from "../../lib/status.ts";
import { thinAxisLabels, type AxisLabel } from "./charts.ts";

export type Row = TimelineRowResponse;
export type Marker = TimelineMarkerResponse;
type MarkerItem = Marker["items"][number];

/** The narrowest the axis is drawn: four weeks per 112 px, so a week is always 28 px. */
export const MIN_PX_PER_WEEK = 28;
/** Past twelve weeks a row's changes merge per week, so a dot keeps its room. */
export const WEEK_MERGE_DAYS = 84;
/** Two dots on one row keep this far apart; the later one moves right. */
export const DOT_GAP = 16;
/** The most items a change's list names; the rest are counted. */
export const LIST_ITEMS = 10;
/** The most days a row's "What changed" list says, newest first. */
export const CHANGE_LINES = 5;
/** The team's date is said beside the likely date only when they are this far apart. */
export const TEAM_APART_DAYS = 7;

const DAY_MS = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// ---- days -----------------------------------------------------------------------

/** Days since 1 Jan 1970 for a calendar day: the same day wherever the viewer is. */
export function dayNumber(iso: string): number {
  return Math.round(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / DAY_MS);
}

export function isoOf(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

/** "9 Oct" for a calendar day. */
export function shortDay(iso: string | null | undefined): string {
  if (!iso) return "no date";
  const day = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return `${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]}`;
}

/** "Fri 9 Oct" for a calendar day. */
export function longDay(iso: string | null | undefined): string {
  if (!iso) return "no date";
  const day = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return `${WEEKDAYS[day.getUTCDay()]} ${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]}`;
}

/** The Monday of the week a day falls in. */
export function weekOf(iso: string): string {
  const day = dayNumber(iso);
  const weekday = new Date(day * DAY_MS).getUTCDay();
  return isoOf(day - ((weekday + 6) % 7));
}

// ---- the scale ----------------------------------------------------------------------

export type Scale = {
  /** The axis's first and last day, as day numbers. */
  start: number;
  end: number;
  days: number;
  pxPerDay: number;
  /** The drawing's width in px: wider than the box on a phone, which then scrolls. */
  width: number;
  /** Changes merge per week on a long axis. */
  weekly: boolean;
};

/** The axis fitted to ``available`` px, never narrower than 28 px a week. */
export function timeScale(axis: TimelineResponse["axis"], available: number): Scale {
  const start = dayNumber(axis.start);
  const end = Math.max(dayNumber(axis.end), start + 1);
  const days = end - start;
  const least = MIN_PX_PER_WEEK / 7;
  const pxPerDay = Math.max(least, available > 0 ? available / days : least);
  return {
    start,
    end,
    days,
    pxPerDay,
    width: Math.floor(days * pxPerDay),
    weekly: days > WEEK_MERGE_DAYS,
  };
}

/** A day's x, kept inside the drawing. */
export function xOf(scale: Scale, iso: string): number {
  const x = (dayNumber(iso) - scale.start) * scale.pxPerDay;
  return Math.round(Math.min(Math.max(x, 0), scale.width) * 10) / 10;
}

/**
 * The axis labels: each month's first day only, thinned so no two run
 * together. ``today`` is the day line's own label: a month label it would touch
 * gives way to it.
 */
export function axisLabels(scale: Scale, today?: { x: number; label: string }): AxisLabel[] {
  const candidates: { x: number; label: string; anchor: "middle" }[] = [];
  for (let day = scale.start; day <= scale.end; day++) {
    const date = new Date(day * DAY_MS);
    if (date.getUTCDate() === 1) {
      candidates.push({
        x: (day - scale.start) * scale.pxPerDay,
        label: `1 ${MONTHS[date.getUTCMonth()]}`,
        anchor: "middle",
      });
    }
  }
  if (!today) return thinAxisLabels(candidates, scale.width);
  // The day line's label goes first, so it is kept; it is drawn on its own.
  const marked = ` ${today.label}`;
  return thinAxisLabels(
    [{ x: today.x, label: marked, anchor: "middle" }, ...candidates],
    scale.width,
  ).filter((label) => label.label !== marked);
}

// ---- one row -------------------------------------------------------------------------

export type RowShape = {
  /** The track: from planning (or the first kept day) to the committed date. */
  start: number;
  end: number;
  /** No committed date: the track runs to the likely finish (or today), dashed. */
  open: boolean;
  /** Where the done fill ends. */
  fill: number | null;
  /** Done of total, 0 to 1. */
  share: number | null;
  /** The likely finish, 50% to 85%, in the verdict's colour. */
  likely: {
    from: number;
    to: number;
    tone: BadgeTone;
    /** It runs past the committed date: drawn there, with its dates said. */
    late: boolean;
    label: string;
  } | null;
  committed: number | null;
};

export function rowShape(row: Row, scale: Scale, today: string): RowShape {
  const committedDate = row.committed.date;
  const start = xOf(scale, row.start);
  const committed = committedDate ? xOf(scale, committedDate) : null;
  const forecast = row.forecast;
  const likely = forecast
    ? (() => {
        const from = xOf(scale, forecast.p50);
        return {
          from,
          to: Math.max(xOf(scale, forecast.p85), from + 3),
          tone: forecastTone(committedDate ?? row.jira_release_date, forecast.p50, forecast.p85),
          late: Boolean(committedDate && forecast.p85 > committedDate),
          label: likelyRange(forecast.p50, forecast.p85),
        };
      })()
    : null;
  const end =
    committed !== null
      ? Math.max(committed, start)
      : Math.max(likely?.to ?? 0, xOf(scale, today), start + 2);
  const progress = row.progress;
  const share =
    progress && progress.total > 0
      ? Math.min(1, Math.max(0, progress.done / progress.total))
      : null;
  return {
    start,
    end,
    open: committed === null,
    fill: share !== null ? start + (end - start) * share : null,
    share,
    likely,
    committed,
  };
}

/** "likely 3 Feb – 19 Mar"; one day when both are the same. */
export function likelyRange(p50: string, p85: string): string {
  return p50 === p85 ? `likely ${shortDay(p85)}` : `likely ${shortDay(p50)} – ${shortDay(p85)}`;
}

/** The verdict's pill: its word and tone; a dates row without a forecast says so. */
export function verdictChip(verdict: Verdict | null): { label: string; tone: BadgeTone } {
  if (verdict === null) return { label: "No likely date", tone: "neutral" };
  return { label: VERDICT_LABELS[verdict], tone: toneForVerdict(verdict) };
}

/** "2 of 10 done", "8 of 22 pts done"; nothing on a dates row. */
export function progressLabel(row: Row): string {
  const progress = row.progress;
  if (!progress) return "";
  if (progress.total <= 0) return "nothing in scope";
  const points = progress.unit === "story points" ? " pts" : "";
  return `${number(progress.done)} of ${number(progress.total)}${points} done`;
}

/**
 * The row's one plain line: "Likely by 19 Mar · committed 15 Dec · 2 of 10 done",
 * with "team says 4 Dec" only when the team's date is more than a week from the
 * likely one.
 */
export function rowLine(row: Row): string[] {
  const parts: string[] = [];
  const likely = row.forecast?.p85 ?? null;
  parts.push(likely ? `Likely by ${shortDay(likely)}` : "No likely date yet");
  parts.push(
    row.committed.date ? `committed ${shortDay(row.committed.date)}` : "no committed date",
  );
  const done = progressLabel(row);
  if (done) parts.push(done);
  const team = row.team.date;
  if (team && (!likely || Math.abs(dayNumber(team) - dayNumber(likely)) > TEAM_APART_DAYS)) {
    parts.push(`team says ${shortDay(team)}`);
  }
  return parts;
}

// ---- what changed --------------------------------------------------------------------

export type ChangeEntry = {
  kind: string;
  count: number;
  /** In plain words: "1 requirement added without the CR label". */
  words: string;
  /** It pushed the date: added without the change label, a bug, a blocker, reopened, a later date. */
  pushed: boolean;
  items: MarkerItem[];
};

export type Dot = {
  id: string;
  /** The day it happened, or the Monday of its week on a long axis. */
  day: string;
  weekly: boolean;
  x: number;
  count: number;
  pushed: boolean;
  entries: ChangeEntry[];
  label: string;
};

/** One marker as a change a leader reads, with whether it pushed the date. */
export function changeEntry(marker: Marker, row: Row, changeLabel: string): ChangeEntry {
  const count = marker.count;
  const n = (one: string, many: string) => (count === 1 ? `1 ${one}` : `${count} ${many}`);
  const requirements = n("requirement", "requirements");
  const entry = (words: string, pushed: boolean): ChangeEntry => ({
    kind: marker.kind,
    count,
    words,
    pushed,
    items: marker.items,
  });
  switch (marker.kind) {
    case "scope_added":
      return marker.change_label
        ? entry(`${requirements} added with the ${changeLabel} label`, false)
        : entry(`${requirements} added without the ${changeLabel} label`, true);
    case "scope_removed":
      return entry(`${requirements} removed`, false);
    case "reopened":
      return entry(`${requirements} reopened`, true);
    case "late":
      return entry(
        `${requirements} passed ${count === 1 ? "its" : "their"} date while open`,
        false,
      );
    case "bug":
      return entry(n("bug opened", "bugs opened"), true);
    case "blocker_opened":
      return entry(n("blocker opened", "blockers opened"), true);
    case "blocker_closed":
      return entry(n("blocker cleared", "blockers cleared"), false);
    case "committed_date_changed": {
      const index = row.committed.history.findIndex((tick) => tick.on === marker.day);
      const tick = index >= 0 ? row.committed.history[index] : null;
      const before = index > 0 ? row.committed.history[index - 1] : null;
      if (tick && before) {
        return {
          ...entry(`committed date moved ${shortDay(before.date)} → ${shortDay(tick.date)}`, false),
          count: 1,
          pushed: tick.date > before.date,
        };
      }
      return {
        ...entry(
          tick ? `committed date set to ${shortDay(tick.date)}` : "committed date changed",
          false,
        ),
        count: 1,
      };
    }
    default:
      return entry(`${n("change", "changes")}`, false);
  }
}

/**
 * A row's dots: every change on one day (one week on a long axis) is one dot
 * with a count, red when any of them pushed the date; left to right, nudged
 * apart so none hides another.
 */
export function rowDots(row: Row, scale: Scale, changeLabel: string): Dot[] {
  const buckets = new Map<string, Dot>();
  for (const marker of mergedMarkers(row.markers, scale.weekly)) {
    const day = scale.weekly ? weekOf(marker.day) : marker.day;
    const change = changeEntry(marker, row, changeLabel);
    const found = buckets.get(day);
    if (found) {
      found.entries.push(change);
      found.count += change.count;
      found.pushed ||= change.pushed;
      // A week's dot sits on its first change, never ahead of what happened.
      found.x = Math.min(found.x, xOf(scale, marker.day));
      continue;
    }
    buckets.set(day, {
      id: day,
      day,
      weekly: scale.weekly,
      x: xOf(scale, marker.day),
      count: change.count,
      pushed: change.pushed,
      entries: [change],
      label: "",
    });
  }
  const dots = [...buckets.values()].sort((a, b) => a.x - b.x);
  let last = -Infinity;
  for (const dot of dots) {
    dot.x = Math.max(dot.x, last + DOT_GAP);
    last = dot.x;
    dot.entries.sort((a, b) => Number(b.pushed) - Number(a.pushed));
    const when = dot.weekly ? `Week of ${shortDay(dot.day)}` : shortDay(dot.day);
    const words = dot.entries.map((entry) => entry.words).join(", ");
    dot.label = `${when}: ${words}.${dot.pushed ? " Pushed the date." : ""}`;
  }
  return dots;
}

/**
 * A dot's markers with one kind said once: the same kind (and label) on one day,
 * or in one week on a long axis, is one marker with the counts and items added.
 * A committed date's moves stay apart, since each says its own dates.
 */
function mergedMarkers(markers: Marker[], weekly: boolean): Marker[] {
  const merged = new Map<string, Marker>();
  const kept: Marker[] = [];
  for (const marker of markers) {
    if (marker.kind === "committed_date_changed") {
      kept.push(marker);
      continue;
    }
    const day = weekly ? weekOf(marker.day) : marker.day;
    const key = `${day}:${marker.kind}:${String(marker.change_label)}`;
    const found = merged.get(key);
    if (found) {
      merged.set(key, {
        ...found,
        day: found.day < marker.day ? found.day : marker.day,
        count: found.count + marker.count,
        items: [...found.items, ...marker.items],
      });
    } else {
      merged.set(key, marker);
    }
  }
  return [...merged.values(), ...kept];
}

/** One item of a change's list, after its key and title: its stage, label, who and what. */
export function itemNote(
  item: MarkerItem,
  kind: string,
  changeLabel: string,
  stageWord: (stage: NonNullable<MarkerItem["stage"]>) => string,
): string {
  const parts: string[] = [];
  if (item.stage) parts.push(stageWord(item.stage));
  if (kind === "scope_added") {
    parts.push(item.change_label ? `${changeLabel} label` : `no ${changeLabel} label`);
    if (item.hygiene) parts.push("open in Jira hygiene");
  }
  if (item.added_by) parts.push(`added by ${item.added_by}`);
  if (item.waits_on) parts.push(`waits on: ${item.waits_on}`);
  return parts.join(" · ");
}

export type ChangeList = {
  /** "Since planning (5 Oct): 8 → 10 requirements", when the row has a baseline. */
  lead: string | null;
  /** Newest first: "9 Oct · 2 requirements added (1 without the CR label)". */
  lines: { day: string; text: string; pushed: boolean }[];
  /** Days older than the lines shown. */
  more: number;
};

/** The short "What changed" list under the chart: one line per day, newest first. */
export function changeList(row: Row, changeLabel: string, max = CHANGE_LINES): ChangeList {
  const byDay = new Map<string, Marker[]>();
  for (const marker of row.markers) {
    byDay.set(marker.day, [...(byDay.get(marker.day) ?? []), marker]);
  }
  const days = [...byDay.keys()].sort((a, b) => b.localeCompare(a));
  const lines = days.slice(0, max).map((day) => {
    const markers = byDay.get(day) ?? [];
    const phrases: string[] = [];
    const added = markers.filter((marker) => marker.kind === "scope_added");
    if (added.length > 0) {
      const total = added.reduce((sum, marker) => sum + marker.count, 0);
      const without = added
        .filter((marker) => !marker.change_label)
        .reduce((sum, marker) => sum + marker.count, 0);
      const noun = total === 1 ? "requirement" : "requirements";
      phrases.push(
        without === 0
          ? `${total} ${noun} added with the ${changeLabel} label`
          : without === total
            ? `${total} ${noun} added without the ${changeLabel} label`
            : `${total} ${noun} added (${without} without the ${changeLabel} label)`,
      );
    }
    const entries = markers
      .filter((marker) => marker.kind !== "scope_added")
      .map((marker) => changeEntry(marker, row, changeLabel));
    phrases.push(...entries.map((entry) => entry.words));
    const pushed =
      added.some((marker) => !marker.change_label) || entries.some((entry) => entry.pushed);
    return { day, text: `${shortDay(day)} · ${phrases.join(" · ")}`, pushed };
  });
  const scope = row.scope;
  const lead =
    row.baseline && scope
      ? `Since planning (${shortDay(row.baseline.day)}): ${
          scope.at_baseline === scope.now
            ? `${scope.now} requirements, unchanged`
            : `${scope.at_baseline} → ${scope.now} requirements`
        }`
      : row.access === "panel" && !row.baseline
        ? "Planning is not marked complete yet"
        : null;
  return { lead, lines, more: Math.max(0, days.length - max) };
}

// ---- what is drawn, for the legend ----------------------------------------------------

export type Drawn = {
  done: boolean;
  toDo: boolean;
  likely: boolean;
  likelyTones: BadgeTone[];
  committed: boolean;
  change: boolean;
};

/** Only what the rows draw gets a legend entry, five at the most. */
export function drawn(rows: Row[], scale: Scale, today: string): Drawn {
  const shapes = rows.map((row) => rowShape(row, scale, today));
  const tones = [
    ...new Set(shapes.flatMap((shape) => (shape.likely ? [shape.likely.tone] : []))),
  ] as BadgeTone[];
  return {
    done: shapes.some((shape) => shape.fill !== null && shape.share !== null && shape.share > 0),
    toDo: shapes.length > 0,
    likely: tones.length > 0,
    likelyTones: tones,
    committed: shapes.some((shape) => shape.committed !== null),
    change: rows.some((row) => row.markers.length > 0),
  };
}

// ---- keyboard ------------------------------------------------------------------------

/** Where focus is: a row's label (dot null) or one of its dots. */
export type Focus = { row: number; glyph: number | null };

/**
 * The arrow keys: right and left move along a row's dots (left from the first
 * goes back to the row), up and down to the row above or below, keeping the
 * place along it; Home and End go to a row's first and last dot. Null when the
 * key moves nothing, so the page keeps it.
 */
export function moveFocus(focus: Focus, key: string, glyphCounts: number[]): Focus | null {
  const count = glyphCounts[focus.row] ?? 0;
  switch (key) {
    case "ArrowRight":
      if (count === 0) return null;
      if (focus.glyph === null) return { row: focus.row, glyph: 0 };
      return focus.glyph < count - 1 ? { row: focus.row, glyph: focus.glyph + 1 } : null;
    case "ArrowLeft":
      if (focus.glyph === null) return null;
      return { row: focus.row, glyph: focus.glyph > 0 ? focus.glyph - 1 : null };
    case "ArrowDown":
    case "ArrowUp": {
      const row = focus.row + (key === "ArrowDown" ? 1 : -1);
      if (row < 0 || row >= glyphCounts.length) return null;
      const there = glyphCounts[row];
      return {
        row,
        glyph: focus.glyph === null || there === 0 ? null : Math.min(focus.glyph, there - 1),
      };
    }
    case "Home":
      return count > 0 ? { row: focus.row, glyph: 0 } : null;
    case "End":
      return count > 0 ? { row: focus.row, glyph: count - 1 } : null;
    default:
      return null;
  }
}

// ---- the rows drawn ---------------------------------------------------------------------

/**
 * The project's releases sit folded under its row (DESIGN §5.2): the rows to
 * draw, and how many releases the fold holds. A release that heads the read
 * (``?release=``) is its first row and is never folded.
 */
export function foldReleases(rows: Row[], open: boolean): { rows: Row[]; releases: number } {
  const folded = (row: Row, index: number) => index > 0 && row.scope_kind === "release";
  const releases = rows.filter(folded).length;
  if (open || releases === 0) return { rows, releases };
  return { rows: rows.filter((row, index) => !folded(row, index)), releases };
}

// ---- a phone ---------------------------------------------------------------------------

/** How far into the box today and the nearest committed date ahead keep from its edges. */
const EDGE = 16;

/**
 * Where the box opens scrolled on a narrow screen: on today and the committed
 * dates ahead of it. Today sits a quarter in, so the latest changes show too;
 * when the nearest committed date ahead would fall past the right edge, the box
 * opens further on, as far as keeping today just inside the left edge. Never
 * past either end.
 */
export function openingScroll(
  scale: Scale,
  today: string,
  viewport: number,
  committed: readonly (string | null)[] = [],
): number {
  if (scale.width <= viewport) return 0;
  const todayX = xOf(scale, today);
  const ahead = committed
    .filter((day): day is string => day !== null && day > today)
    .map((day) => xOf(scale, day));
  const nearest = ahead.length > 0 ? Math.min(...ahead) : null;
  let target = todayX - viewport * 0.25;
  if (nearest !== null && nearest > target + viewport - EDGE) {
    target = Math.min(nearest - viewport + EDGE, todayX - EDGE);
  }
  return Math.round(Math.min(Math.max(target, 0), scale.width - viewport));
}

// ---- the table -------------------------------------------------------------------------

export type TableRow = {
  row: string;
  name: string;
  start: string;
  committed: string;
  moves: string;
  likely: string;
  team: string;
  done: string;
  events: string[];
};

/**
 * One row as the table lists it: start, the committed date and its moves, the
 * likely finish, the team's date, done, and what changed by date.
 */
export function tableRow(row: Row, changeLabel: string): TableRow {
  const history = row.committed.history;
  const moves = history
    .slice(1)
    .map(
      (tick, i) => `${shortDay(history[i].date)} → ${shortDay(tick.date)} on ${shortDay(tick.on)}`,
    )
    .filter((_line, i) => history[i + 1].date !== history[i].date);
  const events = [...row.markers]
    .sort((a, b) => a.day.localeCompare(b.day))
    .map((marker) => {
      const entry = changeEntry(marker, row, changeLabel);
      const keys = marker.items.map((item) => item.key);
      const named =
        keys.length > 0 ? ` (${keys.slice(0, 5).join(", ")}${keys.length > 5 ? ", …" : ""})` : "";
      return `${shortDay(marker.day)}: ${entry.words}${named}`;
    });
  return {
    row: row.row,
    name: row.name,
    start: row.baseline
      ? `${shortDay(row.start)} (planning)`
      : `${shortDay(row.start)} (no baseline)`,
    committed: row.committed.date ? shortDay(row.committed.date) : "No date",
    moves: moves.length > 0 ? moves.join("; ") : "Never moved",
    likely: row.forecast
      ? likelyRange(row.forecast.p50, row.forecast.p85).replace(/^likely /, "")
      : (row.forecast_reason ?? "—"),
    team: row.team.date
      ? `${shortDay(row.team.date)}${row.team.key ? ` (${row.team.key})` : ""}${
          row.team.undated > 0 ? `, ${row.team.undated} undated` : ""
        }`
      : row.team.undated > 0
        ? `None given, ${row.team.undated} undated`
        : "—",
    done: progressLabel(row) || "—",
    events,
  };
}

/** The server's sentence for a row; one exists for each row it sends. */
export function sentenceOf(timeline: TimelineResponse, row: Row): string {
  return (
    timeline.text.find((text) => text.row === row.row && text.project_id === row.project_id)
      ?.sentence ?? row.name
  );
}

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
