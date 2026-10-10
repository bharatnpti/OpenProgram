// O3, "Requirements by stage": a cumulative flow of the requirements in each
// stage, day by day, with today's counts beside it; under the working days of
// history the forecast needs (the tenant's minimum), one bar of today's split. Pure geometry and wording, runtime imports
// by their .ts path, so `node --test` runs it as written.
import type { DeliveryStage, RequirementTimelinePointResponse } from "../../api/schema";

import { STAGE_LABELS, STAGE_ORDER } from "../../components/viz/stages.ts";
import { daysBetween, formatDay } from "../../lib/format.ts";
import { countTicks, niceCeiling, thinAxisLabels, type AxisLabel } from "./charts.ts";
import { monthMarks } from "./slip.ts";

type Point = Pick<RequirementTimelinePointResponse, "day" | "counts">;
export type StageLabel = (stage: DeliveryStage) => string;

const defaultLabel: StageLabel = (stage) => STAGE_LABELS[stage];
const count = (point: Point, stage: DeliveryStage) => point.counts[stage] ?? 0;
const isWorkingDay = (iso: string) => {
  const day = new Date(`${iso}T12:00:00Z`).getUTCDay();
  return day !== 0 && day !== 6;
};

/**
 * Working days of history the timeline holds, counted as the forecast counts
 * them (core/domain/forecast.py `daily_completions`): a working day whose
 * working day before has a snapshot too. So "3 of 10 working days" here is the
 * same 3 as the forecast's, and against the same minimum (the requirements
 * read's `forecast_needed_days`), so the flow draws when the forecast could.
 */
export function historyDays(timeline: Point[]): number {
  const days = new Set(timeline.map((point) => point.day));
  return timeline.filter(
    (point) => isWorkingDay(point.day) && days.has(previousWorkingDay(point.day)),
  ).length;
}

function previousWorkingDay(iso: string): string {
  const moment = new Date(`${iso}T12:00:00Z`);
  do moment.setUTCDate(moment.getUTCDate() - 1);
  while (moment.getUTCDay() === 0 || moment.getUTCDay() === 6);
  return moment.toISOString().slice(0, 10);
}

/** Whether the flow can draw: the timeline holds the working days the forecast needs. */
export function flowDrawable(timeline: Point[], neededDays: number): boolean {
  return historyDays(timeline) >= neededDays;
}

/** "Not enough history for the flow yet: 3 of 10 working days." */
export function shortHistoryNote(timeline: Point[], neededDays: number): string {
  return `Not enough history for the flow yet: ${historyDays(timeline)} of ${neededDays} working days. Until then one bar shows today's split.`;
}

/**
 * What the split today says, in a line: where most of the open work sits.
 * "6 raised and 6 in development: 12 of the 13 open requirements."
 */
export function splitFinding(
  counts: Partial<Record<DeliveryStage, number>>,
  label: StageLabel = defaultLabel,
): string {
  const total = STAGE_ORDER.reduce((sum, stage) => sum + (counts[stage] ?? 0), 0);
  const open = total - (counts.production ?? 0);
  if (total === 0) return "No requirements are counted yet.";
  if (open === 0)
    return `All ${total} ${total === 1 ? "requirement is" : "requirements are"} in production.`;
  const stages = STAGE_ORDER.filter((stage) => stage !== "production" && (counts[stage] ?? 0) > 0);
  const largest = [...stages].sort(
    (a, b) =>
      (counts[b] ?? 0) - (counts[a] ?? 0) || STAGE_ORDER.indexOf(a) - STAGE_ORDER.indexOf(b),
  );
  const lower = (stage: DeliveryStage) => label(stage).toLowerCase();
  if (stages.length === 1) {
    return `All ${open} open ${open === 1 ? "requirement is" : "requirements are"} ${lower(stages[0])}.`;
  }
  const first = counts[largest[0]] ?? 0;
  if (first * 2 >= open) {
    return `${first} of the ${open} open requirements ${first === 1 ? "is" : "are"} ${lower(largest[0])}.`;
  }
  const pair = largest.slice(0, 2).sort((a, b) => STAGE_ORDER.indexOf(a) - STAGE_ORDER.indexOf(b));
  const [a, b] = pair.map((stage) => counts[stage] ?? 0);
  return `${a} ${lower(pair[0])} and ${b} ${lower(pair[1])}: ${a + b} of the ${open} open requirements.`;
}

