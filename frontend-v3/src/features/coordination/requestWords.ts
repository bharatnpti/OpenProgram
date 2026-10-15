// Pure wording for the Requests page and a request's trail. Type imports
// only, and runtime imports by their .ts path, so `node --test` runs it as written.
import type {
  CrossPersonRequestResponse,
  RequestCountsResponse,
  RequestTrailStepDto,
} from "../../api/schema";
import { daysBetween, formatDay, formatTime } from "../../lib/format.ts";
import { plural } from "../../lib/words.ts";

/** How the board is filtered: what still waits, or what was resolved lately. */
export type RequestState = "open" | "resolved";

/** How a day and a time are said; tests hand in fixed ones. */
export type Say = { day: (iso: string) => string; time: (iso: string) => string };

const SAY: Say = { day: formatDay, time: formatTime };

/** "scrum master" from the matrix level's own name ("Scrum master"); a member level keeps its name. */
function levelWords(role: string | null | undefined): string {
  if (!role) return "the next level";
  return role.charAt(0).toLowerCase() + role.slice(1);
}

/** Who stands at a step, or the step's role for a reader who reads no names. */
function whoOr(who: string | null | undefined, fallback: string): string {
  return who && who.trim() ? who : fallback;
}

export type TrailLine = {
  /** What happened, or what is due, in one sentence. */
  title: string;
  /** When, and anything that qualifies it. */
  detail: string | null;
  /** Due, not yet carried: drawn as an open step. */
  due: boolean;
  /** Worked out from the escalation matrix rather than recorded. */
  computed: boolean;
};

/**
 * One step of a request's trail in plain words. Every DM is said as it went:
 * sent, still being retried, not delivered, or none. A step from the matrix says
 * so, and whether a day report carried it.
 */
export function trailLine(step: RequestTrailStepDto, say: Say = SAY): TrailLine {
  const at = step.at ? `${say.day(step.at)}, ${say.time(step.at)}` : null;
  const base = { due: step.state === "due", computed: step.computed ?? false };
  switch (step.kind) {
    case "unmatched":
      return {
        ...base,
        title: step.who ? `“${step.who}” matched nobody` : "The name in the ask matched nobody",
        detail: [at, `${whoOr(step.by, "The asker")} was asked whom they meant`]
          .filter(Boolean)
          .join(" · "),
      };
    case "asked":
      return {
        ...base,
        title: `Asked ${whoOr(step.who, "the person asked")} directly`,
        detail: [at, dmWords(step, say)].filter(Boolean).join(" · "),
      };
    case "acknowledged":
      return {
        ...base,
        title: step.by ? `Acknowledged by ${step.by}` : "Acknowledged",
        detail:
          [at ?? "When was not recorded", step.eta ? `expects it ${step.eta}` : null]
            .filter(Boolean)
            .join(" · ") || null,
      };
    case "named":
      return { ...base, ...namedWords(step, say) };
    case "nobody_above":
      return {
        ...base,
        title: step.on ? `Due to go up from ${say.day(step.on)}` : "Due to go up",
        detail: `Nobody is named above ${whoOr(step.who, "the person asked")} on the escalation matrix`,
      };
    case "resolved":
      return { ...base, title: step.by ? `Resolved by ${step.by}` : "Resolved", detail: at };
    case "cleared":
      return { ...base, title: "Cleared: the merge landed", detail: at };
    case "closed":
      return { ...base, title: "Closed as a copy of a newer ask", detail: at };
    default:
      return { ...base, title: "A step OpenProgram does not word yet", detail: at };
  }
}

/** The DM to the person asked, as it went. Never "sent" unless it was. */
function dmWords(step: RequestTrailStepDto, say: Say): string {
  const attempts = step.dm_attempts ?? 0;
  switch (step.dm) {
    case "sent":
      return step.dm_at ? `DM sent ${say.time(step.dm_at)}` : "DM sent";
    case "retrying":
      return `DM not sent yet, trying again (${plural(attempts, "attempt", "attempts")} so far)`;
    case "not_delivered":
      return `DM not delivered after ${plural(attempts, "attempt", "attempts")}`;
    default:
      return "No DM was sent";
  }
}

