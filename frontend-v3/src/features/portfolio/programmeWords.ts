// A programme's health in words, pure: what rolls up into it, and the warnings'
// count line beyond the total. Type imports, and runtime imports by their .ts
// path, so `node --test` runs it as written.
import type { PortfolioWarningsResponse, Verdict, VerdictCountsDto } from "../../api/schema";
import { plural } from "../../lib/words.ts";

/** Worst first, as the pod bar ranks verdicts: a date nobody committed to sits just under late. */
const VERDICT_ORDER: readonly Verdict[] = [
  "off_track",
  "no_date",
  "at_risk",
  "not_enough_data",
  "on_track",
  "done",
];

/**
 * A programme's date verdict for its dot in the programme picker: the worst
 * verdict among its projects; null for a programme none of whose projects has one.
 */
export function worstVerdict(counts: VerdictCountsDto | null | undefined): Verdict | null {
  if (!counts) return null;
  return VERDICT_ORDER.find((verdict) => (counts[verdict] ?? 0) > 0) ?? null;
}

/** Fewer people than this, and a group's people are not counted for a reader who sees no names. */
export const SMALL_GROUP = 5;

/**
 * What rolls up into a programme, as chips: "3 projects", "5 workstreams" (only
 * when it uses any: they are optional), "4 pods", "13 people", "16 tasks".
 * People are the tree's own when it lists them; a reader whose tree names
 * nobody gets the programme's count instead, and none for a group under five.
 */
export function rollUpChips(
  nodes: readonly { kind: string }[],
  peopleUnder: number | null | undefined,
): string[] {
  const count = (kind: string) => nodes.filter((node) => node.kind === kind).length;
  const listed = count("developer");
  const people = listed > 0 ? listed : (peopleUnder ?? 0) >= SMALL_GROUP ? (peopleUnder ?? 0) : 0;
  const chips: [number, string, string][] = [
    [count("project"), "project", "projects"],
    [count("workstream"), "workstream", "workstreams"],
    [count("pod"), "pod", "pods"],
    [people, "person", "people"],
    [count("task"), "task", "tasks"],
  ];
  return chips
    .filter(([value, one]) => value > 0 || one === "project" || one === "pod")
    .map(([value, one, many]) => plural(value, one, many));
}

/**
 * The warnings' count line, said once at the top of All projects › Early
 * warnings: "19 open across 3 projects · 1 where the owner says on track ·
 * 1 drift". The watermelons are the items whose owner says all is well while
 * the tools disagree; drift is what Jira and Git say against each other or
 * against what was reported (the tools-disagree and done-in-code groups). Each
 * is said beside any open warning, 0 included, so none reads apart from not
 * counted.
 */
export function warningsCountLine(
  view: Pick<PortfolioWarningsResponse, "total" | "projects" | "groups">,
): string {
  if (view.total === 0) return "Nothing open: Jira and Git agree with what was reported.";
  const watermelons = view.groups
    .flatMap((group) => group.items)
    .filter((item) => item.watermelon).length;
  const drift = view.groups
    .filter((group) => group.kind === "tools_disagree" || group.kind === "done_in_code")
    .reduce((sum, group) => sum + group.count, 0);
  const parts = [
    `${view.total} open across ${plural(view.projects, "project", "projects")}`,
    `${watermelons} where the owner says on track`,
    `${drift} drift`,
  ];
  return `${parts.join(" · ")}.`;
}
