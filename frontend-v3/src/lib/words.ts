// Pure wording helpers, type imports only so `node --test` can run them.
import type { StatusSource } from "../api/schema";

const SOURCE_WORDS: Record<StatusSource, string> = {
  confirmed: "confirmed",
  partial: "partly answered",
  inferred: "inferred",
  stale: "stale",
  unknown: "unknown",
};

/** Where a status came from, with confidence when the server gives one. */
export function sourceLine(source: StatusSource | null | undefined, confidence?: number | null) {
  const word = source ? SOURCE_WORDS[source] : "no status";
  return confidence === null || confidence === undefined
    ? word
    : `${word} · ${Math.round(confidence * 100)}% confidence`;
}

/** Today's date and the program, the way every Today opens. */
export function todayEyebrow(programName: string | null | undefined): string {
  const day = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  return programName ? `${day} · ${programName}` : day;
}

export function greetingWord(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** The people a workstream's metadata names, by their directory key. */
export const PERSON_KEY_WORDS: Record<string, string> = {
  owner_id: "Owner",
  tpm_id: "TPM",
  sm_id: "Scrum master",
};
