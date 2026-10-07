// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DeliveryStage,
  DeliveryStagesResponse,
  DeliveryStagesUpdateRequest,
  ObservedStatusResponse,
} from "../../api/schema";

/** The six steps in order, as the backend counts them. */
export const STEPS: DeliveryStage[] = [
  "raised",
  "groomed",
  "in_development",
  "in_testing",
  "business_testing",
  "production",
];

/** What each step means, in the words someone outside delivery would use. */
export const STEP_MEANING: Record<DeliveryStage, string> = {
  raised: "Asked for, but nobody has looked at it closely yet.",
  groomed: "Understood, sized and ready to be built.",
  in_development: "Being built, or its code is being reviewed.",
  in_testing: "Built; the team is testing that it works.",
  business_testing: "The business is checking it does what they asked for.",
  production: "Released to the people who use it, or otherwise done.",
};

export const NOT_COUNTED_MEANING =
  "Left out of every count, such as work that was cancelled or is a duplicate.";

export type StageDraft = {
  stages: Record<DeliveryStage, string[]>;
  excluded: string[];
  requirementTypes: string[];
};

/** A stage, "excluded" (not counted), or null when nothing names the status. */
export type Placement = DeliveryStage | "excluded" | null;

export function draftFromResponse(response: DeliveryStagesResponse): StageDraft {
  const stages = Object.fromEntries(STEPS.map((step) => [step, [] as string[]])) as Record<
    DeliveryStage,
    string[]
  >;
  response.stages.forEach((item) => {
    stages[item.stage] = [...item.statuses];
  });
  return {
    stages,
    excluded: [...response.excluded_statuses],
    requirementTypes: [...response.requirement_types],
  };
}

const key = (name: string) => name.trim().replace(/\s+/g, " ").toLowerCase();

/** Where the draft puts a status, whatever its case or spacing. */
export function placementOf(draft: StageDraft, status: string): Placement {
  const wanted = key(status);
  if (draft.excluded.some((name) => key(name) === wanted)) return "excluded";
  return STEPS.find((step) => draft.stages[step].some((name) => key(name) === wanted)) ?? null;
}

/** Put a status in one place only: a step, not counted, or nowhere (its broad state decides). */
export function moveStatus(draft: StageDraft, status: string, target: Placement): StageDraft {
  const wanted = key(status);
  const without = (names: string[]) => names.filter((name) => key(name) !== wanted);
  const stages = Object.fromEntries(
    STEPS.map((step) => [step, without(draft.stages[step])]),
  ) as Record<DeliveryStage, string[]>;
  const excluded = without(draft.excluded);
  const name = status.trim().replace(/\s+/g, " ");
  if (target === "excluded") excluded.push(name);
  else if (target !== null) stages[target] = [...stages[target], name];
  return { ...draft, stages, excluded };
}

/**
 * The names the draft recognises that the tracker does not use: what other
 * trackers call these steps, kept so a status that appears later is placed on
 * its own. A status the tracker does use is chosen in its own row instead.
 */
export function otherNames(
  draft: StageDraft,
  trackerStatuses: string[],
): { stages: Record<DeliveryStage, string[]>; excluded: string[]; total: number } {
  const carried = new Set(trackerStatuses.map(key));
  const others = (names: string[]) => names.filter((name) => !carried.has(key(name)));
  const stages = Object.fromEntries(
    STEPS.map((step) => [step, others(draft.stages[step])]),
  ) as Record<DeliveryStage, string[]>;
  const excluded = others(draft.excluded);
  const total = STEPS.reduce((sum, step) => sum + stages[step].length, 0) + excluded.length;
  return { stages, excluded, total };
}

/** Statuses nothing places come first; each group keeps its order (most issues first). */
export function unplacedFirst<T extends { status: string }>(items: T[], draft: StageDraft): T[] {
  const unplaced = items.filter((item) => placementOf(draft, item.status) === null);
  const placed = items.filter((item) => placementOf(draft, item.status) !== null);
  return [...unplaced, ...placed];
}

/** Comma- or newline-separated names, trimmed and without repeats. */
export function parseNames(text: string): string[] {
  const seen = new Set<string>();
  return text
    .split(/[,\n]/)
    .map((name) => name.trim().replace(/\s+/g, " "))
    .filter((name) => {
      if (!name || seen.has(key(name))) return false;
      seen.add(key(name));
      return true;
    });
}

export function requestFromDraft(draft: StageDraft): DeliveryStagesUpdateRequest {
  return {
    stages: draft.stages,
    excluded_statuses: draft.excluded,
    requirement_types: draft.requirementTypes,
  };
}

export function sameDraft(left: StageDraft, right: StageDraft): boolean {
  return JSON.stringify(requestFromDraft(left)) === JSON.stringify(requestFromDraft(right));
}

/** Issues per step under a placement, and how many are left out. */
export function issuesPerStep(rows: ObservedStatusResponse[]): {
  steps: Record<DeliveryStage, number>;
  notCounted: number;
} {
  const steps = Object.fromEntries(STEPS.map((step) => [step, 0])) as Record<DeliveryStage, number>;
  let notCounted = 0;
  rows.forEach((row) => {
    if (row.stage === null || row.stage === undefined) notCounted += row.issues;
    else steps[row.stage] += row.issues;
  });
  return { steps, notCounted };
}

/** "1 issue", "8 issues". */
export function issueCount(count: number): string {
  return `${count} ${count === 1 ? "issue" : "issues"}`;
}
