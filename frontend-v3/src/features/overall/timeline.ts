// The timeline per project and pod: one row per scope from planning to its
// committed date, with progress, the 50% to 85% band, the team's date, the
// committed date's earlier values as hollow ticks, and the impact markers on the
// day each thing happened. Pure geometry, words and keyboard moves; the
// components draw what these return. Type imports and runtime imports by their
// .ts path only, so `node --test` runs it as written.
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
/** Past twelve weeks a lane merges its markers per week, so a glyph keeps its room. */
export const WEEK_MERGE_DAYS = 84;
/** Two glyphs on one lane keep this far apart; the later one moves right. */
export const GLYPH_GAP = 18;
/** The most items a marker's list names; the rest are counted. */
export const LIST_ITEMS = 10;

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
  /** Markers merge per week on a long axis. */
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
    width: Math.ceil(days * pxPerDay),
    weekly: days > WEEK_MERGE_DAYS,
  };
}

/** A day's x, kept inside the drawing. */
export function xOf(scale: Scale, iso: string): number {
  const x = (dayNumber(iso) - scale.start) * scale.pxPerDay;
  return Math.round(Math.min(Math.max(x, 0), scale.width) * 10) / 10;
}

/**
 * The axis labels: each month's first day, and its 15th when a fortnight is
 * wide enough to say it, thinned so no two run together. ``today`` is the day
 * line's own label: a month label it would touch gives way to it.
 */
export function axisLabels(scale: Scale, today?: { x: number; label: string }): AxisLabel[] {
  const wide = scale.pxPerDay * 14 >= 64;
  const candidates: { x: number; label: string; anchor: "middle" }[] = [];
  for (let day = scale.start; day <= scale.end; day++) {
    const date = new Date(day * DAY_MS);
    const dom = date.getUTCDate();
    if (dom === 1 || (wide && dom === 15)) {
      candidates.push({
        x: (day - scale.start) * scale.pxPerDay,
        label: `${dom} ${MONTHS[date.getUTCMonth()]}`,
        anchor: "middle",
      });
    }
  }
  // Month starts first, so a 15th gives way to a 1st it would touch.
  candidates.sort((a, b) => Number(!a.label.startsWith("1 ")) - Number(!b.label.startsWith("1 ")));
  if (!today) return thinAxisLabels(candidates, scale.width);
  // The day line's label goes first, so it is kept; it is drawn on its own.
  const marked = ` ${today.label}`;
  return thinAxisLabels(
    [{ x: today.x, label: marked, anchor: "middle" }, ...candidates],
    scale.width,
  ).filter((label) => label.label !== marked);
}

// ---- one row -------------------------------------------------------------------------

export type Tick = { x: number; date: string; on: string };

export type RowShape = {
  /** The span: from the baseline (or the first kept day) to the committed date. */
  start: number;
  end: number;
  /** No committed date: the span runs to the right edge, dashed. */
  open: boolean;
  /** No planning baseline: the span starts at the first kept day, faded. */
  faded: boolean;
  /** Where the done fill ends; only on a span with a committed end. */
  fill: number | null;
  /** Done of total, 0 to 1. */
  share: number | null;
  band: { from: number; to: number; tone: BadgeTone } | null;
  /** The band runs past the committed date: late, visible at a glance. */
  overhang: boolean;
  team: { x: number; hollow: boolean } | null;
  committed: number | null;
  /** The committed date's earlier values, oldest first. */
  earlier: Tick[];
  /** Jira's release date, when it is not the committed date. */
  jira: number | null;
  /** A pod's row: its project's committed date. */
  reference: number | null;
};