function namedWords(step: RequestTrailStepDto, say: Say): Pick<TrailLine, "title" | "detail"> {
  const role = levelWords(step.role);
  const who = step.who ? `${step.who} (${role})` : `the ${role}`;
  const day = step.on ? say.day(step.on) : "a later day";
  const report = step.project_name ? `${step.project_name}'s day report` : "the day report";
  const matrix = "By the escalation matrix; the report names them, nobody gets a DM for it";
  switch (step.report) {
    case "sent":
      return { title: `Named to ${who} in ${report} of ${day}`, detail: matrix };
    case "not_sent":
      return {
        title: `Due to ${who} from ${day}`,
        detail: `By the escalation matrix. No day report went out while it stood there, so nobody was told`,
      };
    case "no_report":
      return {
        title: `Reaches ${who} from ${day}`,
        detail: `By the escalation matrix. ${step.project_name ?? "The project"} has no day report, so nobody is told`,
      };
    case "upcoming":
      return {
        title: `Goes to ${who} from ${day} if still open`,
        detail: `By the escalation matrix, in ${report}`,
      };
    default:
      return { title: `Named to ${who} from ${day}`, detail: matrix };
  }
}

/** Days an ask has waited, by the viewer's calendar: 0 when raised today. */
export function waitedDays(request: Pick<CrossPersonRequestResponse, "created_at">, today: string) {
  return Math.max(0, daysBetween(request.created_at, today));
}

/**
 * The Status cell: "Open · 6d", "Acknowledged · 2d", "Needs who · 3d" while it
 * waits; "Resolved Fri 9 Oct" once done. The days are how long it has waited.
 */
export function statusWords(
  request: Pick<CrossPersonRequestResponse, "status" | "created_at" | "updated_at">,
  today: string,
  say: Say = SAY,
): { word: string; tone: "warning" | "success" | "neutral" | "danger" } {
  const days = `${waitedDays(request, today)}d`;
  switch (request.status) {
    case "open":
      return { word: `Open · ${days}`, tone: "warning" };
    case "acknowledged":
      return { word: `Acknowledged · ${days}`, tone: "neutral" };
    case "needs_resolution":
      return { word: `Needs who · ${days}`, tone: "danger" };
    case "resolved":
      return { word: `Resolved ${say.day(request.updated_at)}`, tone: "success" };
    default:
      return { word: "Closed", tone: "neutral" };
  }
}

/** The order of the open board: the longest wait first, then by who asked. */
export function longestWaitFirst<R extends Pick<CrossPersonRequestResponse, "created_at" | "id">>(
  a: R,
  b: R,
): number {
  return a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id);
}

/** The resolved list: the newest resolution first. */
export function newestFirst<R extends Pick<CrossPersonRequestResponse, "updated_at" | "id">>(
  a: R,
  b: R,
): number {
  return b.updated_at.localeCompare(a.updated_at) || a.id.localeCompare(b.id);
}

/** Whether the request matches the typed words: in its note, either person, or its kind. */
export function matchesSearch(text: string, query: string): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = text.toLowerCase();
  return words.every((word) => hay.includes(word));
}

export type CountTile = { label: string; value: number; note: string };

/**
 * An executive's Requests: counts only, never who asked whom. Each tile
 * says what it counts in a few words.
 */
export function countTiles(counts: RequestCountsResponse): CountTile[] {
  return [
    { label: "Open", value: counts.open, note: "Asked, not yet taken on" },
    { label: "Acknowledged", value: counts.acknowledged, note: "Taken on, not yet done" },
    {
      label: "Waiting over a week",
      value: counts.waiting_over_a_week,
      note: "Open or acknowledged for 7 days or more",
    },
    {
      label: "Resolved",
      value: counts.resolved_recently,
      note: `In the last ${counts.resolved_window_days} days`,
    },
  ];
}

/** The line under the tiles, for what the tiles leave out: unmatched names and failed DMs. */
export function countsFootnote(counts: RequestCountsResponse): string | null {
  const parts = [
    counts.needs_resolution > 0
      ? `${plural(counts.needs_resolution, "ask names", "asks name")} someone nobody matched yet`
      : null,
    counts.not_delivered > 0
      ? `${plural(counts.not_delivered, "DM", "DMs")} to the person asked did not go out`
      : null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? `${parts.join("; ")}.` : null;
}
