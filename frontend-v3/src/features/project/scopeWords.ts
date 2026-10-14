// The Project page's one scope line: the planning
// baseline and Jira hygiene in a sentence, their full sections on Scope control.
// "8 at planning (5 Oct) · +2 added since, 1 without the CR label · 1 removed ·
// 3 open hygiene findings". Counts of work items only: nothing names a person.
// Type imports and runtime imports by their .ts path, so `node --test` runs it.
import type { HygieneBoardResponse, PlanningBaselinesResponse } from "../../api/schema";

import { shortDay } from "../overall/timeline.ts";

/** The parts of the line; null when there is nothing to say (neither read answered). */
export function scopeParts(
  baselines: Pick<PlanningBaselinesResponse, "active" | "in_scope"> | undefined,
  hygiene: Pick<HygieneBoardResponse, "open"> | undefined,
): string[] | null {
  if (!baselines && !hygiene) return null;
  const parts: string[] = [];
  const active = baselines?.active ?? null;
  if (active) {
    parts.push(`${active.requirements} at planning (${shortDay(active.planned_on)})`);
    const { added, added_with_label: labelled, removed } = active.since;
    if (added > 0) {
      const without = added - labelled;
      parts.push(
        without > 0
          ? `+${added} added since, ${without} without the ${active.change_label} label`
          : `+${added} added since, all with the ${active.change_label} label`,
      );
    } else {
      parts.push("nothing added since");
    }
    if (removed > 0) parts.push(`${removed} removed`);
  } else if (baselines) {
    parts.push(`Planning is not marked complete yet · ${baselines.in_scope} in scope today`);
  }
  if (hygiene) {
    parts.push(
      hygiene.open === 0
        ? "no open Jira hygiene finding"
        : `${hygiene.open} open Jira hygiene ${hygiene.open === 1 ? "finding" : "findings"}`,
    );
  }
  return parts;
}