export function rowShape(row: Row, scale: Scale): RowShape {
  const committedDate = row.committed.date;
  const start = xOf(scale, row.start);
  const committed = committedDate ? xOf(scale, committedDate) : null;
  const end = committed !== null ? Math.max(committed, start) : scale.width;
  const progress = row.progress;
  const share =
    progress && progress.total > 0
      ? Math.min(1, Math.max(0, progress.done / progress.total))
      : null;
  const forecast = row.forecast;
  const target = committedDate ?? row.jira_release_date;
  const history = row.committed.history;
  const earlier = history
    .slice(0, -1)
    .filter((tick) => tick.date !== committedDate)
    .filter((tick, i, all) => all.findIndex((other) => other.date === tick.date) === i)
    .map((tick) => ({ x: xOf(scale, tick.date), date: tick.date, on: tick.on }));
  return {
    start,
    end,
    open: committed === null,
    faded: row.baseline === null,
    fill: committed !== null && share !== null ? start + (end - start) * share : null,
    share,
    band: forecast
      ? {
          from: xOf(scale, forecast.p50),
          to: Math.max(xOf(scale, forecast.p85), xOf(scale, forecast.p50) + 2),
          tone: forecastTone(target, forecast.p50, forecast.p85),
        }
      : null,
    overhang: Boolean(forecast && committedDate && forecast.p85 > committedDate),
    team: row.team.date ? { x: xOf(scale, row.team.date), hollow: row.team.undated > 0 } : null,
    committed,
    earlier,
    jira:
      row.jira_release_date && row.jira_release_date !== committedDate
        ? xOf(scale, row.jira_release_date)
        : null,
    reference: row.reference_date ? xOf(scale, row.reference_date) : null,
  };
}

/** The verdict's chip: its word and tone; a dates row without a forecast says so. */
export function verdictChip(verdict: Verdict | null): { label: string; tone: BadgeTone } {
  if (verdict === null) return { label: "No forecast kept", tone: "neutral" };
  return { label: VERDICT_LABELS[verdict], tone: toneForVerdict(verdict) };
}

/** "8 of 22 · 36%", or on a phone "36%"; story points say so. Nothing on a dates row. */
export function progressLabel(row: Row, short = false): string {
  const progress = row.progress;
  if (!progress) return "";
  if (progress.total <= 0) return "Nothing in scope";
  const percent = Math.round((progress.done / progress.total) * 100);
  if (short) return `${percent}%`;
  const points = progress.unit === "story points" ? " pts" : "";
  return `${number(progress.done)} of ${number(progress.total)}${points} · ${percent}%`;
}

/** "18 → 22 requirements since planning", when the row has a baseline. */
export function scopeLabel(row: Row): string | null {
  if (!row.scope) return null;
  const { at_baseline: then, now } = row.scope;
  return then === now ? `${now}, unchanged since planning` : `${then} → ${now} since planning`;
}

/** "Moved twice, +12 days": the committed date's history in a few words. */
export function movesLabel(row: Row): string | null {
  const dated = row.committed.history.map((tick) => tick.date);
  const moves = dated.filter((day, i) => i > 0 && day !== dated[i - 1]).length;
  if (moves === 0 || !row.committed.date) return null;
  const days = dayNumber(row.committed.date) - dayNumber(dated[0]);
  const times = moves === 1 ? "once" : moves === 2 ? "twice" : `${moves} times`;
  const signed = days > 0 ? `+${days}` : days < 0 ? `−${-days}` : "±0";
  return `Moved ${times}, ${signed} days`;
}

// ---- the impact lane -------------------------------------------------------------------

/** What a lane draws, each with its own shape (never colour alone). */
export type GlyphKind =
  | "added_label"
  | "added"
  | "removed"
  | "reopened"
  | "late"
  | "bug"
  | "blocker_opened"
  | "blocker_closed";

export const GLYPH_ORDER: GlyphKind[] = [
  "added_label",
  "added",
  "removed",
  "reopened",
  "late",
  "bug",
  "blocker_opened",
  "blocker_closed",
];

/** A marker's glyph; the committed date's moves are the row's ticks, not a glyph. */
export function glyphKind(marker: Pick<Marker, "kind" | "change_label">): GlyphKind | null {
  switch (marker.kind) {
    case "scope_added":
      return marker.change_label ? "added_label" : "added";
    case "scope_removed":
      return "removed";
    case "reopened":
      return "reopened";
    case "late":
      return "late";
    case "bug":
      return "bug";
    case "blocker_opened":
      return "blocker_opened";
    case "blocker_closed":
      return "blocker_closed";
    default:
      return null;
  }
}

export type Glyph = {
  id: string;
  kind: GlyphKind;
  /** The day it happened, or the Monday of its week on a long axis. */
  day: string;
  weekly: boolean;
  x: number;
  count: number;
  items: MarkerItem[];
  /** More items than the list names. */
  more: number;
  /** An addition without the change label with an open Jira hygiene finding on it. */
  ringed: boolean;
  label: string;
};

