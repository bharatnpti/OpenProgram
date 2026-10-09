// Pure layout and wording for Daily's pictures (D1 date bar, D2 stage strip, D4 gate
// rings, D3 who acts), drawn from the preview's `facts`. Types, and pure modules
// imported by their .ts path, so `node --test` runs this.
//
// The facts are the report itself, structured by the server from the same parts as
// its lines; a fact the sent text leaves unsaid is null. So a picture never says
// more than the message: every word here is the server's, a count, a date, or a
// label for one of them.
import type { components } from "../../api/generated";
import { formatDay } from "../../lib/format.ts";
import { STAGE_LABELS, STAGE_ORDER } from "../../lib/status.ts";

type Schemas = components["schemas"];
export type ReportFacts = Schemas["ReportFactsResponse"];
export type DateFacts = Schemas["ReportDateFactsResponse"];
export type ProgressFacts = Schemas["ReportProgressFactsResponse"];
export type StageMoveFacts = Schemas["ReportStageMoveResponse"];
export type GateFacts = Schemas["ReportGateFactsResponse"];
export type ImportantFacts = Schemas["ReportImportantFactsResponse"];
export type OwnerAsks = Schemas["ReportOwnerAsksResponse"];
export type AskFacts = Schemas["ReportAskFactsResponse"];
export type NeedType = Schemas["NeedType"];
export type Stage = Schemas["DeliveryStage"];
type VerdictKey = Schemas["Verdict"];
type Table = Schemas["ReportTableResponse"];

/** The colour a verdict or gate state takes; grey is no data yet. */
export type Tone = "green" | "amber" | "red" | "neutral";

// ---- words --------------------------------------------------------------------------

/** The verdict as the report's delivery line words it ("Delivery …: at risk."). */
export const VERDICT_WORDS: Record<VerdictKey, string> = {
  on_track: "on track",
  at_risk: "at risk",
  off_track: "off track",
  done: "done",
  no_date: "no delivery date set",
  not_enough_data: "not enough history to forecast",
};

export function verdictTone(verdict: VerdictKey): Tone {
  if (verdict === "on_track" || verdict === "done") return "green";
  if (verdict === "at_risk") return "amber";
  if (verdict === "off_track" || verdict === "no_date") return "red";
  return "neutral";
}

/** The ask kinds as the report starts each line ("Fix: …"). */
export const NEED_WORDS: Record<NeedType, string> = {
  fix: "Fix",
  decision: "Decision",
  answer: "Answer",
  review: "Review",
};

/** "Tue 15 Dec 2026", as the report writes a date. */
export function reportDate(iso: string): string {
  return `${formatDay(iso)} ${iso.slice(0, 4)}`;
}

