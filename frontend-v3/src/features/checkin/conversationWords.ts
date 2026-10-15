// Pure words for the person's own conversation on Check-in. Type imports
// and extensioned imports only, so `node --test` runs it.
import type { ConversationResponse, ConversationTurnDto } from "../../api/schema";
import { formatDay } from "../../lib/format.ts";

/** Who wrote a turn, as the bubble says it: Ora, or "You". */
export function turnAuthor(turn: Pick<ConversationTurnDto, "role">): string {
  return turn.role === "user" ? "You" : "Ora";
}

/** The turns, oldest first, as the server sends them; a stable order for equal times. */
export function orderedTurns(turns: readonly ConversationTurnDto[]): ConversationTurnDto[] {
  return turns
    .map((turn, index) => ({ turn, index }))
    .sort((a, b) => a.turn.at.localeCompare(b.turn.at) || a.index - b.index)
    .map(({ turn }) => turn);
}

/**
 * What the column says when there is nothing to show: past the retention
 * window it is not kept; otherwise nothing was said that day yet.
 */
export function emptyConversationWords(
  conversation: Pick<ConversationResponse, "kept" | "note" | "retention_days" | "day">,
  isToday: boolean,
): string {
  if (!conversation.kept) {
    return conversation.note ?? `Not kept after ${conversation.retention_days} days.`;
  }
  return isToday
    ? "Nothing yet today. Ora asks in your chat at the team's check-in time."
    : `Nothing was said on ${formatDay(conversation.day)}.`;
}
