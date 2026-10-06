// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type { AppRole } from "../../app/role";
import type { BadgeTone } from "../../lib/status";
import type {
  DeliveryStage,
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
  Role,
  TrackedQuestionResponse,
} from "../../api/schema";

export const GATE_STATE_LABELS: Record<GateState, string> = {
  passed: "Passed",
  open: "Open",
  failed: "Failed",
  missing: "Nothing confirmed",
};

export const GATE_STATE_TONES: Record<GateState, BadgeTone> = {
  passed: "success",
  open: "info",
  failed: "danger",
  missing: "neutral",
};

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
  description: "From the description",
  comment: "From a comment",
  manual: "Added by hand",
};

export const QUESTION_STATUS_LABELS: Record<QuestionStatus, string> = {
  not_yet: "Not yet",
  partly: "Partly",
  answered: "Answered",
  closed_unanswered: "Closed unanswered",
};

export const QUESTION_STATUS_TONES: Record<QuestionStatus, BadgeTone> = {
  not_yet: "warning",
  partly: "info",
  answered: "success",
  closed_unanswered: "danger",
};

export const QUESTION_STATUSES: QuestionStatus[] = [
  "not_yet",
  "partly",
  "answered",
  "closed_unanswered",
];

export const ROLE_NAMES: Record<Role, string> = {
  dev: "Developer",
  sm: "Scrum master",
  po: "Product owner",
  mgr: "Manager",
  exec: "Executive",
  admin: "Admin",
};

/** The roles a template may name as signing a kind off; an admin always may. */
export const SIGN_OFF_ROLES: Role[] = ["dev", "sm", "po", "mgr"];

export function boardKey(projectId: string, asOf: string, releaseId: string) {
  return ["persona", "gates", projectId, asOf, releaseId] as const;
}

export function evaluationFor(
  issue: IssueGatesResponse,
  templateId: string,
): GateEvaluationResponse | undefined {
  return issue.evaluations.find((evaluation) => evaluation.template_id === templateId);
}

/** "2 of 3 met", "1 suggestion", or the state's label when nothing is counted. */
export function evaluationLabel(evaluation: GateEvaluationResponse): string {
  if (evaluation.total > 0) return `${evaluation.met} of ${evaluation.total} met`;
  if (evaluation.suggested > 0) return plural(evaluation.suggested, "suggestion");
  return GATE_STATE_LABELS[evaluation.state];
}

export type GateSummary = {
  issues: number;
  passedAll: number;
  passedWithout: number;
  suggestions: number;
  openQuestions: number;
  questionsToConfirm: number;
};

export function gateSummary(board: GateBoardResponse): GateSummary {
  const live = board.questions.filter((question) => question.confirmed);
  return {
    issues: board.issues.length,
    passedAll: board.issues.filter(
      (issue) =>
        issue.evaluations.length > 0 &&
        issue.evaluations.every((evaluation) => evaluation.state === "passed"),
    ).length,
    passedWithout: board.issues.filter((issue) => issue.passed_without.length > 0).length,
    suggestions: board.issues.reduce(
      (sum, issue) => sum + issue.items.filter((item) => item.status === "suggested").length,
      0,
    ),
    openQuestions: live.filter((question) => isOpen(question.status)).length,
    questionsToConfirm: board.questions.filter((question) => !question.confirmed).length,
  };
}

/** Whether an issue needs someone: past a gate it has not passed, failed, or with suggestions. */
export function needsAttention(issue: IssueGatesResponse): boolean {
  return (
    issue.passed_without.length > 0 ||
    issue.items.some((item) => item.status === "suggested") ||
    issue.evaluations.some((evaluation) => evaluation.state === "failed")
  );
}

/** Issues past a gate first, then failed, then with suggestions; then by key. */
export function issueRows(
  board: GateBoardResponse,
  filter: "attention" | "all",
): IssueGatesResponse[] {
  const rows = filter === "attention" ? board.issues.filter(needsAttention) : [...board.issues];
  return rows.sort((a, b) => urgency(b) - urgency(a) || a.key.localeCompare(b.key));
}

function urgency(issue: IssueGatesResponse): number {
  if (issue.passed_without.length > 0) return 3;
  if (issue.evaluations.some((evaluation) => evaluation.state === "failed")) return 2;
  if (issue.items.some((item) => item.status === "suggested")) return 1;
  return 0;
}