/**
 * A row's glyphs: markers of one kind on one day merged into one glyph with a
 * count (per week on a long axis), left to right, nudged apart so none hides
 * another. The committed date's moves are left to the ticks.
 */
export function rowGlyphs(row: Row, scale: Scale, changeLabel: string): Glyph[] {
  const buckets = new Map<string, Glyph>();
  for (const marker of row.markers) {
    const kind = glyphKind(marker);
    if (kind === null) continue;
    const day = scale.weekly ? weekOf(marker.day) : marker.day;
    const id = `${day}:${kind}`;
    const found = buckets.get(id);
    if (found) {
      found.count += marker.count;
      found.items = [...found.items, ...marker.items];
      found.ringed ||= marker.items.some((item) => item.hygiene === true);
      // A week's glyph sits on its first event, never ahead of what happened.
      found.x = Math.min(found.x, xOf(scale, marker.day));
      continue;
    }
    buckets.set(id, {
      id,
      kind,
      day,
      weekly: scale.weekly,
      x: xOf(scale, marker.day),
      count: marker.count,
      items: [...marker.items],
      more: 0,
      ringed: marker.items.some((item) => item.hygiene === true),
      label: "",
    });
  }
  const glyphs = [...buckets.values()].sort(
    (a, b) => a.x - b.x || GLYPH_ORDER.indexOf(a.kind) - GLYPH_ORDER.indexOf(b.kind),
  );
  let last = -Infinity;
  for (const glyph of glyphs) {
    glyph.x = Math.max(glyph.x, last + GLYPH_GAP);
    last = glyph.x;
    glyph.more = Math.max(0, glyph.count - Math.min(glyph.items.length, LIST_ITEMS));
    glyph.items = glyph.items.slice(0, LIST_ITEMS);
    glyph.label = glyphLabel(glyph.kind, glyph.count, glyph.day, glyph.weekly, changeLabel);
  }
  return glyphs;
}

/** What one glyph says: "9 Oct: 2 requirements added without the CR label". */
export function glyphLabel(
  kind: GlyphKind,
  count: number,
  day: string,
  weekly: boolean,
  changeLabel: string,
): string {
  const when = weekly ? `Week of ${shortDay(day)}` : shortDay(day);
  return `${when}: ${glyphWords(kind, count, changeLabel)}`;
}

export function glyphWords(kind: GlyphKind, count: number, changeLabel: string): string {
  const requirements = count === 1 ? "1 requirement" : `${count} requirements`;
  switch (kind) {
    case "added_label":
      return `${requirements} added with the ${changeLabel} label`;
    case "added":
      return `${requirements} added without the ${changeLabel} label`;
    case "removed":
      return `${requirements} left the scope`;
    case "reopened":
      return `${requirements} reopened`;
    case "late":
      return `${requirements} passed ${count === 1 ? "its" : "their"} date while open`;
    case "bug":
      return count === 1 ? "1 bug opened" : `${count} bugs opened`;
    case "blocker_opened":
      return count === 1 ? "1 blocker opened" : `${count} blockers opened`;
    case "blocker_closed":
      return count === 1 ? "1 blocker cleared" : `${count} blockers cleared`;
  }
}

/** The legend's word for each glyph. */
export function glyphLegend(kind: GlyphKind, changeLabel: string): string {
  return {
    added_label: `Added with the ${changeLabel} label`,
    added: `Added without the ${changeLabel} label`,
    removed: "Left the scope",
    reopened: "Reopened",
    late: "Passed its date while open",
    bug: "Bug opened",
    blocker_opened: "Blocker opened",
    blocker_closed: "Blocker cleared",
  }[kind];
}

/** One item of a glyph's list, after its key and title: its stage, label, who and what. */
export function itemNote(
  item: MarkerItem,
  kind: GlyphKind,
  changeLabel: string,
  stageWord: (stage: NonNullable<MarkerItem["stage"]>) => string,
): string {
  const parts: string[] = [];
  if (item.stage) parts.push(stageWord(item.stage));
  if (kind === "added" || kind === "added_label") {
    parts.push(item.change_label ? `${changeLabel} label` : `no ${changeLabel} label`);
    if (item.hygiene) parts.push("open in Jira hygiene");
  }
  if (item.added_by) parts.push(`added by ${item.added_by}`);
  if (item.waits_on) parts.push(`waits on: ${item.waits_on}`);
  return parts.join(" · ");
}