/** "a", "a and b", "a, b and c". */
export function joined(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** "28%" and "complete: 5 of 18 …": the progress line split for a large number. */
export function progressParts(line: string): { lead: string | null; rest: string } {
  const match = /^(\d+%) (.+)$/.exec(line);
  return match ? { lead: match[1], rest: match[2] } : { lead: null, rest: line };
}

// ---- days ---------------------------------------------------------------------------

const DAY_MS = 86_400_000;
const dayNumber = (iso: string) => Math.round(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / DAY_MS);
const isoOf = (day: number) => new Date(day * DAY_MS).toISOString().slice(0, 10);
/** The last day of the month `day` falls in. */
function monthEnd(day: number): number {
  const date = new Date(day * DAY_MS);
  return Math.round(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0) / DAY_MS);
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ---- D1: the date bar ---------------------------------------------------------------

export type BarVariant = "wide" | "narrow";

type Text = {
  x: number;
  y: number;
  text: string;
  anchor: "start" | "middle" | "end";
  /** Font size, for keeping labels apart; 12.5 when not given. */
  size?: number;
};

export type DateBarView = {
  /** "Delivery Tue 15 Dec 2026" and the verdict's words, as In short says them. */
  date: string;
  verdict: string;
  tone: Tone;
  /** "committed by Mina Patel · moved once, +14 days", when the report says it. */
  sub: string | null;
  /** Drawn only when there is a date to place. */
  chart: DateBarChart | null;
  /** Said under the bar: why there is no forecast, when the report says so. */
  caption: string | null;
  legend: { committed: boolean; team: boolean; forecast: "range" | "p85" | null };
};

export type DateBarChart = {
  width: number;
  height: number;
  label: string;
  bar: { x: number; y: number; width: number; height: number };
  ticks: { x: number; label: string }[];
  today: Text;
  committed: { x: number; top: number; bottom: number; label: Text; past: boolean } | null;
  forecast: {
    tone: Tone;
    from: number | null;
    to: number;
    labels: Text[];
  } | null;
  /** "No forecast yet", in grey, where a forecast would be. */
  noForecast: Text | null;
  team: { x: number; y: number; title: string } | null;
  /** Bottom row: the team's date, the history count. */
  notes: Text[];
  /** The team's date in words under the bar, when the bottom row has no room for it. */
  teamNote: string | null;
};

const BAR_GEOMETRY = {
  wide: { width: 560, height: 104, x0: 16, x1: 544, barY: 46, top: 16, ticks: 39, bottom: 94 },
  narrow: { width: 340, height: 92, x0: 8, x1: 332, barY: 42, top: 14, ticks: 34, bottom: 88 },
} as const;

/** Roughly how wide a label is, for keeping two from overlapping. */
const textWidth = (text: string, size = 12.5) => text.length * size * 0.55;

function shortDay(iso: string): string {
  const [, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${day} ${MONTHS[month - 1]}`;
}

/**
 * D1: today to the end of the month of the furthest date, with the committed date
 * as a line, the forecast as a band (50% to 85%) or a tick (85% only), and the
 * team's latest date as a diamond. Each is drawn only when the report states it.
 */
export function dateBar(
  facts: DateFacts,
  today: string,
  variant: BarVariant = "wide",
): DateBarView {
  const target = facts.target;
  const date = target
    ? `Delivery ${reportDate(target)}${facts.target_source === "jira_release" ? " (the Jira release date)" : ""}`
    : "Delivery";
  const sub = target ? committedSub(facts) : null;
  const caption =
    facts.no_forecast_reason && facts.history_days === null ? facts.no_forecast_reason : null;
  const forecast = facts.p85 ? (facts.p50 ? "range" : "p85") : null;
  const dated = [target, facts.p50, facts.p85, facts.team_latest].filter(Boolean);
  return {
    date,
    verdict: VERDICT_WORDS[facts.verdict],
    tone: verdictTone(facts.verdict),
    sub,
    chart: dated.length ? dateChart(facts, today, variant) : null,
    caption,
    legend: { committed: Boolean(target), team: Boolean(facts.team_latest), forecast },
  };
}

function committedSub(facts: DateFacts): string | null {
  const parts: string[] = [];
  if (facts.committed_by) parts.push(`committed by ${facts.committed_by}`);
  if (facts.times_moved && facts.moved_days) {
    const times = facts.times_moved === 1 ? "once" : `${facts.times_moved} times`;
    const days = Math.abs(facts.moved_days);
    parts.push(`moved ${times}, ${facts.moved_days > 0 ? "+" : "−"}${plural(days, "day", "days")}`);
  }
  return parts.length ? parts.join(" · ") : null;
}

function dateChart(facts: DateFacts, today: string, variant: BarVariant): DateBarChart {
  const g = BAR_GEOMETRY[variant];
  const start = dayNumber(today);
  const days = [facts.target, facts.p50, facts.p85, facts.team_latest]
    .filter((iso): iso is string => Boolean(iso))
    .map(dayNumber);
  const end = monthEnd(Math.max(start + 21, ...days));
  const x = (day: number) =>
    g.x0 + ((Math.min(Math.max(day, start), end) - start) / (end - start)) * (g.x1 - g.x0);
  const barHeight = 24;
  const mid = g.barY + barHeight / 2;
  const small = variant === "narrow";
  const size = small ? 11.5 : 12.5;

  const ticks: DateBarChart["ticks"] = [];
  for (let day = start + 1; day <= end; day += 1) {
    if (isoOf(day).endsWith("-01")) ticks.push({ x: x(day), label: shortDay(isoOf(day)) });
  }
  const todayText = `Today · ${formatDay(today)}`;

  let committed: DateBarChart["committed"] = null;
  if (facts.target) {
    const past = dayNumber(facts.target) < start;
    const cx = x(dayNumber(facts.target));
    const word = facts.target_source === "jira_release" ? "Jira release" : "Committed";
    const text = past ? `${word} · ${formatDay(facts.target)}, past` : word;
    // Clear of "Today · …" at the left; to the right of the line when it is that close.
    const clearOfToday = g.x0 + textWidth(todayText) + 10;
    const middle = cx - textWidth(text) / 2 >= clearOfToday && cx + textWidth(text) / 2 <= g.width;
    committed = {
      x: cx,
      top: g.top + 6,
      bottom: g.barY + barHeight + 8,
      past,
      label: middle
        ? { x: cx, y: g.top, text, anchor: "middle" }
        : cx + textWidth(text) / 2 > g.width
          ? { x: g.width - 2, y: g.top, text, anchor: "end" }
          : { x: Math.max(cx + 6, clearOfToday), y: g.top, text, anchor: "start" },
    };
  }

  let forecast: DateBarChart["forecast"] = null;
  const notes: Text[] = [];
  const labels: Text[] = [];
  if (facts.p85) {
    const to = x(dayNumber(facts.p85));
    const from = facts.p50 ? x(dayNumber(facts.p50)) : null;
    const tone = verdictTone(facts.verdict);
    const day = (iso: string) => (small ? shortDay(iso) : formatDay(iso));
    if (from !== null && facts.p50) {
      labels.push(
        small
          ? { x: from + 3, y: g.bottom, text: `50% · ${day(facts.p50)}`, anchor: "end", size }
          : { x: from, y: g.bottom, text: `50% · ${day(facts.p50)}`, anchor: "middle", size },
      );
    }
    labels.push(
      small
        ? { x: g.x1, y: g.bottom, text: `85% · ${day(facts.p85)}`, anchor: "end", size }
        : { x: to, y: g.bottom, text: `85% · ${day(facts.p85)}`, anchor: "middle", size },
    );
    forecast = { tone, from, to, labels: fitRow(labels, g.width) };
  }

  let noForecast: Text | null = null;
  if (!facts.p85 && facts.target) {
    const text = "No forecast yet";
    const room = textWidth(text, 11.5) + 16;
    const line = committed ? committed.x : g.x1;
    // Before the committed date where it fits, else after it.
    const [from, to] = line - g.x0 >= room ? [g.x0, line] : [line, g.x1];
    noForecast =
      to - from >= room ? { x: (from + to) / 2, y: mid + 4, text, anchor: "middle" } : null;
  }
  if (facts.history_days !== null && facts.history_needed !== null) {
    const count = `${facts.history_days} of ${facts.history_needed} working days`;
    notes.push({
      x: g.x1,
      y: g.bottom,
      text: small ? count : `History: ${count}`,
      anchor: "end",
      size,
    });
  }

  let team: DateBarChart["team"] = null;
  let teamNote: string | null = null;
  if (facts.team_latest) {
    const day = dayNumber(facts.team_latest);
    const when =
      day === start
        ? "today"
        : day < start
          ? `${formatDay(facts.team_latest)}, past`
          : formatDay(facts.team_latest);
    const key = facts.team_latest_key ? ` · ${facts.team_latest_key}` : "";
    team = {
      x: x(day),
      y: mid,
      title: `Team's latest date, ${formatDay(facts.team_latest)}${facts.team_latest_key ? `, ${facts.team_latest_key}` : ""}`,
    };
    const text = small ? `Team${key} · ${when}` : `Team's latest date${key} · ${when}`;
    const left: Text = { x: g.x0, y: g.bottom, text, anchor: "start", size };
    // The forecast's labels and the history count share the bottom row: the team's
    // date goes there where it fits, else in words under the bar.
    const row = [...(forecast?.labels ?? []), ...notes];
    if (row.some((other) => overlaps(left, other))) teamNote = `Team's latest date${key} · ${when}`;
    else notes.unshift(left);
  }

  return {
    width: g.width,
    // Without a bottom row the bar needs no room under it.
    height: notes.length || forecast ? g.height : g.barY + barHeight + 12,
    label: dateBarLabel(facts, today, end),
    bar: { x: g.x0, y: g.barY, width: g.x1 - g.x0, height: barHeight },
    ticks,
    today: { x: g.x0, y: g.top, text: todayText, anchor: "start" },
    committed,
    forecast,
    noForecast,
    team,
    notes,
    teamNote,
  };
}

