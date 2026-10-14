// Early warnings (`/warnings`): the groups and their items in words, pure, from the
// bulk read (`GET /portfolio/warnings`). Findings attach to work items; a
// person appears only as the owner of one, and a reader who sees no person reads
// the team. Type imports, and runtime imports by their .ts path, so `node --test`
// runs it as written.
import type {
  PortfolioWarningsResponse,
  WarningGroupDto,
  WarningItemDto,
  WarningKind,
} from "../../api/schema";
import { formatDay } from "../../lib/format.ts";

/** The groups always shown, a count of 0 included: what the page is for. */
const ALWAYS: readonly WarningKind[] = [
  "blockers",
  "tools_disagree",
  "done_in_code",
  "review_stuck",
];

/** The items a group lists before "Show all". */
export const SHOWN = 5;

/** The groups to draw: the four main ones always, the others only with something open. */
export function shownGroups(view: PortfolioWarningsResponse): WarningGroupDto[] {
  return view.groups.filter((group) => ALWAYS.includes(group.kind) || group.count > 0);
}

/** "19 open across 3 projects", the header's count line. */
export function countLine(view: Pick<PortfolioWarningsResponse, "total" | "projects">): string {
  if (view.total === 0) return "Nothing open: Jira and Git agree with what was reported.";
  const projects = `${view.projects} ${view.projects === 1 ? "project" : "projects"}`;
  return `${view.total} open across ${projects}.`;
}

/**
 * How old an item is: "6 days", "today", or, while the scan's record of when it
 * was first seen starts on the day it was first recorded, "seen since 14 Oct":
 * a lower bound, never a guess.
 */
export function ageWords(
  item: Pick<WarningItemDto, "age_days" | "age_known" | "since">,
  seenSince: string | null | undefined,
): string {
  if (!item.age_known) {
    const from = seenSince ?? item.since;
    if (!from || item.age_days === 0) return "seen today";
    return `seen since ${formatDay(from)}`;
  }
  if (item.age_days === 0) return "today";
  return `${item.age_days} ${item.age_days === 1 ? "day" : "days"}`;
}

/** Who acts on it, as the reader may see it: "You", a name with its team, or the team. */
export function ownerWords(
  item: Pick<WarningItemDto, "owner" | "owner_team" | "owner_is_you">,
  kind: WarningKind,
): string {
  const role = kind === "blockers" ? "Resolves it" : "Owner";
  if (item.owner_is_you) return `${role}: you`;
  if (item.owner) return `${role}: ${item.owner}${item.owner_team ? ` · ${item.owner_team}` : ""}`;
  if (item.owner_team) return `${role}: ${item.owner_team}`;
  return "No owner named yet";
}

/**
 * The item's line after its name: "has had no state change in 21 days." where
 * the line starts with the name it stands beside, so the name is said once.
 */
export function itemLine(item: Pick<WarningItemDto, "key" | "line">): string {
  const key = item.key.trim();
  const line = item.line.trim();
  if (key && line.toLowerCase().startsWith(`${key.toLowerCase()} `)) {
    return line.slice(key.length + 1);
  }
  return line;
}

/** The empty line of a group with nothing open. */
export function emptyWords(kind: WarningKind): string {
  return {
    blockers: "No open blockers.",
    tools_disagree: "Every reported status agrees with the tools.",
    done_in_code: "Nothing merged is still open.",
    review_stuck: "No merge request is stuck in review.",
    no_movement: "Nothing has stopped moving.",
    no_code_yet: "All active work has code.",
  }[kind];
}