// ---- what is drawn, for the legend ----------------------------------------------------

export type Drawn = {
  done: boolean;
  band: boolean;
  bandTones: BadgeTone[];
  team: boolean;
  teamHollow: boolean;
  committed: boolean;
  earlier: boolean;
  jira: boolean;
  reference: boolean;
  open: boolean;
  faded: boolean;
  glyphs: GlyphKind[];
  ringed: boolean;
};

/** Only what the rows draw gets a legend entry; a swatch is the colour drawn. */
export function drawn(rows: Row[], scale: Scale, changeLabel: string): Drawn {
  const shapes = rows.map((row) => rowShape(row, scale));
  const glyphs = rows.flatMap((row) => rowGlyphs(row, scale, changeLabel));
  const tones = [
    ...new Set(shapes.flatMap((shape) => (shape.band ? [shape.band.tone] : []))),
  ] as BadgeTone[];
  return {
    done: shapes.some((shape) => shape.fill !== null),
    band: shapes.some((shape) => shape.band !== null),
    bandTones: tones,
    team: shapes.some((shape) => shape.team !== null),
    teamHollow: shapes.some((shape) => shape.team?.hollow),
    committed: shapes.some((shape) => shape.committed !== null),
    earlier: shapes.some((shape) => shape.earlier.length > 0),
    jira: shapes.some((shape) => shape.jira !== null),
    reference: shapes.some((shape) => shape.reference !== null),
    open: shapes.some((shape) => shape.open),
    faded: shapes.some((shape) => shape.faded),
    glyphs: GLYPH_ORDER.filter((kind) => glyphs.some((glyph) => glyph.kind === kind)),
    ringed: glyphs.some((glyph) => glyph.ringed),
  };
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

// ---- keyboard ------------------------------------------------------------------------

/** Where focus is: a row's label (glyph null) or one of its glyphs. */
export type Focus = { row: number; glyph: number | null };

/**
 * The arrow keys: right and left move along a row's glyphs (left from the first
 * goes back to the row), up and down to the row above or below, keeping the
 * place along it; Home and End go to a row's first and last glyph. Null when the
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

// ---- a phone ---------------------------------------------------------------------------

/** How far into the box today and the nearest committed date ahead keep from its edges. */
const EDGE = 16;

/**
 * Where the box opens scrolled on a narrow screen: on today and the committed
 * dates ahead of it. Today sits a quarter in, so the latest markers show too;
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
  p50: string;
  p85: string;
  team: string;
  done: string;
  events: string[];
};

/**
 * One row as the table lists it: start, the committed date and its moves, 50%
 * and 85%, the team's date, done, and the events by date.
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
    .map((marker) => eventLine(marker, row, changeLabel));
  return {
    row: row.row,
    name: row.name,
    start: row.baseline
      ? `${shortDay(row.start)} (planning)`
      : `${shortDay(row.start)} (no baseline)`,
    committed: row.committed.date ? shortDay(row.committed.date) : "No date",
    moves: moves.length > 0 ? moves.join("; ") : "Never moved",
    p50: row.forecast ? shortDay(row.forecast.p50) : "—",
    p85: row.forecast ? shortDay(row.forecast.p85) : (row.forecast_reason ?? "—"),
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

function eventLine(marker: Marker, row: Row, changeLabel: string): string {
  const kind = glyphKind(marker);
  if (kind === null) {
    const index = row.committed.history.findIndex((tick) => tick.on === marker.day);
    const tick = index >= 0 ? row.committed.history[index] : null;
    const before = index > 0 ? row.committed.history[index - 1] : null;
    const words = tick
      ? before
        ? `committed date moved ${shortDay(before.date)} → ${shortDay(tick.date)}`
        : `committed date set to ${shortDay(tick.date)}`
      : "committed date changed";
    return `${shortDay(marker.day)}: ${words}`;
  }
  const keys = marker.items.map((item) => item.key);
  const named =
    keys.length > 0 ? ` (${keys.slice(0, 5).join(", ")}${keys.length > 5 ? ", …" : ""})` : "";
  return `${shortDay(marker.day)}: ${glyphWords(kind, marker.count, changeLabel)}${named}`;
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
