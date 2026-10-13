// The assistant: its name, how it greets, the questions it suggests on each
// page, and how an answer's sources and a refusal read. Pure, with type imports
// and pure modules by their .ts path, so `node --test` runs it as written.
import type { AskSourceResponse } from "../../api/schema";
import type { Page, PaletteTargets } from "../../app/access";
import { actionError } from "../../lib/errors.ts";
import { greetingWord, spaced } from "../../lib/words.ts";

/** The assistant's name. Every place that names it reads it from here. */
export const ASSISTANT_NAME = "Ora";

/** The button, the palette row and the panel's own label. */
export const ASK_LABEL = `Ask ${ASSISTANT_NAME}`;

/**
 * The one-line note under the input. It says an AI writes the answers, as
 * people talking to one must be told, and where to check them: the sources.
 */
export const ANSWER_NOTE = `${ASSISTANT_NAME} writes answers with AI from your delivery data. Check the sources before you act.`;

/** The switch in the composer: on, a question is investigated in steps. */
export const INVESTIGATE_LABEL = "Investigate";
/** Under a quick answer: the same question, investigated. */
export const INVESTIGATE_THIS = "Investigate this";

/** The switch's tooltip, both ways. */
export function investigateTitle(on: boolean): string {
  return on
    ? "Investigating: checks the question step by step. Click for a quick answer."
    : "Quick answer. Click to investigate step by step instead.";
}

/** What the waiting bubble says: looking it up, planning, or checking the steps planned. */
export function pendingWords(investigating: boolean, steps: number): string {
  if (!investigating) return `${ASSISTANT_NAME} is looking it up…`;
  if (steps === 0) return `${ASSISTANT_NAME} is planning the steps… This can take a minute.`;
  return `${ASSISTANT_NAME} is checking ${steps === 1 ? "1 step" : `${steps} steps`}…`;
}

/** The header's actions. */
export const NEW_CHAT_LABEL = "New chat";
export const OPEN_PAGE_LABEL = `Open ${ASSISTANT_NAME} full page`;

/** The fold under an answer that lists its sources: "Sources (4)". */
export function sourcesWords(count: number): string {
  return `Sources (${count})`;
}

/** The fold under an investigated answer: "How Ora checked: 3 steps". */
export function checkedWords(steps: number): string {
  return `How ${ASSISTANT_NAME} checked: ${steps === 1 ? "1 step" : `${steps} steps`}`;
}

/** "Good morning, Ira — what would you like to know?"; no name when nobody names the person. */
export function assistantGreeting(first: string | null | undefined, now: Date = new Date()) {
  return `${greetingWord(now)}${first ? `, ${first}` : ""} — what would you like to know?`;
}

/** What the page being looked at shows, when it shows one thing. */
export type AskSubject = { kind: "program" | "project" | "workstream" | "pod"; name: string };

/** Where the person is: the page, its view, and the one thing it shows. */
export type AskPlace = {
  page: Page;
  /** Reports: "daily" inside a project's day report, "briefs" on the briefs, else null. */
  view: string | null;
  subject: AskSubject | null;
};

/** Each page's first path segment (app/access.ts keeps the same map; this module stays pure). */
const PAGES: Record<string, Page> = {
  today: "today",
  "check-in": "checkin",
  requests: "requests",
  portfolio: "portfolio",
  projects: "projects",
  pods: "pods",
  warnings: "warnings",
  scope: "scope",
  reports: "reports",
  flow: "flow",
  ora: "ora",
  people: "people",
  admin: "admin",
};

/** The page a path is, with its view; an address no page owns reads as Today. */
export function placeOf(pathname: string): Omit<AskPlace, "subject"> {
  const [, first = "", second = "", third = ""] = pathname.split("/");
  const page = Object.prototype.hasOwnProperty.call(PAGES, first) ? PAGES[first] : "today";
  if (page === "reports") {
    return { page, view: second === "briefs" ? "briefs" : third === "daily" ? "daily" : null };
  }
  return { page, view: null };
}

/**
 * Two to four questions that fit the page and what it shows, worded for the
 * day shown (`day` is "today", or "on Mon 5 Oct" for a past day). The pod
 * questions are for the roles that read a pod's check-ins and blockers
 * (`podDetail`): the scrum master, manager and admin.
 */
