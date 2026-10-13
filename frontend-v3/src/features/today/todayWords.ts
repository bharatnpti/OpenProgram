// Pure words for Today's cards (console IA lane T, D96): the replies of the day,
// open warnings, the Jira changes waiting for the person's yes and who must act.
// Type imports and extensioned imports only, so `node --test` runs it.
import type {
  JiraChangeDto,
  JiraChangeGateDto,
  ProjectAskDto,
  RepliesCountDto,
} from "../../api/schema";
import { formatDay } from "../../lib/format.ts";
import { daysLabel, plural } from "../../lib/words.ts";

/** How many rows Who must act shows before "Show all". */
export const WHO_MUST_ACT_SHOWN = 8;

/** The line every Today closes with: findings are about work, and nobody is ranked. */
export function footerLine(day: string | null): string {
  const when = day ? ` As of ${formatDay(day)}.` : "";
  return `Findings attach to work items, not people. Nobody is ranked.${when}`;
}

/**
 * Replies today: "8 of 14 replied", and on its own line the ones the tracker
 * filled in, which never count as a reply and are never green.
 */
export function repliesWords(
  replies: RepliesCountDto,
  tracker = "Jira",
): { count: string; headline: string; inferred: string | null; waiting: string | null } {
  if (replies.people === 0) {
    return {
      count: "0",
      headline: "Nobody in your teams is asked yet.",
      inferred: null,
      waiting: null,
    };
  }
  return {
    count: `${replies.replied}/${replies.people}`,
    headline: `${replies.replied} of ${plural(replies.people, "person", "people")} replied`,
    inferred:
      replies.inferred > 0
        ? `${replies.inferred} filled from ${tracker}, marked as inferred, never green.`
        : null,
    waiting: replies.waiting > 0 ? `${replies.waiting} not yet.` : null,
  };
}

/** Open warnings: "6 open", "None open". */
export function warningsWords(count: number): string {
  return count === 0 ? "None open" : `${count} open`;
}

/** The change one row asks for: "CHK-4: move to Done", "CHK-5: due date". */
export function changeTitle(item: Pick<JiraChangeDto, "kind" | "issue_key" | "target_label">) {
  if (item.kind === "due_date") return `${item.issue_key}: due date`;
  return `${item.issue_key}: move to ${item.target_label ?? "its new state"}`;
}

/**
 * What a yes would meet now, said plainly; null when nothing stands in its way.
 * The console offers no yes for any of these but "already there", where a yes
 * only closes the question.
 */
export function gateWords(
  gate: JiraChangeGateDto | null | undefined,
  item: Pick<JiraChangeDto, "issue_key" | "target_label">,
  tracker = "Jira",
): string | null {
  if (!gate) return null;
  switch (gate.state) {
    case "open":
      return null;
    case "no_change":
      return `Already ${item.target_label ?? "there"} in ${tracker}: a yes only closes the question.`;
    case "off":
      return `Updating ${tracker} from OpenProgram is off right now, so only a no can be given.`;
    case "not_owner":
      return `${item.issue_key} is not assigned to you in ${tracker} any more.`;
    case "held_open_mr": {
      const many = gate.merge_requests.length > 1;
      const named = gate.merge_requests.join(", ") || "Its merge request";
      return `${named} ${many ? "are" : "is"} still open, so it can't move to Done yet.`;
    }
    case "unreadable":
      return `${tracker} could not be read just now. Try again in a minute.`;
    default:
      return null;
  }
}

/** The buttons a row gets: a yes only where the gates let it through; a no for any answer. */
export function answerButtons(item: Pick<JiraChangeDto, "answerable" | "gate">): {
  yes: boolean;
  no: boolean;
} {
  if (!item.answerable) return { yes: false, no: false };
  return { yes: Boolean(item.gate?.yes_allowed), no: true };
}

/** A due date the console never writes: what the person does instead. */
export function dueDateWords(tracker = "Jira"): string {
  return `Change it in ${tracker}: OpenProgram never writes a due date.`;
}

/**
 * The person's check-in, in one line, from their status and the server's day:
 * replied today, waiting for a yes on Jira changes, or not replied yet.
 */
export function checkinStateWords(input: {
  /** The status's day and source, or null when there is none yet. */
  status: { status_as_of: string | null; source: string } | null;
  /** The server's day. */
  today: string | null;
  /** Changes the day's check-in waits on. */
  waitingOnYes: number;
}): { replied: boolean; line: string } {
  if (input.waitingOnYes > 0) {
    return {
      replied: false,
      line: `Your check-in waits for your yes or no on ${plural(input.waitingOnYes, "Jira change", "Jira changes")}.`,
    };
  }
  const own = input.status?.source === "confirmed" || input.status?.source === "partial";
  if (own && input.today && input.status?.status_as_of === input.today) {
    return { replied: true, line: "You replied today." };
  }
  return { replied: false, line: "Not replied yet today." };
}

/** A who-must-act row's age: "today", "3 days", "—" when it can't be told. */
export function askAge(days: number | null | undefined): string {
  return days === null || days === undefined ? "—" : daysLabel(days);
}

/** Who must act: the person, or for an executive the team; never empty. */
export function askWho(ask: Pick<ProjectAskDto, "who">): string {
  return ask.who ?? "Nobody named yet";
}

/**
 * Why it matters: facts about the item only, never anyone's reply. Its kind,
 * how long it has waited, who raised it or what it waits on, and how far up
 * the escalation matrix it has gone.
 */
export function askWhy(
  ask: Pick<
    ProjectAskDto,
    "need_label" | "waited_days" | "detail" | "escalated_to" | "escalation_label" | "level"
  >,
): string {
  const waited =
    ask.waited_days === null || ask.waited_days === undefined
      ? null
      : ask.waited_days <= 0
        ? "since today"
        : `for ${plural(ask.waited_days, "day", "days")}`;
  const parts = [waited ? `${ask.need_label} needed ${waited}` : `${ask.need_label} needed`];
  if (ask.detail) parts.push(ask.detail);
  if (ask.escalated_to) {
    parts.push(
      `moved up to ${ask.escalated_to}${ask.escalation_label ? ` (${ask.escalation_label})` : ""}`,
    );
  } else if (ask.level > 1 && ask.escalation_label) {
    parts.push(`moved up to the ${ask.escalation_label}`);
  }
  return parts.join(" · ");
}

/**
 * The asks of several projects as one list, by the item and never by person:
 * the level reached, then how long it waited, as the server orders one
 * project's. `teams` keeps only the asks of those teams (a scrum master's pods).
 */
export function mergeAsks(
  lists: readonly (readonly ProjectAskDto[])[],
  teams?: ReadonlySet<string>,
): ProjectAskDto[] {
  const all = lists.flat().filter((ask) => !teams || (ask.team != null && teams.has(ask.team)));
  return all
    .map((ask, index) => ({ ask, index }))
    .sort(
      (a, b) =>
        b.ask.level - a.ask.level ||
        (b.ask.waited_days ?? 0) - (a.ask.waited_days ?? 0) ||
        a.index - b.index,
    )
    .map(({ ask }) => ask);
}