function span(text: Text): [number, number] {
  const width = textWidth(text.text, text.size);
  if (text.anchor === "start") return [text.x, text.x + width];
  if (text.anchor === "end") return [text.x - width, text.x];
  return [text.x - width / 2, text.x + width / 2];
}

function overlaps(a: Text, b: Text): boolean {
  if (a.y !== b.y || !a.text || !b.text) return false;
  const [a0, a1] = span(a);
  const [b0, b1] = span(b);
  return a0 < b1 + 6 && b0 < a1 + 6;
}

/** Labels on one row moved apart where they would overlap, and kept inside the frame. */
function fitRow(labels: Text[], width: number): Text[] {
  const placed: Text[] = [];
  for (const label of labels) {
    let next = { ...label };
    const [, right] = span(next);
    if (right > width) next = { ...next, x: width - 2, anchor: "end" };
    const before = placed[placed.length - 1];
    if (before && overlaps(before, next)) {
      const [, beforeRight] = span(before);
      next = { ...next, x: beforeRight + 8, anchor: "start" };
      if (span(next)[1] > width) {
        placed[placed.length - 1] = { ...before, x: span(before)[0] - 4, anchor: "end" };
        next = { ...label, x: width - 2, anchor: "end" };
      }
    }
    placed.push(next);
  }
  return placed;
}

