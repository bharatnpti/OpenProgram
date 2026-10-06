// Pure chart geometry for the Overall view, type imports only so `node --test`
// runs it directly. Components draw what these return; no scale lives in JSX.
import type { DeliveryStage, RequirementTimelinePointResponse } from "../../api/schema";

import { daysBetween } from "../../lib/format.ts";

export type BurndownPoint = { day: string; remaining: number };

/** Requirements not yet in production, per day of the timeline: the burn-down by count. */
export function burndownByCount(timeline: RequirementTimelinePointResponse[]): BurndownPoint[] {
  return timeline.map((point) => {
    let remaining = 0;
    for (const [stage, count] of Object.entries(point.counts)) {
      if (stage !== "production") remaining += count ?? 0;
    }
    return { day: point.day, remaining };
  });
}

/**
 * Story points not yet in production, per day, or null unless every day of the
 * timeline kept points for every requirement it counted. That is the backend's
 * own rule for forecasting by points, so the line and the forecast dates on it
 * measure the same thing.
 */
export function burndownByPoints(
  timeline: RequirementTimelinePointResponse[],
): BurndownPoint[] | null {
  if (timeline.length === 0 || !timeline.every((point) => point.has_points)) return null;
  return timeline.map((point) => {
    let remaining = 0;
    for (const [stage, points] of Object.entries(point.points ?? {})) {
      if (stage !== "production") remaining += points ?? 0;
    }
    return { day: point.day, remaining: Math.round(remaining * 10) / 10 };
  });
}

export type BurndownSeries = {
  points: BurndownPoint[];
  unit: "story points" | "requirements";
  /** The chart's caption: what it measures, and why by count when it is. */
  caption: string;
};

/** The burn-down to draw: by story points when the timeline has them, else by count. */
export function burndownSeries(timeline: RequirementTimelinePointResponse[]): BurndownSeries {
  const byPoints = burndownByPoints(timeline);
  if (byPoints) {
    return {
      points: byPoints,
      unit: "story points",
      caption: "Story points not yet in production",
    };
  }
  return {
    points: burndownByCount(timeline),
    unit: "requirements",
    caption:
      timeline.length > 0
        ? "Requirements not yet in production, by count: not every requirement has story points"
        : "Requirements not yet in production, by count",
  };
}

export type Marker = { key: string; day: string; label: string };

export type BurndownGeometry = {
  width: number;
  height: number;
  /** SVG path of the remaining line, empty when there is no history. */
  path: string;
  /** Closed path under the line for the area fill. */
  area: string;
  last: { x: number; y: number; remaining: number } | null;
  yTicks: { y: number; value: number }[];
  xLabels: { x: number; label: string; anchor: "start" | "middle" | "end" }[];
  markers: (Marker & { x: number })[];
};

/**
 * One scale for line, ticks and markers. The x axis runs from the first day of
 * history to the latest of the last day and every marker (committed date,
 * forecast dates), so a date past today still lands on the chart.
 */
export function burndownGeometry(
  points: BurndownPoint[],
  markers: Marker[],
  formatLabel: (iso: string) => string,
  size = { width: 560, height: 220, left: 36, right: 16, top: 20, bottom: 30 },
): BurndownGeometry {
  const { width, height, left, right, top, bottom } = size;
  const empty: BurndownGeometry = {
    width,
    height,
    path: "",
    area: "",
    last: null,
    yTicks: [],
    xLabels: [],
    markers: [],
  };
  if (points.length === 0) return empty;

  const start = points[0].day;
  const lastDay = points[points.length - 1].day;
  const end = [lastDay, ...markers.map((m) => m.day)].reduce((a, b) => (b > a ? b : a));
  const span = Math.max(1, daysBetween(start, end));
  const max = Math.max(1, ...points.map((p) => p.remaining));
  const yMax = niceCeiling(max);

  const x = (day: string) => left + (daysBetween(start, day) / span) * (width - left - right);
  const y = (value: number) => top + (1 - value / yMax) * (height - top - bottom);

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.day).toFixed(1)} ${y(p.remaining).toFixed(1)}`)
    .join(" ");
  const baseline = y(0).toFixed(1);
  const area = `${path} L${x(lastDay).toFixed(1)} ${baseline} L${x(start).toFixed(1)} ${baseline} Z`;
  const tail = points[points.length - 1];

  return {
    width,
    height,
    path,
    area,
    last: { x: x(tail.day), y: y(tail.remaining), remaining: tail.remaining },
    yTicks: [0, yMax / 2, yMax].map((value) => ({ y: y(value), value })),
    xLabels: [
      { x: x(start), label: formatLabel(start), anchor: "start" as const },
      ...(lastDay !== start && lastDay !== end
        ? [{ x: x(lastDay), label: formatLabel(lastDay), anchor: "middle" as const }]
        : []),
      ...(end !== start ? [{ x: x(end), label: formatLabel(end), anchor: "end" as const }] : []),
    ],
    markers: markers.filter((m) => m.day >= start).map((m) => ({ ...m, x: x(m.day) })),
  };
}

/** Round up to a value that halves cleanly: 1, 2, 4, 6, 10, 20, 40, 60, 100… */
export function niceCeiling(value: number): number {
  if (value <= 2) return 2;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 4, 6, 10]) {
    if (step * magnitude >= value) return step * magnitude;
  }
  return 10 * magnitude;
}

export type StackBar = {
  day: string;
  x: number;
  width: number;
  segments: { stage: DeliveryStage; y: number; height: number; count: number }[];
};

/** One stacked bar per day, stages bottom-up in delivery order. */
export function stageStack(
  timeline: RequirementTimelinePointResponse[],
  order: DeliveryStage[],
  size = { width: 560, height: 180, left: 30, right: 8, top: 10, bottom: 24 },
): { bars: StackBar[]; yMax: number; yTicks: { y: number; value: number }[] } {
  const { width, height, left, right, top, bottom } = size;
  const totals = timeline.map((p) => order.reduce((sum, s) => sum + (p.counts[s] ?? 0), 0));
  const yMax = niceCeiling(Math.max(1, ...totals));
  const y = (value: number) => top + (1 - value / yMax) * (height - top - bottom);
  const slot = timeline.length > 0 ? (width - left - right) / timeline.length : 0;
  const gap = Math.min(2, slot * 0.2);

  const bars = timeline.map((point, i) => {
    let acc = 0;
    const segments = order
      .map((stage) => {
        const count = point.counts[stage] ?? 0;
        const y0 = y(acc + count);
        const y1 = y(acc);
        acc += count;
        return { stage, y: y0, height: y1 - y0, count };
      })
      .filter((s) => s.count > 0);
    return {
      day: point.day,
      x: left + i * slot + gap / 2,
      width: Math.max(1, slot - gap),
      segments,
    };
  });
  return { bars, yMax, yTicks: [0, yMax / 2, yMax].map((value) => ({ y: y(value), value })) };
}
