// An investigated answer as the stream builds it (POST /ask/investigate): the
// plan's steps, each step as it finishes, then the answer or why there is none.
// Pure, with type imports and pure modules by their .ts path, so `node --test`
// runs it as written.
import type {
  AskSourceResponse,
  InvestigateEvent,
  InvestigateStepResponse,
} from "../../api/schema";
import { spaced } from "../../lib/words.ts";

/** How a question is asked: one quick look-up (POST /ask), or an investigation in steps. */
export type AskMode = "quick" | "investigate";

/** The parts of an assistant message the stream changes. */
export type Answering = {
  status: "pending" | "answered" | "failed";
  text: string;
  sources: AskSourceResponse[];
  steps: InvestigateStepResponse[];
  followUps: string[];
};

/** Said when the stream ends with neither an answer nor a reason. */
export const STOPPED_EARLY = "The investigation stopped before it answered. Please ask again.";

export const answering = (): Answering => ({
  status: "pending",
  text: "",
  sources: [],
  steps: [],
  followUps: [],
});

/** What one line of the stream changes. A step replaces the planned step with its index. */
export function applyEvent(current: Answering, event: InvestigateEvent): Answering {
  switch (event.type) {
    case "plan":
      return { ...current, steps: event.steps };
    case "step":
      return {
        ...current,
        steps: current.steps.map((step) => (step.index === event.step.index ? event.step : step)),
      };
    case "answer":
      return {
        status: "answered",
        text: event.answer.answer,
        sources: event.answer.sources ?? [],
        steps: event.steps,
        followUps: event.answer.follow_ups ?? [],
      };
    case "failed":
      return {
        status: "failed",
        text: event.message,
        sources: [],
        steps: event.steps.length > 0 ? event.steps : current.steps,
        followUps: [],
      };
  }
}

/** The answer once the stream has ended: still pending means it stopped early. */
export function streamEnded(current: Answering): Answering {
  return current.status === "pending"
    ? { ...current, status: "failed", text: STOPPED_EARLY }
    : current;
}

/** "open risks · status reasons": the tools a step read with, in words. */
export function toolWords(tools: string[]): string {
  return tools.map(spaced).join(" · ");
}
