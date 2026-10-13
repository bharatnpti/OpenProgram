// Pure wording and choices for the Pod page (D86, D87). Type imports only, and
// runtime imports by their .ts path, so `node --test` runs it as written.
import type {
  CheckinDeveloperDto,
  CheckinRungDto,
  DirectoryItemResponse,
  Rag,
} from "../../api/schema";
import { formatTime } from "../../lib/format.ts";
import { ragSeverity } from "../../lib/status.ts";
import { plural } from "../../lib/words.ts";
import type { ProjectOption } from "../project/projectChoice.ts";

type Time = (iso: string) => string;

const RUNG_WORDS: Record<NonNullable<CheckinRungDto["target"]>, string> = {
  developer: "Reminder sent",
  scrum_master: "Scrum master told",
  manager: "Manager told",
};

/**
 * One rung of the day's silence ladder that went out: "Reminder sent 13:30",
 * "Scrum master told 17:30". A rung whose target was not recorded when it went
 * out is read against today's ladder, and says so.
 */
export function rungWords(rung: CheckinRungDto, time: Time = formatTime): string {
  const lead = rung.target ? RUNG_WORDS[rung.target] : `Rung ${rung.number} sent`;
  return `${lead} ${time(rung.sent_at)}${rung.recorded ? "" : " (by today's ladder)"}`;
}

type Person = Pick<
  CheckinDeveloperDto,
  "state" | "source" | "status_as_of" | "replied_at" | "asked_at" | "reminders"
>;

/**
 * What a person's day says on "Who needs a nudge": replied (with when), partly
 * replied, filled from Jira (never green), or no reply yet, which is the only
 * one the ladder is for. `asOf` is the day shown.
 */
export function nudgeState(
  person: Person,
  asOf: string,
  time: Time = formatTime,
): { word: string; tone: "success" | "warning" | "neutral"; silent: boolean } {
  const when = person.replied_at ? ` ${time(person.replied_at)}` : "";
  const today = person.status_as_of === asOf;
  if (person.state === "confirmed")
    return { word: `Replied${when}`, tone: "success", silent: false };
  if (person.state === "partial") {
    return { word: `Partly replied${when}`, tone: "warning", silent: false };
  }
  if (today && person.source === "inferred") {
    return { word: "Filled from Jira", tone: "warning", silent: true };
  }
  if (!person.asked_at) return { word: "Not asked yet", tone: "neutral", silent: false };
  return { word: "No reply yet", tone: "neutral", silent: true };
}

/** The ladder line: "Asked 09:30 · Reminder sent 13:30 · Scrum master told 17:30". */
export function ladderLine(person: Person, time: Time = formatTime): string {
  return [
    person.asked_at ? `Asked ${time(person.asked_at)}` : null,
    ...(person.reminders ?? []).map((rung) => rungWords(rung, time)),
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** "09:30 Europe/Berlin": a person's own check-in time, in their zone. */
export function scheduleWords(
  person: Pick<CheckinDeveloperDto, "checkin_time" | "timezone">,
): string | null {
  if (!person.checkin_time) return null;
  return person.timezone ? `${person.checkin_time} ${person.timezone}` : person.checkin_time;
}

/**
 * The line under the pod's name: "Scrum master Ira Novak · 6 members · 4 of 6
 * replied today · 1 partly · 2 open blockers", the replies counted as the
 * check-in boards count them (`repliedCount`). Each part only when it is known.
 */
export function podLine(parts: {
  scrumMaster: string | null;
  members: number;
  replied: number | null;
  /** Of those asked, how many replied in part only. */
  partly?: number;
  counted: number | null;
  day: string;
  blockers: number | null;
}): string {
  return [
    parts.scrumMaster ? `Scrum master ${parts.scrumMaster}` : null,
    plural(parts.members, "member", "members"),
    parts.replied !== null && parts.counted !== null
      ? `${parts.replied} of ${parts.counted} replied ${parts.day}`
      : null,
    parts.replied !== null && parts.partly ? `${parts.partly} partly` : null,
    parts.blockers !== null ? plural(parts.blockers, "open blocker", "open blockers") : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** The pod's scrum master: the member its `sm_id` names, else its escalation contact's name. */
export function scrumMasterOf(
  pod: Pick<DirectoryItemResponse, "metadata" | "people">,
): string | null {
  const named = pod.people?.find((person) => person.key === "sm_id");
  if (named?.name) return named.name;
  const shown = pod.metadata?.escalation_sm_display_name;
  return typeof shown === "string" && shown.trim() ? shown.trim() : null;
}

/**
 * The pods as the switcher lists them (D86): the person's own first, the rest
 * under their first project, worst first; the project switcher's own sections
 * and search, with a pod's projects standing where a project's programmes do.
 */
export function podOptions(
  pods: Pick<DirectoryItemResponse, "id" | "name" | "rag" | "project_ids">[],
  mine: ReadonlySet<string>,
  colour: (pod: { id: string; rag: Rag | null }) => Rag | null = (pod) => pod.rag,
): ProjectOption[] {
  return pods.map((pod) => ({
    id: pod.id,
    name: pod.name,
    rag: colour({ id: pod.id, rag: pod.rag }),
    programIds: pod.project_ids,
    jiraKey: null,
    mine: mine.has(pod.id),
  }));
}

/**
 * The pod a bare /pods opens: for someone with pods of their own, the worst of
 * them; else the one they opened last, if still listed; else the worst pod.
 */
export function defaultPod(options: ProjectOption[], recent: readonly string[]): string | null {
  const level = (option: ProjectOption) => (option.rag === null ? -1 : ragSeverity(option.rag));
  const worst = (list: ProjectOption[]) =>
    [...list].sort((a, b) => level(b) - level(a) || a.name.localeCompare(b.name))[0]?.id ?? null;
  const mine = options.filter((option) => option.mine);
  if (mine.length > 0) return worst(mine);
  const last = recent.find((id) => options.some((option) => option.id === id));
  return last ?? worst(options);
}

/** The storage key of the pods opened lately, per person. */
export function recentPodsKey(who: string | null | undefined): string {
  return `openprogram.v3.recent-pods.${who || "anyone"}`;
}
