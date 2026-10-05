// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DeliveryStage,
  RequirementStageCountResponse,
  RequirementTimelinePointResponse,
} from "../../api/schema";

export const STAGES: DeliveryStage[] = [
  "raised",
  "groomed",
  "in_development",
  "in_testing",
  "business_testing",
  "production",
];

export const STAGE_LABELS: Record<DeliveryStage, string> = {
  raised: "Raised",
  groomed: "Groomed",
  in_development: "In development",
  in_testing: "In testing",
  business_testing: "Business testing",
  production: "Production",
};

/** Each stage's colour token (index.css): light to dark as work moves on. */
export const STAGE_COLORS: Record<DeliveryStage, string> = {
  raised: "var(--op-stage-raised)",
  groomed: "var(--op-stage-groomed)",
  in_development: "var(--op-stage-in-development)",
  in_testing: "var(--op-stage-in-testing)",
  business_testing: "var(--op-stage-business-testing)",
  production: "var(--op-stage-production)",
};

/** "+2", "−1" (a real minus), or "" for no change or nothing to compare with. */
export function changeLabel(change: number | null | undefined): string {
  if (change === null || change === undefined || change === 0) return "";
  return change > 0 ? `+${change}` : `−${Math.abs(change)}`;
}

export type StackSegment = { stage: DeliveryStage; count: number; y: number; height: number };
export type StackColumn = {
  day: string;
  x: number;
  width: number;
  total: number;
  segments: StackSegment[];
};

/**
 * Stacked columns for the stage timeline, one per day, production at the base.
 *
 * Heights share one scale, up to ``scaleMax`` (the top axis tick) or else the
 * tallest day. Each segment above the first
 * gives up ``gap`` pixels to the one below it, the surface gap that separates
 * touching fills; a segment too short to survive its gap keeps one pixel, so a
 * count is never drawn as nothing.
 */
export function stackColumns(
  points: RequirementTimelinePointResponse[],
  options: { width: number; height: number; gap?: number; maxBar?: number; scaleMax?: number },
): { columns: StackColumn[]; max: number } {
  const gap = options.gap ?? 2;
  const max = Math.max(1, options.scaleMax ?? 0, maxTotal(points));
  const slot = points.length > 0 ? options.width / points.length : options.width;
  const width = Math.max(2, Math.min(options.maxBar ?? 24, slot * 0.7));
  const order = [...STAGES].reverse();
  const columns = points.map((point, index) => {
    const x = index * slot + (slot - width) / 2;
    let base = options.height;
    const segments: StackSegment[] = [];
    order.forEach((stage) => {
      const count = point.counts[stage] ?? 0;
      if (count <= 0) return;
      const full = (count / max) * options.height;
      const height = segments.length === 0 ? full : Math.max(1, full - gap);
      const y = base - full;
      segments.push({ stage, count, y, height });
      base = y;
    });
    return { day: point.day, x, width, total: total(point.counts), segments };
  });
  return { columns, max };
}

/** Clean tick values for a count axis: 0, a round step, ..., at or above ``max``. */
export function countTicks(max: number, target = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / target;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= raw) ?? raw;
  const ticks: number[] = [];
  for (let value = 0; value < max + step; value += step) {
    ticks.push(Math.round(value));
    if (value >= max) break;
  }
  return ticks;
}

/** "64%" with no decimals, or "—" when nothing is counted. */
export function percentLabel(percent: number | null | undefined): string {
  if (percent === null || percent === undefined) return "—";
  return `${Math.round(percent)}%`;
}

export function stageCount(
  stages: RequirementStageCountResponse[],
  stage: DeliveryStage,
): RequirementStageCountResponse | undefined {
  return stages.find((item) => item.stage === stage);
}

/** The most requirements counted on any one day. */
export function maxTotal(points: RequirementTimelinePointResponse[]): number {
  return Math.max(0, ...points.map((point) => total(point.counts)));
}

function total(counts: Partial<Record<DeliveryStage, number>>): number {
  return STAGES.reduce((sum, stage) => sum + (counts[stage] ?? 0), 0);
}