function dateBarLabel(facts: DateFacts, today: string, end: number): string {
  const month = new Date(end * DAY_MS).toLocaleDateString("en-GB", {
    month: "long",
    timeZone: "UTC",
  });
  const parts = [`Delivery dates from today, ${formatDay(today)}, to the end of ${month}.`];
  if (facts.target) {
    const word =
      facts.target_source === "jira_release" ? "Jira's release date" : "The committed date";
    parts.push(`${word} is ${formatDay(facts.target)}: ${VERDICT_WORDS[facts.verdict]}.`);
  }
  if (facts.p50 && facts.p85) {
    parts.push(
      `The forecast is 50% likely by ${formatDay(facts.p50)}, 85% likely by ${formatDay(facts.p85)}.`,
    );
  } else if (facts.p85) {
    parts.push(`The forecast is 85% likely by ${formatDay(facts.p85)}.`);
  } else if (facts.target) {
    parts.push(
      facts.history_days !== null && facts.history_needed !== null
        ? `No forecast yet: ${facts.history_days} of ${facts.history_needed} working days of history.`
        : "No forecast yet.",
    );
  }
  if (facts.team_latest) {
    const day = facts.team_latest === today ? "today" : formatDay(facts.team_latest);
    const key = facts.team_latest_key ? `, ${facts.team_latest_key}` : "";
    parts.push(`The team's latest date is ${day}${key}.`);
  }
  return parts.join(" ");
}

// ---- D2: the stage strip ------------------------------------------------------------

export type StageTile = {
  stage: Stage;
  label: string;
  count: number;
  /** "−1", "+1", "1 in · 1 out", "no change", or "" on the first day. */
  change: string;
  changed: boolean;
  title: string;
};

export type StageArrow = {
  path: string;
  label: Text;
  title: string;
};

export type MoveItem = { move: string; what: string };

export type StageStripView = {
  tiles: StageTile[];
  /** Drawn on the wide strip when the moves between stages are few enough. */
  arrows: StageArrow[];
  /** Every move the strip has no arrow for; on a phone, every move. */
  listed: MoveItem[];
  all: MoveItem[];
  /** "2 of 18 moved since Thu 8 Oct, and both skipped a stage." */
  summary: string | null;
  label: string;
};

const STRIP = { width: 600, gap: 8, tileY: 64, top: 62 } as const;
const tileWidth = (STRIP.width - 5 * STRIP.gap) / 6;
const tileX = (index: number) => index * (tileWidth + STRIP.gap);
const ARROW_LEVELS = [30, 14] as const;

const stageIndex = (stage: Stage) => STAGE_ORDER.indexOf(stage);

function skipped(from: Stage, to: Stage): Stage[] {
  const a = stageIndex(from);
  const b = stageIndex(to);
  return b > a + 1 ? STAGE_ORDER.slice(a + 1, b) : [];
}

