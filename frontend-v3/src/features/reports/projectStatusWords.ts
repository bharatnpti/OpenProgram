// The one status a project's card on Reports carries. Pure, with runtime imports
// by their .ts path, so `node --test` runs it as written.
import type { Rag, ScopeDeliveryResponse } from "../../api/schema";
import { compactParts } from "../../components/ui/dateStripWords.ts";
import { ragWords, toneForRag, type BadgeTone } from "../../lib/status.ts";

export type ProjectStatus = { label: string; tone: BadgeTone };

/**
 * One status for a project's card, never two that can disagree. Where the card
 * shows the project's delivery (`delivery`, its read), that is the verdict's own
 * words and colour; a verdict the date line already says (no committed date) is
 * not said twice, so there is no chip and the line is the status. Where it does
 * not, the project's colour in words: "At risk", never "amber". Null while the
 * delivery is still being read, so the card does not show a colour it may then
 * contradict.
 */
export function projectStatus(
  delivery: { scope: ScopeDeliveryResponse | undefined; reading: boolean } | null,
  rag: Rag | null | undefined,
): ProjectStatus | null {
  if (delivery?.scope) return compactParts(delivery.scope).chip;
  if (delivery?.reading) return null;
  return { label: ragWords(rag), tone: toneForRag(rag) };
}