export type FlowChange = { stage: DeliveryStage; from: number; to: number };

/**
 * Where work piled up over the timeline: the stage before production that
 * widened most, and how production moved. "In development widened from 3 to 6
 * in 30 days, while production grew from 0 to 5."
 */
export function flowFinding(
  timeline: Point[],
  label: StageLabel = defaultLabel,
): { text: string; widest: FlowChange | null; production: FlowChange } {
  const first = timeline[0];
  const last = timeline[timeline.length - 1];
  const days = daysBetween(first.day, last.day) + 1;
  const change = (stage: DeliveryStage): FlowChange => ({
    stage,
    from: count(first, stage),
    to: count(last, stage),
  });
  const widest =
    STAGE_ORDER.filter((stage) => stage !== "production")
      .map(change)
      .filter((item) => item.to > item.from)
      // Ties go to the stage holding most today, where the pile is, then the later stage.
      .sort(
        (a, b) =>
          b.to - b.from - (a.to - a.from) ||
          b.to - a.to ||
          STAGE_ORDER.indexOf(b.stage) - STAGE_ORDER.indexOf(a.stage),
      )[0] ?? null;
  const production = change("production");
  const span = `in ${days} ${days === 1 ? "day" : "days"}`;
  const prod =
    production.to > production.from
      ? `production grew from ${production.from} to ${production.to}`
      : production.to < production.from
        ? `production went from ${production.from} to ${production.to}`
        : null;
  let text: string;
  if (widest) {
    const lead = `${label(widest.stage)} widened from ${widest.from} to ${widest.to} ${span}`;
    text = prod ? `${lead}, while ${prod}.` : `${lead}, and nothing more reached production.`;
  } else if (prod) {
    text = `${prod[0].toUpperCase()}${prod.slice(1)} ${span}, and no stage before it widened.`;
  } else {
    text = `No stage widened ${span}, and nothing more reached production.`;
  }
  return { text, widest, production };
}

export type FlowSize = {
  width: number;
  height: number;
  left: number;
  plotRight: number;
  top: number;
  bottom: number;
};

export const FLOW_SIZE: FlowSize = {
  width: 560,
  height: 236,
  left: 36,
  plotRight: 404,
  top: 28,
  bottom: 206,
};

export type FlowGeometry = {
  size: FlowSize;
  /** Bottom-up: production first, raised on top. */
  bands: { stage: DeliveryStage; points: string; title: string }[];
  /** The edges between bands, drawn in the surface colour so neighbours part. */
  edges: string[];
  /** Today's counts at the right edge, de-overlapped; `was` for the stages the finding names. */
  labels: { stage: DeliveryStage; y: number; text: string; was: string | null }[];
  yTicks: { y: number; value: number }[];
  xLabels: AxisLabel[];
  /** Where the callout points: the widest band's middle three quarters along. */
  callout: { x: number; y: number; text: string } | null;
};