function skipWords(from: Stage, to: Stage): string {
  if (stageIndex(to) < stageIndex(from)) return "moved back";
  const over = skipped(from, to);
  if (over.length === 0) return "";
  if (over.length > 2) return `skipped ${over.length} stages`;
  return `skipped ${joined(over.map((stage) => STAGE_LABELS[stage]))}`;
}

/** The two lines a stage's name takes on a tile ("In" / "development"). */
export function tileName(label: string): string[] {
  if (label.length <= 11 || !label.includes(" ")) return [label];
  const at = label.indexOf(" ");
  return [label.slice(0, at), label.slice(at + 1)];
}

/** D2: each stage's count and its change since the previous day, and what moved. */
export function stageStrip(progress: ProgressFacts): StageStripView {
  const between = progress.moves.filter(
    (move): move is StageMoveFacts & { from_stage: Stage; to_stage: Stage } =>
      Boolean(move.from_stage && move.to_stage),
  );
  const complete = progress.more_moves === 0;
  const since = progress.since ? formatDay(progress.since) : null;

  const tiles = progress.stages.map((item): StageTile => {
    const label = STAGE_LABELS[item.stage];
    const into = progress.moves.filter((move) => move.to_stage === item.stage).length;
    const out = progress.moves.filter((move) => move.from_stage === item.stage).length;
    let change = "";
    if (item.previous !== null && item.previous !== undefined) {
      const delta = item.count - item.previous;
      if (complete && into > 0 && out > 0) change = `${into} in · ${out} out`;
      else if (delta > 0) change = `+${delta}`;
      else if (delta < 0) change = `−${Math.abs(delta)}`;
      else change = "no change";
    }
    const before =
      item.previous !== null && item.previous !== undefined && since
        ? `, ${item.previous} on ${since}`
        : "";
    return {
      stage: item.stage,
      label,
      count: item.count,
      change,
      changed: change !== "" && change !== "no change",
      title: `${label}: ${item.count} today${before}`,
    };
  });

  const pairs: { from: Stage; to: Stage; moves: StageMoveFacts[] }[] = [];
  for (const move of between) {
    const pair = pairs.find((p) => p.from === move.from_stage && p.to === move.to_stage);
    if (pair) pair.moves.push(move);
    else pairs.push({ from: move.from_stage, to: move.to_stage, moves: [move] });
  }
  const drawn = tiles.length === 6 && pairs.length > 0 && pairs.length <= ARROW_LEVELS.length;
  const arrows = drawn ? pairs.map((pair, index) => arrow(pair, index)) : [];

  const all = progress.moves.map(moveItem);
  if (progress.more_moves) {
    all.push({ move: `and ${progress.more_moves} more`, what: "" });
  }
  // With arrows, the list keeps what has no arrow: new requirements, ones gone, "and N more".
  const arrowed = (index: number) => {
    const move = progress.moves[index];
    return Boolean(move?.from_stage && move.to_stage);
  };
  const listed = drawn ? all.filter((_item, index) => !arrowed(index)) : all;

  return {
    tiles,
    arrows,
    listed,
    all,
    summary: moveSummary(between.length + progress.more_moves, between, progress.total, since),
    label: stripLabel(tiles, pairs, since),
  };
}

function arrow(
  pair: { from: Stage; to: Stage; moves: StageMoveFacts[] },
  index: number,
): StageArrow {
  const a = stageIndex(pair.from);
  const b = stageIndex(pair.to);
  const forward = b > a;
  const sx = tileX(a) + (forward ? 0.586 : 0.35) * tileWidth;
  const ex = tileX(b) + (forward ? 0.35 : 0.586) * tileWidth;
  const apex = ARROW_LEVELS[index];
  const how = skipWords(pair.from, pair.to);
  const who =
    pair.moves.length === 1
      ? pair.moves[0].key
      : plural(pair.moves.length, "requirement", "requirements");
  const text = how ? `${who} · ${how}` : who;
  const half = textWidth(text, 11.5) / 2 + 4;
  const mid = Math.min(Math.max((sx + ex) / 2, half), STRIP.width - half);
  const keys = pair.moves.map((move) => move.key).join(", ");
  return {
    path: `M${sx.toFixed(1)} ${STRIP.top} C${sx.toFixed(1)} ${apex}, ${ex.toFixed(1)} ${apex}, ${ex.toFixed(1)} ${STRIP.top - 1}`,
    label: { x: mid, y: apex + 1, text, anchor: "middle" },
    title: `${keys} moved from ${STAGE_LABELS[pair.from]} to ${STAGE_LABELS[pair.to]}`,
  };
}

