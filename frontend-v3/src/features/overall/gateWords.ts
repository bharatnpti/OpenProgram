// Pure wording and grouping for gates and questions, ported from frontend-v2's
// features/gates/gates.ts. Type imports only, so `node --test` runs it directly.
import type {
  GateBoardResponse,
  GateEvaluationResponse,
  GateItemResponse,
  GateScanResponse,
  GateState,
  GateTemplateDto,
  IssueGatesResponse,
  ItemKindDto,
  ItemSource,
  ItemStatus,
  QuestionStatus,
  TrackedQuestionResponse,
} from "../../api/schema";
import type { BadgeTone } from "../../lib/status";

export const ITEM_STATUS_LABELS: Record<ItemStatus, string> = {
  suggested: "Suggested",
  dismissed: "Dismissed",
  pending: "To check",
  met: "Met",
  failed: "Failed",
  waived: "Waived",
};

export const ITEM_STATUS_TONES: Record<ItemStatus, BadgeTone> = {
  suggested: "neutral",
  dismissed: "neutral",
  pending: "info",
  met: "success",
  failed: "danger",
  waived: "warning",
};

export const SOURCE_LABELS: Record<ItemSource, string> = {
  description: "Read from the Jira description",
  comment: "Read from a Jira comment",
  manual: "Added by hand",
};

export const GATE_STATE_TONES: Record<GateState, BadgeTone> = {
  passed: "success",
  open: "warning",
  failed: "danger",
  missing: "neutral",
};

/** The statuses a sign-off sets, and "back to check" to reopen one. */
export const SIGN_OFF_CHOICES: ItemStatus[] = ["met", "failed", "waived", "pending"];

export const QUESTION_STATUSES: QuestionStatus[] = [
  "not_yet",
  "partly",
  "answered",
  "closed_unanswered",
];

export const QUESTION_LABELS: Record<QuestionStatus, string> = {
  not_yet: "Not yet",
  partly: "Partly",
  answered: "Yes",
  closed_unanswered: "Closed without an answer",
};

export const QUESTION_TONES: Record<QuestionStatus, BadgeTone> = {
  not_yet: "warning",
  partly: "info",
  answered: "success",
  closed_unanswered: "neutral",
};

/** A gate's state for one requirement; "missing" splits into nothing yet and part. */
export function evaluationChip(evaluation: GateEvaluationResponse): string {
  if (evaluation.state === "passed") return "Passed";
  if (evaluation.state === "failed") return "Failed";
  if (evaluation.state === "open") return "Open";
  return evaluation.total === 0 ? "Not started" : "Incomplete";
}

/** "2 of 3 met · 1 suggested from Jira · no test case kept yet", in the gate's own labels. */
export function evaluationLine(
  evaluation: GateEvaluationResponse,
  template: Pick<GateTemplateDto, "kinds">,
): string {
  const parts: string[] = [];
  if (evaluation.total > 0) parts.push(`${evaluation.met} of ${evaluation.total} met`);
  if (evaluation.suggested > 0) {
    parts.push(`${evaluation.suggested} suggested from Jira to keep or dismiss`);
  }
  for (const key of evaluation.missing_kinds) {
    const label = template.kinds.find((kind) => kind.key === key)?.label ?? key;
    parts.push(`no ${label.toLowerCase()} kept yet`);
  }
  const line = parts.join(" · ");
  return line ? line[0].toUpperCase() + line.slice(1) : "";
}

export type KindItems = {
  kind: ItemKindDto;
  suggested: GateItemResponse[];
  confirmed: GateItemResponse[];
};

/** One issue's items under one gate, per kind, suggestions apart from kept ones. */
export function itemsByKind(issue: IssueGatesResponse, template: GateTemplateDto): KindItems[] {
  const mine = issue.items.filter((item) => item.template_id === template.template_id);
  return template.kinds.map((kind) => ({
    kind,
    suggested: mine.filter((item) => item.kind === kind.key && item.status === "suggested"),
    confirmed: mine.filter(
      (item) =>
        item.kind === kind.key && item.status !== "suggested" && item.status !== "dismissed",
    ),
  }));
}

/** An evidence link the server will take, or a sentence saying why not. */
export function evidenceProblem(url: string, required: boolean, status: ItemStatus): string | null {
  const clean = url.trim();
  if (!clean) {
    return required && status === "met" ? "Add a link to the evidence to mark this met." : null;
  }
  return /^https?:\/\//.test(clean) ? null : "The evidence is a link starting with https://.";
}

/** "Read 3 issues: 4 new suggestions, 1 question. 2 had not changed." */
export function scanSummary(scan: GateScanResponse): string {
  if (scan.read === 0 && scan.failed === 0) {
    return scan.unchanged > 0
      ? `Nothing changed in Jira since the last read (${plural(scan.unchanged, "issue")}).`
      : "There are no requirements to read.";
  }
  const parts: string[] = [];
  if (scan.read > 0) {
    parts.push(
      `Read ${plural(scan.read, "issue")}: ${plural(scan.suggested_items, "new suggestion")}, ${plural(scan.questions, "question")}.`,
    );
  }
  if (scan.unchanged > 0) parts.push(`${plural(scan.unchanged, "issue")} had not changed.`);
  if (scan.failed > 0) {
    parts.push(`${plural(scan.failed, "issue")} could not be read from Jira.`);
  }
  return parts.join(" ");
}

/** Whether an issue needs someone: past a gate it has not passed, failed, or with suggestions. */
export function needsSomeone(issue: IssueGatesResponse): boolean {
  return (
    issue.passed_without.length > 0 ||
    issue.items.some((item) => item.status === "suggested") ||
    issue.evaluations.some((evaluation) => evaluation.state === "failed")
  );
}

export type GateCounts = {
  issues: number;
  passedAll: number;
  passedWithout: number;
  suggestions: number;
};

export function gateCounts(board: Pick<GateBoardResponse, "issues">): GateCounts {
  return {
    issues: board.issues.length,
    passedAll: board.issues.filter(
      (issue) =>
        issue.evaluations.length > 0 && issue.evaluations.every((e) => e.state === "passed"),
    ).length,
    passedWithout: board.issues.filter((issue) => issue.passed_without.length > 0).length,
    suggestions: board.issues.reduce(
      (sum, issue) => sum + issue.items.filter((item) => item.status === "suggested").length,
      0,
    ),
  };
}

export function isOpenQuestion(status: QuestionStatus): boolean {
  return status === "not_yet" || status === "partly";
}

/** Questions to keep or dismiss first, then open ones longest waiting, then the rest, newest first. */
export function questionRows(questions: TrackedQuestionResponse[]): TrackedQuestionResponse[] {
  const rank = (q: TrackedQuestionResponse) =>
    !q.confirmed ? 0 : isOpenQuestion(q.status) ? 1 : 2;
  return [...questions].sort((a, b) => {
    const byRank = rank(a) - rank(b);
    if (byRank !== 0) return byRank;
    // Open questions wait longest first; answered and closed ones read newest first.
    return rank(a) === 2
      ? b.asked_at.localeCompare(a.asked_at)
      : a.asked_at.localeCompare(b.asked_at);
  });
}

/** What the form would be refused for, said first. */
export function questionProblem(askedTo: string, summary: string): string | null {
  if (!askedTo.trim()) return "Say who has to answer.";
  if (!summary.trim()) return "Write what was asked.";
  return null;
}

/** "an acceptance criterion", "a test case". */
export function withArticle(label: string): string {
  const lower = label.toLowerCase();
  return `${/^[aeiou]/.test(lower) ? "an" : "a"} ${lower}`;
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
