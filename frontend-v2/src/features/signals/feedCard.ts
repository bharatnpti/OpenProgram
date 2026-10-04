import type { PortfolioFeedItemResponse } from "../../api/schema";
import type { SignalCardData } from "./SignalCard";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

/** Who a person is when the server sends no name for them: never their chat id. */
export const UNKNOWN_PERSON = "a team member";

/** What a signal is about: its entity, and the name the server gives a person. */
export type SignalSubject = {
  entity_ref: { kind: string; id: string };
  person_name?: string | null;
};

/**
 * The line under a signal's title that says what it is about.
 *
 * A person reads as the name the server resolved for them. N37: Signals
 * labelled people "developer · U0123ABCD", their chat id. An older server sends
 * no name, and the card then says "a team member" rather than the id. Anything
 * else keeps its kind and id: an issue key is the one id that reads as a name.
 */
export function entityLabel(subject: SignalSubject): string {
  const name = subject.person_name?.trim();
  if (name) return name;
  if (subject.entity_ref.kind === "developer") return UNKNOWN_PERSON;
  return `${subject.entity_ref.kind} · ${subject.entity_ref.id}`;
}

/** One portfolio feed item as a Signals card. */
export function feedCard(item: PortfolioFeedItemResponse, index: number): SignalCardData {
  return {
    id: `feed-${item.entity_ref.id}-${item.observed_at}-${index}`,
    tone: "info",
    title: item.summary,
    category: "feed",
    entityLabel: entityLabel(item),
    ageLabel: new Date(item.observed_at).toLocaleDateString(),
    signalsSay: `${item.source} · ${item.kind}`,
    animateDelay: Math.min(index * 70, 420),
  };
}
