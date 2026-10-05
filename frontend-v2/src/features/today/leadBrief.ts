import type { NarrativeBriefResponse } from "../../api/schema";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

/** A brief as Exec Today shows it: a verdict, when it has one, then short bullets. */
export type BriefParts = { verdict: string | null; bullets: string[] };

type BriefText = Pick<NarrativeBriefResponse, "body"> &
  Partial<Pick<NarrativeBriefResponse, "verdict" | "bullets">>;

// A sentence ends at . ! or ? followed by a space and a capital, digit, quote
// or bracket -- the break brief_grounding.py reads sentences by.
const SENTENCE_BREAK = /(?<=[.!?])\s+(?=[A-Z0-9"'(])/;
const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;

/**
 * The verdict and bullets of a brief.
 *
 * A structured brief carries both. An older one is one free-text paragraph,
 * so it is split into its sentences, each a bullet, with no verdict: a
 * paragraph's first sentence is not a verdict just because it comes first.
 */
export function briefParts(brief: BriefText): BriefParts {
  const bullets = (brief.bullets ?? []).map((bullet) => bullet.trim()).filter(Boolean);
  if (brief.verdict && bullets.length > 0) {
    return { verdict: brief.verdict.trim(), bullets };
  }
  const sentences = brief.body
    .split(/\n+/)
    .flatMap((line) => line.split(SENTENCE_BREAK))
    .map((sentence) => sentence.replace(BULLET, "").trim())
    .filter(Boolean);
  return { verdict: null, bullets: sentences };
}

/**
 * When a brief was written, in words: "Sunday 4 October, 19:46".
 *
 * The year is said only when it is not this year. `timeZone` is for tests;
 * the console shows the reader's own clock.
 */
export function briefDateLabel(
  generatedAt: string,
  options: { now?: Date; timeZone?: string } = {},
): string {
  const at = new Date(generatedAt);
  if (Number.isNaN(at.getTime())) return "";
  const now = options.now ?? new Date();
  const zone = options.timeZone ? { timeZone: options.timeZone } : {};
  const year = new Intl.DateTimeFormat("en-GB", { year: "numeric", ...zone });
  const sameYear = year.format(at) === year.format(now);
  const day = new Intl.DateTimeFormat("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    ...(sameYear ? {} : { year: "numeric" }),
    ...zone,
  }).format(at);
  const time = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    ...zone,
  }).format(at);
  return `${day.replace(/,/g, "")}, ${time}`;
}
