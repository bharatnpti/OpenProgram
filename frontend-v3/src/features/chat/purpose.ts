// What a bot message is for, in words. Type imports only, so `node --test` can run it.
import type { ChatSimulatorMessageResponse } from "../../api/schema";

/** The purpose the backend gives a DM that asks a person for a review or input. */
export const REQUEST_PURPOSE = "cross_person_request";

/**
 * The purposes the backend sends with a chat message (metadata `purpose` in
 * core/application/status_collector.py and cross_person_service.py, and the
 * chat adapter's own post). A purpose not listed here reads as its own words.
 */
export const PURPOSE_WORDS: Record<string, string> = {
  status_checkin: "check-in",
  status_clarification: "follow-up question",
  status_nudge: "reminder",
  status_escalation: "missed check-in notice",
  status_ack: "update recorded",
  status_non_status_ack: "waiting for your update",
  status_late_update_ack: "late update recorded",
  writeback_consent_prompt: "permission to update your tracker",
  cross_person_request: "request",
  cross_person_request_acknowledged: "request acknowledged",
  cross_person_request_resolved: "request resolved",
  manual_post: "posted by hand",
};

/**
 * The label under a message, or null for a plain reply. A day report's DM has
 * no purpose: the report sender marks it with `kind: day_report` instead.
 */
export function purposeWords(
  message: Pick<ChatSimulatorMessageResponse, "purpose" | "metadata">,
): string | null {
  if (message.purpose) {
    return PURPOSE_WORDS[message.purpose] ?? message.purpose.replace(/_/g, " ");
  }
  return message.metadata?.kind === "day_report" ? "day report" : null;
}
