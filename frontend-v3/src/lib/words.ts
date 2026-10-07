// Pure wording helpers, type imports only so `node --test` can run them.
import type { StatusSource } from "../api/schema";

const SOURCE_WORDS: Record<StatusSource, string> = {
  confirmed: "confirmed",
  partial: "partly answered",
  inferred: "inferred",
  stale: "stale",
  // The source of a row whose person never answered: nothing was reported, so
  // say that rather than the word "unknown".
  unknown: "no status",
};

/**
 * Where a task's or blocker's status came from, with confidence when the server
 * gives one. Not for a person's own check-in: that has its own words
 * (lib/checkinWords.ts), so "confirmed" means one thing there.
 */
export function sourceLine(source: StatusSource | null | undefined, confidence?: number | null) {
  const word = source ? SOURCE_WORDS[source] : "no status";
  return confidence === null || confidence === undefined
    ? word
    : `${word} · ${Math.round(confidence * 100)}% confidence`;
}

/** "1 person", "2 people": the count with the word that fits it. */
export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** How long, in days, as a request or blocker is said to have waited: "today", "1 day", "16 days". */
export function daysLabel(days: number): string {
  return days <= 0 ? "today" : plural(days, "day", "days");
}

/**
 * The day shown and the program, the way every Today opens. `day` is the day
 * the screen's numbers are for (a past day being viewed, or the server's
 * today); without one it is the browser's today. With several programs (a
 * person whose pods feed more than one) they are listed, so the line never
 * names one program for work that sits in another.
 */
export function todayEyebrow(
  programNames: string | string[] | null | undefined,
  day?: string | null,
): string {
  const moment = day ? new Date(`${day.slice(0, 10)}T12:00:00`) : new Date();
  const label = moment.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const names = (Array.isArray(programNames) ? programNames : [programNames]).filter(
    (name): name is string => Boolean(name),
  );
  return names.length > 0 ? `${label} · ${names.join(" · ")}` : label;
}

/** "Good morning", "Good afternoon" or "Good evening", by the browser's own clock. */
export function greetingWord(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/**
 * The first name in a person's full name, to greet them by: "Liam Chen" is
 * "Liam", and "Chen, Liam" (surname first, as some directories write it) is
 * "Liam" too. A name that is really an address ("liam@example.com") or empty
 * has no first name: null, never a guess from the part before the "@".
 */
export function firstName(fullName: string | null | undefined): string | null {
  const name = (fullName ?? "").trim().replace(/\s+/g, " ");
  if (!name || name.includes("@")) return null;
  const [before, after] = name.split(",").map((part) => part.trim());
  const given = (after || before).split(" ")[0];
  return given || null;
}

/**
 * What every Today opens with: "Good morning, Liam". A person nobody names
 * (no member record, or a sign-in that gave no name) is greeted by the role
 * they are viewing as, as the screen always was, rather than by a guess.
 */
export function greetingTitle(
  first: string | null | undefined,
  roleLabel: string,
  now: Date = new Date(),
): string {
  return `${greetingWord(now)}, ${first || roleLabel}`;
}

/** The people a workstream's metadata names, by their directory key. */
export const PERSON_KEY_WORDS: Record<string, string> = {
  owner_id: "Owner",
  tpm_id: "TPM",
  sm_id: "Scrum master",
};

/** "stale_work_item" to "stale work item". */
export function spaced(snake: string): string {
  return snake.replace(/_/g, " ");
}

/** "stale work item" to "Stale work item". */
export function sentenceCase(text: string): string {
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text;
}

/*
 * Requests between people. The backend's kinds are `dependency`, `review` and
 * `input` (CrossPersonRequestKind); a kind it adds later still reads as words.
 */
const REQUEST_KIND_LABELS: Record<string, string> = {
  review: "Review",
  input: "Input",
  dependency: "Dependency",
};

/** A request's kind as a chip: "Review", "Input", "Dependency". */
export function requestKindLabel(kind: string): string {
  return REQUEST_KIND_LABELS[kind] ?? sentenceCase(spaced(kind));
}

/** What a request asks of the person it waits on, as one sentence about the requester. */
export function requestSentence(requester: string, kind: string): string {
  switch (kind) {
    case "review":
      return `${requester} asks you for a review`;
    case "input":
      return `${requester} asks for your input`;
    case "dependency":
      return `${requester} depends on you`;
    default:
      return `${requester} asks you for ${spaced(kind)}`;
  }
}

const REQUEST_STATUS_LABELS: Record<string, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  needs_resolution: "Needs resolution",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

/** Where a request has got to, as a word. */
export function requestStatusLabel(status: string): string {
  return REQUEST_STATUS_LABELS[status] ?? sentenceCase(spaced(status));
}

/** Whether the DM that tells the counterpart is a problem worth saying, and what to say. */
export function deliveryNote(
  delivery: string | null | undefined,
  raisedByYou: boolean,
): { text: string; bad: boolean } | null {
  if (delivery === "not_delivered") {
    return {
      text: raisedByYou ? "DM not delivered. Ask them directly." : "DM not delivered",
      bad: true,
    };
  }
  if (delivery === "retrying") return { text: "DM not sent yet, retrying", bad: false };
  if (delivery === "sent") return { text: "DM sent", bad: false };
  return null;
}

/**
 * What kind of thing a Today signal is, under its sentence. The sentences
 * (`title`) already name people and issues; this is only the category:
 * `blocker`, `unanswered`, `risk:stale_work_item`, `drift:merged_issue_open`.
 */
export function signalKindLabel(kind: string): string {
  const [head, rest] = kind.split(":");
  const detail = rest ? spaced(rest) : null;
  switch (head) {
    case "risk":
      return detail ? `Risk · ${detail}` : "Risk";
    case "drift":
      return detail ? `Drift · ${detail}` : "Drift";
    case "blocker":
      return "Blocker";
    case "blocked_task":
      return "Blocked task";
    case "unanswered":
      return "No reply to the check-in";
    case "partial":
      return "Check-in partly replied to";
    case "inferred":
      return "Status inferred, no reply";
    case "stale":
      return "Update carried forward";
    case "missing":
      return "No status reported";
    case "attention_task":
      return "Task needing attention";
    case "target_date":
      return "Target date";
    default:
      return sentenceCase(spaced(head));
  }
}

/** How long a signal has stood: "open 3d", or "new" for one that started today. */
export function signalAge(ageDays: number): string {
  return ageDays > 0 ? `open ${ageDays}d` : "new";
}

/**
 * The day's check-in count in the portfolio verdict. Nobody asked yet is not
 * "0 of 0 replied": the day's check-ins start at each person's own time.
 */
export function checkinsLine(
  checkins: { people: number; asked: number; answered: number },
  firstAsked: string | null,
  day = "today",
): string {
  if (checkins.asked === 0) {
    return checkins.people > 0
      ? `Check-ins ${day}: none asked yet · ${plural(checkins.people, "person", "people")} in teams`
      : `Check-ins ${day}: nobody is in a team yet`;
  }
  const base = `Check-ins ${day}: ${checkins.answered} of ${checkins.asked} replied`;
  return firstAsked ? `${base} · asked from ${firstAsked}` : base;
}