/** One scale for the bands, the labels and the ticks. Days by date, so a gap stays a gap. */
export function flowGeometry(
  timeline: Point[],
  label: StageLabel = defaultLabel,
  size: FlowSize = FLOW_SIZE,
): FlowGeometry {
  const { left, plotRight, top, bottom } = size;
  const first = timeline[0].day;
  const last = timeline[timeline.length - 1].day;
  const span = Math.max(1, daysBetween(first, last));
  const x = (day: string) => left + (daysBetween(first, day) / span) * (plotRight - left);
  const totals = timeline.map((point) => STAGE_ORDER.reduce((sum, s) => sum + count(point, s), 0));
  const yMax = niceCeiling(Math.max(1, ...totals));
  const y = (value: number) => bottom - (value / yMax) * (bottom - top);
  const stack = [...STAGE_ORDER].reverse();
  const below = (point: Point, index: number) =>
    stack.slice(0, index).reduce((sum, stage) => sum + count(point, stage), 0);

  const bands = stack.map((stage, index) => {
    const upper = timeline.map(
      (point) => `${x(point.day).toFixed(1)},${y(below(point, index + 1)).toFixed(1)}`,
    );
    const lower = [...timeline]
      .reverse()
      .map((point) => `${x(point.day).toFixed(1)},${y(below(point, index)).toFixed(1)}`);
    return {
      stage,
      points: [...upper, ...lower].join(" "),
      title: `${label(stage)}: ${count(timeline[0], stage)} on ${formatDay(first)}, ${count(timeline[timeline.length - 1], stage)} today`,
    };
  });
  const edges = stack
    .slice(0, -1)
    .map((_, index) =>
      timeline
        .map((point) => `${x(point.day).toFixed(1)},${y(below(point, index + 1)).toFixed(1)}`)
        .join(" "),
    );

  const { widest, production } = flowFinding(timeline, label);
  const noted = new Set<DeliveryStage>(
    [widest?.stage, production.to !== production.from ? "production" : undefined].filter(
      (stage): stage is DeliveryStage => Boolean(stage),
    ),
  );
  const end = timeline[timeline.length - 1];
  const wanted = stack
    .map((stage, index) => ({ stage, index, value: count(end, stage) }))
    .filter((item) => item.value > 0)
    .map((item) => ({
      stage: item.stage,
      // The middle of the band at the right edge.
      y: y(below(end, item.index) + item.value / 2) + 4,
      text: `${label(item.stage)} ${item.value}`,
      was: noted.has(item.stage)
        ? `was ${count(timeline[0], item.stage)} on ${formatDay(first)}`
        : null,
    }));
  // Top to bottom, each label (and its "was" line) clear of the one above.
  const labels: FlowGeometry["labels"] = [];
  for (const item of [...wanted].sort((a, b) => a.y - b.y)) {
    const prev = labels[labels.length - 1];
    const room = prev ? (prev.was ? 28 : 15) : 0;
    labels.push({ ...item, y: prev && item.y - prev.y < room ? prev.y + room : item.y });
  }

  let callout: FlowGeometry["callout"] = null;
  if (widest) {
    const at = timeline[Math.min(timeline.length - 1, Math.round((timeline.length - 1) * 0.75))];
    const index = stack.indexOf(widest.stage);
    callout = {
      x: x(at.day),
      y: y(below(at, index) + count(at, widest.stage) / 2),
      text: `${label(widest.stage)} widened: ${widest.from} → ${widest.to}`,
    };
  }

  return {
    size,
    bands,
    edges,
    labels,
    yTicks: countTicks(yMax).map((value) => ({ y: y(value), value })),
    xLabels: thinAxisLabels(
      [
        { x: x(first), label: shortDate(first), anchor: "start" as const },
        ...monthMarks(first, last)
          .filter((day) => day !== first && daysBetween(day, last) > 3)
          .map((day) => ({ x: x(day), label: shortDate(day), anchor: "middle" as const })),
      ],
      plotRight - 40,
    ),
    callout,
  };
}

/** The chart's label: what it finds, then today's counts. */
export function flowSpoken(timeline: Point[], label: StageLabel = defaultLabel): string {
  const first = timeline[0];
  const last = timeline[timeline.length - 1];
  const days = daysBetween(first.day, last.day) + 1;
  const total = (point: Point) => STAGE_ORDER.reduce((sum, stage) => sum + count(point, stage), 0);
  const [a, b] = [total(first), total(last)];
  const scope = a === b ? "" : ` Scope ${b > a ? "grew" : "shrank"} from ${a} to ${b}.`;
  const today = STAGE_ORDER.filter((stage) => count(last, stage) > 0)
    .map((stage) => `${label(stage)} ${count(last, stage)}`)
    .join(", ");
  return `Cumulative flow over ${days} days, ${formatDay(first.day)} to today. ${flowFinding(timeline, label).text}${scope} Today: ${today}.`;
}

const shortDate = (iso: string) => formatDay(iso).replace(/^\w+ /, "");