function moveItem(move: StageMoveFacts): MoveItem {
  if (move.from_stage && move.to_stage) {
    const how = skipWords(move.from_stage, move.to_stage);
    return {
      move: `${STAGE_LABELS[move.from_stage]} → ${STAGE_LABELS[move.to_stage]}`,
      what: `${move.key} ${move.title}${how ? ` · ${how}` : ""}`,
    };
  }
  if (move.to_stage)
    return { move: `New, in ${STAGE_LABELS[move.to_stage]}`, what: `${move.key} ${move.title}` };
  if (move.from_stage) {
    return {
      move: `Left the scope from ${STAGE_LABELS[move.from_stage]}`,
      what: `${move.key} ${move.title}`,
    };
  }
  return { move: move.key, what: move.title };
}

function moveSummary(
  moved: number,
  between: StageMoveFacts[],
  total: number,
  since: string | null,
): string | null {
  if (!moved || !since) return null;
  const lead = `${moved} of ${total} moved since ${since}`;
  const skips = between.filter(
    (move) => move.from_stage && move.to_stage && skipped(move.from_stage, move.to_stage).length,
  ).length;
  if (skips === 0) return `${lead}.`;
  if (skips === moved) {
    if (moved === 1) return `${lead}, and it skipped a stage.`;
    return moved === 2
      ? `${lead}, and both skipped a stage.`
      : `${lead}, and all ${moved} skipped a stage.`;
  }
  return `${lead}; ${skips} skipped a stage.`;
}

function stripLabel(
  tiles: StageTile[],
  pairs: { from: Stage; to: Stage; moves: StageMoveFacts[] }[],
  since: string | null,
): string {
  const counts = tiles
    .map((tile) => {
      const change = tile.change.startsWith("+")
        ? `, up ${tile.change.slice(1)}`
        : tile.change.startsWith("−")
          ? `, down ${tile.change.slice(1)}`
          : tile.change.includes(" in · ")
            ? `, ${tile.change.replace(" · ", " and ")}`
            : "";
      return `${tile.label} ${tile.count}${change}`;
    })
    .join("; ");
  const head = since
    ? `Six stages with today's counts and the change since ${since}: `
    : "Six stages with today's counts: ";
  const moves = pairs.map((pair) => {
    const how = skipWords(pair.from, pair.to);
    const who = pair.moves.map((move) => move.key).join(", ");
    const skip = how.startsWith("skipped") ? `, ${how.replace("skipped", "skipping")}` : "";
    return `${who} moved from ${STAGE_LABELS[pair.from]} to ${STAGE_LABELS[pair.to]}${skip}.`;
  });
  return `${head}${counts}.${moves.length ? ` ${moves.join(" ")}` : ""}`;
}

// ---- D4: the gate rings -------------------------------------------------------------

export type RingSegment = { tone: Tone | "bypassed"; dash: string; offset: string; title: string };
export type GateRow = { tone: Tone | "bypassed"; count: number; words: string };

export type GateRingView = {
  name: string;
  before: string;
  passed: number;
  total: number;
  segments: RingSegment[];
  rows: GateRow[];
  label: string;
};

