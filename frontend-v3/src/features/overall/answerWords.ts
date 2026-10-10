// The answer line at the top of Overall: one sentence that says whether the date
// holds and why, in the verdict's own terms, for the project (or a release), for a
// scrum master's own pod, and, for a developer, where the gates stand. Pure, with
// runtime imports by their .ts path, so `node --test` runs it as written.
import type {
  GateBoardResponse,
  Rag,
  ScopeDeliveryResponse,
  TrackedQuestionResponse,
} from "../../api/schema";
import { counted, verdictChip, verdictRag } from "../../components/ui/dateStripWords.ts";
import { formatDay } from "../../lib/format.ts";
import { VERDICT_LABELS, type BadgeTone } from "../../lib/status.ts";
import { gateCounts, isOpenQuestion, plural } from "./gateWords.ts";
import { verdictCause } from "./overallWords.ts";

type Scope = Pick<
  ScopeDeliveryResponse,
  "verdict" | "target" | "history" | "team" | "total" | "open" | "target_source" | "commitment"
>;

/**
 * "Checkout Revamp is at risk because 6 open requirements have no ETA or due
 * date." The cause is the strip's own (`verdictCause`, the server's rule), moved
 * up to lead the page; a verdict that rule does not explain says only itself.
 */
export function scopeAnswer(scope: Scope, subject: string): string {
  if (!counted(scope)) {
    return `${subject} has no requirements counted yet, so there is nothing to forecast.`;
  }
  const label = VERDICT_LABELS[scope.verdict];
  const cause = verdictCause(scope);
  const after = (text: string) => text.slice(label.length).trimStart();
  switch (scope.verdict) {
    case "at_risk":
    case "off_track":
      return cause
        ? `${subject} is ${label.toLowerCase()} ${after(cause.because)}`
        : `${subject} is ${label.toLowerCase()}.`;
    case "not_enough_data":
      return cause
        ? `${subject}: ${label.toLowerCase()}${after(cause.because)}`
        : `${subject}: ${label.toLowerCase()} yet.`;
    case "no_date":
      return `${subject} has no committed date, so there is nothing to judge the forecast against.`;
    case "done":
      return `${subject} is done: every requirement is in production.`;
    case "on_track": {
      const { p85 } = scope.history;
      if (p85) {
        return `${subject} is on track: history is 85% likely to finish by ${formatDay(p85)}, by the delivery date.`;
      }
      const keyed = scope.team.latest_key ? ` (${scope.team.latest_key})` : "";
      return scope.team.latest
        ? `${subject} is on track: every open requirement has a date, the latest ${formatDay(scope.team.latest)}${keyed}, by the delivery date.`
        : `${subject} is on track.`;
    }
  }
}

/** The answer card's chip: the verdict, left out where the sentence starts with it in other words. */
export function answerChip(scope: Scope): { label: string; tone: BadgeTone } | null {
  if (!counted(scope) || scope.verdict === "no_date") return null;
  return verdictChip(scope);
}

/**
 * The card's edge: the verdict's colour, and red for a scope with work and no
 * committed date, since red is for what needs action.
 */
export function answerEdge(scope: Scope): Rag {
  if (counted(scope) && scope.verdict === "no_date") return "red";
  return verdictRag(scope.verdict, counted(scope));
}

/**
 * "5 of 18 requirements in production · 28% by count · the whole project". The
 * share is by story points when the requirements read has them (the Completion
 * card's rule), else by count from the same scope the sentence is about.
 */
export function answerMeta(
  scope: Pick<ScopeDeliveryResponse, "total" | "open">,
  scopeWords: string,
  points: { percent: number | null; hasPoints: boolean } | null,
): string {
  if (!counted(scope)) return scopeWords;
  const done = scope.total - scope.open;
  const share =
    points?.hasPoints && points.percent !== null
      ? `${Math.round(points.percent)}% by story points`
      : `${Math.round((done / scope.total) * 100)}% by count`;
  return `${done} of ${scope.total} ${scope.total === 1 ? "requirement" : "requirements"} in production · ${share} · ${scopeWords}`;
}

export type GateAnswer = {
  text: string;
  meta: string | null;
  /** Red when a requirement went around a gate or failed one, green when all passed. */
  edge: Rag;
};

/**
 * A developer's answer line: how the gates stand, the thing they sign off. "0 of
 * 18 requirements passed every gate, and 6 moved on without passing." Under it,
 * what waits on them: suggestions from Jira to keep or dismiss, and the open
 * questions asked of them (by member id, or by the name someone typed).
 */
export function gateAnswer(
  board: Pick<GateBoardResponse, "templates" | "issues" | "questions">,
  me: { id: string | null; name: string | null },
): GateAnswer {
  if (!board.templates.some((t) => t.enabled)) {
    return { text: "No gates are switched on for this project.", meta: null, edge: "unknown" };
  }
  if (board.issues.length === 0) {
    return { text: "No requirements in this scope yet.", meta: null, edge: "unknown" };
  }
  const counts = gateCounts(board);
  const failed = board.issues.some((issue) =>
    issue.evaluations.some((evaluation) => evaluation.state === "failed"),
  );
  const noun = counts.issues === 1 ? "requirement" : "requirements";
  const text =
    `${counts.passedAll} of ${counts.issues} ${noun} passed every gate` +
    (counts.passedWithout > 0 ? `, and ${counts.passedWithout} moved on without passing.` : ".");
  const mine = board.questions.filter(
    (q) => isOpenQuestion(q.status) && askedOf(q, me.id, me.name),
  ).length;
  const meta = [
    counts.suggestions > 0
      ? `${plural(counts.suggestions, "suggestion")} from Jira ${counts.suggestions === 1 ? "waits" : "wait"} to be kept or dismissed`
      : null,
    mine > 0 ? `${mine === 1 ? "1 question was" : `${mine} questions were`} asked of you` : null,
  ].filter((part): part is string => part !== null);
  return {
    text,
    meta: meta.length > 0 ? meta.join(" · ") : null,
    edge:
      failed || counts.passedWithout > 0
        ? "red"
        : counts.passedAll === counts.issues
          ? "green"
          : "unknown",
  };
}

function askedOf(
  question: Pick<TrackedQuestionResponse, "asked_to" | "asked_to_name">,
  id: string | null,
  name: string | null,
): boolean {
  const named = (value: string) =>
    Boolean(name) && value.trim().toLowerCase() === name?.toLowerCase();
  return (
    (Boolean(id) && question.asked_to === id) ||
    named(question.asked_to) ||
    named(question.asked_to_name)
  );
}