/** "CHK-2 reached Production without Business acceptance passing." */
export function passedWithoutLine(issue: IssueGatesResponse, stageLabel: string): string {
  return `${issue.key} reached ${stageLabel} without ${joinNames(issue.passed_without)} passing.`;
}

export type KindItems = {
  kind: ItemKindDto;
  suggested: GateItemResponse[];
  confirmed: GateItemResponse[];
};

/** One issue's items under one template, per kind, suggestions apart from confirmed ones. */
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

/**
 * Whether the lens reads a project's gate board. Mirrors the board's guard on
 * the server: reading the project's progress, or working its gates. So a
 * developer or scrum master, who signs test cases off, reads the board even
 * though the rest of the project's progress is not theirs to read.
 */
export function mayReadGateBoard(role: {
  canReadProjectProgress: boolean;
  canEditGates: boolean;
}): boolean {
  return role.canReadProjectProgress || role.canEditGates;
}

/** Whether the lens may mark this kind met, failed or waived. */
export function maySignOff(kind: ItemKindDto, roles: AppRole[]): boolean {
  return roles.includes("admin") || kind.sign_off_roles.some((role) => roles.includes(role));
}

/** "Signed off by product owner or manager." */
export function signOffLine(kind: ItemKindDto): string {
  const names = kind.sign_off_roles.map((role) => ROLE_NAMES[role].toLowerCase());
  const who = names.length === 0 ? "nobody yet" : joinNames(names, "or");
  return `Signed off by ${who}${kind.evidence_required ? ", with a link to the evidence" : ""}.`;
}

/** An evidence link the server will take, or a sentence saying why not. */
export function evidenceProblem(url: string, required: boolean, status: ItemStatus): string | null {
  const clean = url.trim();
  if (!clean) {
    return required && status === "met" ? "Add a link to the evidence to mark this met." : null;
  }
  return /^https?:\/\//.test(clean) ? null : "The evidence is a link starting with https://.";
}

export function isOpen(status: QuestionStatus): boolean {
  return status === "not_yet" || status === "partly";
}

/** Questions to confirm first, then open ones longest-waiting first, then the rest. */
export function questionRows(
  questions: TrackedQuestionResponse[],
  showClosed: boolean,
): TrackedQuestionResponse[] {
  const rank = (question: TrackedQuestionResponse) =>
    !question.confirmed ? 0 : isOpen(question.status) ? 1 : 2;
  return questions
    .filter((question) => showClosed || !question.confirmed || isOpen(question.status))
    .sort(
      (a, b) =>
        rank(a) - rank(b) ||
        a.asked_at.localeCompare(b.asked_at) ||
        a.issue_key.localeCompare(b.issue_key),
    );
}

/** Whole days between when a question was asked and the day viewed. */
export function daysWaiting(askedAt: string, asOf: string): number {
  const asked = Date.UTC(
    Number(askedAt.slice(0, 4)),
    Number(askedAt.slice(5, 7)) - 1,
    Number(askedAt.slice(8, 10)),
  );
  const viewed = Date.UTC(
    Number(asOf.slice(0, 4)),
    Number(asOf.slice(5, 7)) - 1,
    Number(asOf.slice(8, 10)),
  );
  return Math.max(0, Math.round((viewed - asked) / 86_400_000));
}

