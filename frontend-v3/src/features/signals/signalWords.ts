// Only type imports here: this module runs under `node --test` as written.
import type { NameOf } from "../../app/names";
import type { PortfolioFlowResponse } from "../../api/schema";

/**
 * Whether the flow read measured anything at all: every count is zero and
 * there is no average. Flow counts the work items an organisation groups in
 * workstreams; one that tracks its work as Jira tasks in pods has none, and a
 * row of zeros beside real risks reads as "nothing is moving".
 */
export function flowIsEmpty(
  flow: Pick<
    PortfolioFlowResponse,
    | "active_count"
    | "features_in_flight"
    | "completed_count"
    | "stale_count"
    | "abandoned_count"
    | "avg_cycle_time_days"
    | "avg_pr_age_days"
    | "workstreams"
  >,
): boolean {
  return (
    flow.active_count === 0 &&
    flow.features_in_flight === 0 &&
    flow.completed_count === 0 &&
    flow.stale_count === 0 &&
    flow.abandoned_count === 0 &&
    flow.avg_cycle_time_days === null &&
    flow.avg_pr_age_days === null &&
    flow.workstreams.length === 0
  );
}

/** What the Flow view says when it measured nothing, instead of zeros. */
export const FLOW_EMPTY_TITLE = "Flow isn't measured here yet";
export const FLOW_EMPTY_WORDS =
  "Flow counts work items: features with a state, a branch and a pull request. This organisation has none, and flow does not read tasks or pods. Nothing was measured, so there are no figures to show, not zeros. The risks and drift on this screen don't depend on it.";

/**
 * What an empty Drift list says, so "Drift · 0" beside "signals disagree on 5
 * issues" elsewhere is not read as a contradiction: the two count different things.
 */
export const DRIFT_EMPTY_WORDS =
  "No drift: nothing anyone reported disagrees with what Jira and Git show (work said done with no merged request, for example). A merge request open too long is listed under Risks, and counts as “signals disagree” on Delivery and Today.";

/**
 * The subject of a finding, in words. A risk or drift finding about a person is
 * filed on the person's id; saying "developer U123456" tells nobody who. A person
 * is named (the finding's own name, else the directory's), a node that is not a
 * person stays "kind id", and an id nobody names is still shown, flagged as unnamed.
 */
export function findingSubject(
  ref: { kind: string; id: string },
  personName: string | null | undefined,
  names: NameOf,
): string {
  if (ref.kind === "developer") return personName?.trim() || names.or(ref.id, "unnamed person");
  return `${ref.kind.replace(/_/g, " ")} ${ref.id}`;
}