const RING_RADIUS = 39;
export const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/** D4: one gate's requirements, each counted once: passed, moved on without it, the rest. */
export function gateRing(gate: GateFacts): GateRingView {
  const rows: GateRow[] = [
    { tone: "green", count: gate.passed, words: "passed" },
    ...(gate.failed ? [{ tone: "red" as const, count: gate.failed, words: "failed" }] : []),
    { tone: "bypassed", count: gate.bypassed, words: "moved on without it" },
    ...(gate.open
      ? [{ tone: "amber" as const, count: gate.open, words: "open, waiting for sign-off" }]
      : []),
    { tone: "neutral", count: gate.missing, words: "still to confirm" },
  ];
  const total = Math.max(gate.total, 1);
  const per = RING_CIRCUMFERENCE / total;
  const shown = rows.filter((row) => row.count > 0);
  const gap = shown.length > 1 ? 1 : 0;
  let at = 0;
  const segments = shown.map((row) => {
    const segment = {
      tone: row.tone,
      dash: `${Math.max(row.count * per - gap, 0.5).toFixed(1)} ${RING_CIRCUMFERENCE.toFixed(1)}`,
      offset: (-(at * per + gap / 2)).toFixed(1),
      title: `${capitalised(row.words)}: ${row.count} of ${gate.total}`,
    };
    at += row.count;
    return segment;
  });
  return {
    name: gate.name,
    before: `before ${STAGE_LABELS[gate.guards_stage].toLowerCase()}`,
    passed: gate.passed,
    total: gate.total,
    segments,
    rows,
    label: `${gate.name}: ${gate.passed} of ${gate.total} passed, ${rows
      .slice(1)
      .map((row) => `${row.count} ${row.words}`)
      .join(", ")}`,
  };
}

const capitalised = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

// ---- Most important -----------------------------------------------------------------

export type BypassGroup = { words: string; keys: string[] };

export type ImportantView = {
  groups: BypassGroup[];
  lines: string[];
  /** Where the lines the message has here are drawn instead. */
  note: string | null;
  empty: boolean;
};

const naturalKey = (key: string) => key.replace(/\d+/g, (digits) => digits.padStart(8, "0"));

/** Most important: every requirement that went around a gate, by stage and gates. */
export function importantView(important: ImportantFacts): ImportantView {
  const groups = new Map<string, { stage: Stage; gates: string[]; keys: string[] }>();
  for (const item of important.bypassed) {
    const id = `${item.stage}|${item.gates.join("|")}`;
    const group = groups.get(id) ?? { stage: item.stage, gates: item.gates, keys: [] };
    group.keys.push(item.key);
    groups.set(id, group);
  }
  const ordered = [...groups.values()].sort(
    (a, b) => stageIndex(b.stage) - stageIndex(a.stage) || b.keys.length - a.keys.length,
  );
  const notes: string[] = [];
  const drawn = drawnWords(important.drawn);
  if (drawn) notes.push(drawn);
  if (important.risks) {
    notes.push(
      important.risks === 1
        ? "The 1 risk the message lists here is a fix under What we need."
        : `The ${important.risks} risks the message lists here are fixes under What we need.`,
    );
  }
  return {
    groups: ordered.map((group) => ({
      words: `Reached ${STAGE_LABELS[group.stage].toLowerCase()} without ${joined(group.gates)}`,
      keys: [...group.keys].sort((a, b) => naturalKey(a).localeCompare(naturalKey(b))),
    })),
    lines: important.lines,
    note: notes.length ? notes.join(" ") : null,
    empty: !ordered.length && !important.lines.length && !notes.length,
  };
}

function drawnWords(kinds: string[]): string | null {
  const parts: string[] = [];
  const add = (part: string) => {
    if (!parts.includes(part)) parts.push(part);
  };
  for (const kind of kinds) {
    if (kind === "committed" || kind === "jira" || kind === "no_date") add("the committed date");
    else if (kind === "forecast" || kind === "history") add("the history");
    else if (kind === "team") add("the team's date");
  }
  if (!parts.length) return null;
  const verb = parts.length === 1 ? "is" : "are";
  return `${capitalised(joined(parts))} ${verb} drawn once, in the bar under In short.`;
}

// ---- D3: who acts -------------------------------------------------------------------

export type AskRow = {
  need: NeedType;
  kind: string;
  text: string;
  days: number | null;
  /** Bar length, 0 to 100, against the scale. */
  width: number;
  daysText: string;
  waited: string;
  neededMost: boolean;
  escalation: string | null;
};

export type LaneView = { heading: string; named: boolean; asks: AskRow[] };

export type WhoActsView = {
  lanes: LaneView[];
  /** "6 escalated: 5 to Ira Novak (Scrum master), 1 to Asha Rao (Manager)". */
  escalated: string | null;
  scale: { ticks: { at: number; label: string }[] };
  kinds: NeedType[];
  anyEscalated: boolean;
};

const STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500];

