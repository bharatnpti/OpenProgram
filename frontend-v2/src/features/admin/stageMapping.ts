// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DeliveryStage,
  DeliveryStagesResponse,
  DeliveryStagesUpdateRequest,
} from "../../api/schema";

export type StageDraft = {
  stages: Record<DeliveryStage, string[]>;
  excluded: string[];
  requirementTypes: string[];
};

export type Placement = DeliveryStage | "excluded" | null;

export function draftFromResponse(response: DeliveryStagesResponse): StageDraft {
  const stages = {} as Record<DeliveryStage, string[]>;
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

/** Where the draft puts a status: a stage, "excluded", or null when nothing names it. */
export function placementOf(draft: StageDraft, status: string): Placement {
  const wanted = key(status);
  if (draft.excluded.some((name) => key(name) === wanted)) return "excluded";
  const stage = (Object.keys(draft.stages) as DeliveryStage[]).find((candidate) =>
    draft.stages[candidate].some((name) => key(name) === wanted),
  );
  return stage ?? null;
}

/** Put a status in one place only: a stage, not counted, or nowhere (its broad state). */
export function moveStatus(draft: StageDraft, status: string, target: Placement): StageDraft {
  const wanted = key(status);
  const without = (names: string[]) => names.filter((name) => key(name) !== wanted);
  const stages = {} as Record<DeliveryStage, string[]>;
  (Object.keys(draft.stages) as DeliveryStage[]).forEach((stage) => {
    stages[stage] = without(draft.stages[stage]);
  });
  const excluded = without(draft.excluded);
  const name = status.trim().replace(/\s+/g, " ");
  if (target === "excluded") excluded.push(name);
  else if (target !== null) stages[target] = [...stages[target], name];
  return { ...draft, stages, excluded };
}

/**
 * The names the draft recognises that the tracker does not carry: what other
 * trackers call these steps, kept so a status that appears later is placed on
 * its own. A status the tracker carries is chosen in its own row instead.
 */
export function otherNames(
  draft: StageDraft,
  trackerStatuses: string[],
): { stages: Record<DeliveryStage, string[]>; excluded: string[]; total: number } {
  const carried = new Set(trackerStatuses.map(key));
  const others = (names: string[]) => names.filter((name) => !carried.has(key(name)));
  const stages = {} as Record<DeliveryStage, string[]>;
  let total = 0;
  (Object.keys(draft.stages) as DeliveryStage[]).forEach((stage) => {
    stages[stage] = others(draft.stages[stage]);
    total += stages[stage].length;
  });
  const excluded = others(draft.excluded);
  return { stages, excluded, total: total + excluded.length };
}

/** Statuses nothing places come first; each group keeps its given order (most common first). */
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