export function suggestionsFor(
  place: AskPlace,
  { podDetail, day = "today" }: { podDetail: boolean; day?: string },
): string[] {
  const subject = place.subject;
  const name = subject?.name ?? "";
  const portfolio = [
    "Which projects are most at risk, and why?",
    "What changed in the last 7 days?",
    podDetail ? "Which blockers have been open longest?" : "Which risks have been open longest?",
  ];
  const pod = [
    `Will ${name} make its date?`,
    `What is blocking ${name}, and for how long?`,
    `Who in ${name} has not replied ${day}?`,
  ];
  const project = [
    `Is ${name} on track for its date?`,
    "Who do we need an answer from?",
    `What changed in ${name} in the last 7 days?`,
  ];
  const node = [
    `Is ${name} on track for its date?`,
    `What is holding ${name} back?`,
    `What changed in ${name} in the last 7 days?`,
  ];
  switch (place.page) {
    case "today":
      if (subject?.kind === "pod" && podDetail) return pod;
      if (subject?.kind === "project") {
        return [
          `Is ${name} on track for its date?`,
          `Which requirements in ${name} have no ETA or due date?`,
          "Who do we need an answer from?",
        ];
      }
      return portfolio;
    case "checkin":
      return ["Which of my tasks are past their due date?", "What changed in the last 7 days?"];
    case "requests":
      return [
        "Which requests have waited longest?",
        "Who do we need an answer from?",
        "What did this week's briefs flag?",
      ];
    case "portfolio":
      return [
        "Which projects will miss their committed date?",
        "Which project needs attention first?",
        "What changed in the last 7 days?",
      ];
    case "projects":
      return subject?.kind === "project" ? project : portfolio;
    case "pods":
      if (subject?.kind === "pod" && podDetail) return pod;
      return subject ? node : portfolio;
    case "warnings":
      return [
        "Which risks have been open longest?",
        "Which merge requests wait longest for review?",
        "Where do the check-ins and the trackers disagree?",
      ];
    case "scope":
      return [
        "What was added after planning, and did it move the date?",
        "Which projects have no committed date?",
      ];
    case "reports":
      if (place.view === "briefs") {
        return ["What did this week's briefs flag?", "Which project needs attention first?"];
      }
      return subject?.kind === "project"
        ? project
        : [
            "Which projects will miss their committed date?",
            "Which project needs attention first?",
          ];
    case "flow":
      return [
        "Which merge requests wait longest for review?",
        "Where does work get stuck in review?",
        "Which repositories take longest to review?",
      ];
    case "ora":
      return portfolio;
    case "people":
      return ["Which pods have no scrum master?", "What changed in the last 7 days?"];
    case "admin":
      return ["Which projects have no committed date?", "Who has not checked in this week?"];
  }
}

/** "Asking about Mon 5 Oct, the day shown." while a past day is shown; null for today. */
export function askedAboutWords(dayLabel: string | null): string | null {
  return dayLabel ? `Asking about ${dayLabel}, the day shown.` : null;
}

/**
 * Where an answer's source opens for this role (app/access.ts `paletteTargets`):
 * a program, project, workstream or pod where the role has its page, else
 * nowhere, and the source is named as plain text.
 */
export function sourceLink(source: AskSourceResponse, targets: PaletteTargets): string | null {
  const kind = source.kind;
  if (kind === "program" || kind === "project" || kind === "workstream" || kind === "pod") {
    return targets[kind]?.(source.id) ?? null;
  }
  return null;
}

/** "Checkout Revamp", with what it is for a tooltip: "project", "work item". */
export function sourceWords(source: AskSourceResponse): { label: string; kind: string | null } {
  return { label: source.label || source.id, kind: source.kind ? spaced(source.kind) : null };
}

/** A failed question in plain words; a 403 is "Not available to you", with the server's reason. */
export function askError(error: unknown): string {
  const e = (typeof error === "object" && error !== null ? error : {}) as {
    status?: unknown;
    message?: unknown;
  };
  if (e.status === 403) {
    const said = typeof e.message === "string" && e.message ? ` The server said: ${e.message}` : "";
    return `Not available to you.${said}`;
  }
  return actionError(error, "answer that");
}