/** A scale for days waited: 0 to a round number, with at most four steps. */
export function dayScale(longest: number): { max: number; ticks: number[] } {
  const top = Math.max(longest, 1);
  const step = STEPS.find((candidate) => top / candidate <= 4) ?? Math.ceil(top / 4);
  const max = Math.ceil(top / step) * step;
  const ticks: number[] = [];
  for (let at = 0; at <= max; at += step) ticks.push(at);
  return { max, ticks };
}

// The legend's order, as the mockup reads it.
const NEED_ORDER: NeedType[] = ["fix", "review", "answer", "decision"];

/** The scale's last label: "6 days", or "30 d" where two digits leave no room for the word. */
const lastTick = (at: number) => (at >= 10 ? `${at} d` : `${at} ${at === 1 ? "day" : "days"}`);

/** D3: the asks by person, in the report's order, each with its kind, wait and escalation. */
export function whoActs(owners: OwnerAsks[]): WhoActsView {
  const asks = owners.flatMap((owner) => owner.asks);
  const scale = dayScale(Math.max(0, ...asks.map((ask) => ask.waited_days ?? 0)));
  const lanes = owners.map((owner): LaneView => ({
    heading: owner.heading,
    named: owner.named,
    asks: owner.asks.map((ask) => askRow(ask, scale.max)),
  }));
  const targets: { who: string; count: number }[] = [];
  for (const ask of asks) {
    if (!ask.escalated_to) continue;
    const who = ask.escalation_label
      ? `${ask.escalated_to} (${ask.escalation_label})`
      : ask.escalated_to;
    const target = targets.find((item) => item.who === who);
    if (target) target.count += 1;
    else targets.push({ who, count: 1 });
  }
  const total = targets.reduce((sum, item) => sum + item.count, 0);
  const ordered = [...targets].sort((a, b) => b.count - a.count);
  return {
    lanes,
    escalated: total
      ? ordered.length === 1
        ? `${total} escalated to ${ordered[0].who}`
        : `${total} escalated: ${ordered.map((item) => `${item.count} to ${item.who}`).join(", ")}`
      : null,
    scale: {
      ticks: scale.ticks.map((at, index) => ({
        at: (at / scale.max) * 100,
        label: index === scale.ticks.length - 1 ? lastTick(at) : String(at),
      })),
    },
    kinds: NEED_ORDER.filter((need) => asks.some((ask) => ask.need === need)),
    anyEscalated: total > 0,
  };
}

function askRow(ask: AskFacts, max: number): AskRow {
  const detail = ask.detail ? ` · ${ask.detail}` : "";
  const text = ask.open_question
    ? `${ask.issue_key ?? "A question"}, the open question below${detail}`
    : `${ask.text}${detail}`;
  const days = ask.waited_days ?? null;
  return {
    need: ask.need,
    kind: NEED_WORDS[ask.need],
    text,
    days,
    width: days === null ? 0 : Math.min(100, (days / max) * 100),
    daysText: days === null ? "–" : `${days} d`,
    waited:
      days === null
        ? "How long it has waited is not known"
        : days === 0
          ? "Raised today"
          : `${plural(days, "day", "days")} waiting`,
    neededMost: ask.needed_most,
    escalation: ask.escalated_to
      ? `Escalated to ${ask.escalated_to}${ask.escalation_label ? ` (${ask.escalation_label})` : ""}`
      : null,
  };
}

// ---- Open questions -----------------------------------------------------------------

export type QuestionRow = {
  ticket: string;
  question: string;
  asked: string;
  heard: string;
  tone: "info" | "neutral";
};

/** The Open questions table as rows, by its own column names. */
export function questionRows(table: Table | null | undefined): QuestionRow[] {
  if (!table) return [];
  const at = (name: string) => table.columns.indexOf(name);
  const cell = (row: string[], name: string) => (at(name) >= 0 ? (row[at(name)] ?? "") : "");
  return table.rows.map((row) => {
    const to = cell(row, "Asked to");
    const on = cell(row, "Asked on");
    const heard = cell(row, "Heard back?");
    return {
      ticket: cell(row, "Ticket"),
      question: cell(row, "What we asked"),
      asked: [to && `Asked to ${to}`, on && `on ${on}`].filter(Boolean).join(" "),
      heard,
      tone: heard === "Partly" ? "info" : "neutral",
    };
  });
}