export function waitingLabel(days: number): string {
  if (days === 0) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

/** Who asked, by name when the board knows it. */
export function askedByName(question: TrackedQuestionResponse, names: Record<string, string>) {
  return question.asked_by_name || names[question.asked_by] || question.asked_by;
}

/** "Read 3 issues: 4 new suggestions, 1 question. 2 had not changed." */
export function scanSummary(scan: GateScanResponse): string {
  if (scan.read === 0 && scan.failed === 0) {
    return scan.unchanged > 0
      ? `Nothing changed in Jira since the last read (${plural(scan.unchanged, "issue")}).`
      : "There are no requirements to read.";
  }
  const found = [
    plural(scan.suggested_items, "new suggestion"),
    plural(scan.questions, "question"),
  ];
  const parts = [`Read ${plural(scan.read, "issue")}: ${found.join(", ")}.`];
  if (scan.unchanged > 0) parts.push(`${plural(scan.unchanged, "issue")} had not changed.`);
  if (scan.failed > 0) parts.push(`${plural(scan.failed, "issue")} could not be read.`);
  return parts.join(" ");
}

// ---- The template editor -------------------------------------------------------------

export type KindDraft = {
  key: string;
  label: string;
  signOffRoles: Role[];
  evidenceRequired: boolean;
  headingsText: string;
  gherkin: boolean;
  /** A kind added in this edit; its key follows its label until saved. */
  isNew: boolean;
};

export type TemplateDraft = {
  templateId: string;
  name: string;
  guardsStage: DeliveryStage;
  issueTypesText: string;
  enabled: boolean;
  kinds: KindDraft[];
};

export const MAX_KINDS = 10;

export function draftFromTemplate(template: GateTemplateDto): TemplateDraft {
  return {
    templateId: template.template_id ?? "",
    name: template.name,
    guardsStage: template.guards_stage,
    issueTypesText: (template.issue_types ?? []).join(", "),
    enabled: template.enabled ?? true,
    kinds: template.kinds.map((kind) => ({
      key: kind.key,
      label: kind.label,
      signOffRoles: [...kind.sign_off_roles],
      evidenceRequired: kind.evidence_required ?? false,
      headingsText: (kind.headings ?? []).join(", "),
      gherkin: kind.gherkin ?? false,
      isNew: false,
    })),
  };
}

export function newTemplateDraft(): TemplateDraft {
  return {
    templateId: "",
    name: "",
    guardsStage: "production",
    issueTypesText: "",
    enabled: true,
    kinds: [newKindDraft()],
  };
}

export function newKindDraft(): KindDraft {
  return {
    key: "",
    label: "",
    signOffRoles: ["po"],
    evidenceRequired: false,
    headingsText: "",
    gherkin: false,
    isNew: true,
  };
}

/** "Security review" -> "security_review". */
export function kindKey(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

export function withLabel(kind: KindDraft, label: string): KindDraft {
  return { ...kind, label, key: kind.isNew ? kindKey(label) : kind.key };
}

export function toggleRole(kind: KindDraft, role: Role): KindDraft {
  const signOffRoles = kind.signOffRoles.includes(role)
    ? kind.signOffRoles.filter((item) => item !== role)
    : [...kind.signOffRoles, role];
  return { ...kind, signOffRoles };
}

export function splitList(text: string): string[] {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const part of text.split(/[,\n]/)) {
    const clean = part.trim().replace(/\s+/g, " ");
    if (clean && !seen.has(clean.toLowerCase())) {
      seen.add(clean.toLowerCase());
      kept.push(clean);
    }
  }
  return kept;
}

/** What the server would refuse, said the same way, so the form says it first. */
export function draftProblems(draft: TemplateDraft): string[] {
  const problems: string[] = [];
  if (!draft.name.trim()) problems.push("Give the gate a name.");
  if (draft.kinds.length === 0) problems.push("A gate needs at least one kind of item.");
  if (draft.kinds.length > MAX_KINDS) problems.push(`A gate has at most ${MAX_KINDS} kinds.`);
  const keys = new Set<string>();
  draft.kinds.forEach((kind, index) => {
    const name = kind.label.trim() || `Kind ${index + 1}`;
    if (!kind.label.trim() || !kind.key) problems.push(`${name} needs a label.`);
    else if (keys.has(kind.key)) problems.push(`Two kinds are both called "${kind.label}".`);
    keys.add(kind.key);
    if (kind.signOffRoles.length === 0) problems.push(`Say who signs off ${name}.`);
  });
  return problems;
}

export function templateFromDraft(draft: TemplateDraft): GateTemplateDto {
  return {
    template_id: draft.templateId,
    name: draft.name.trim(),
    guards_stage: draft.guardsStage,
    issue_types: splitList(draft.issueTypesText),
    enabled: draft.enabled,
    kinds: draft.kinds.map((kind) => ({
      key: kind.key,
      label: kind.label.trim(),
      sign_off_roles: kind.signOffRoles,
      evidence_required: kind.evidenceRequired,
      headings: splitList(kind.headingsText),
      gherkin: kind.gherkin,
    })),
  };
}

/** "an acceptance criterion", "a test case". */
export function withArticle(label: string): string {
  const lower = label.toLowerCase();
  return `${/^[aeiou]/.test(lower) ? "an" : "a"} ${lower}`;
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** "A", "A and B", "A, B and C". */
export function joinNames(names: string[], conjunction = "and"): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} ${conjunction} ${names[names.length - 1]}`;
}
