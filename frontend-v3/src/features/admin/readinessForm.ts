// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DeliveryStage,
  ReadinessAppliesTo,
  ReadinessCriterionDto,
  ReadinessMatcherKind,
  ReadinessSettingsDto,
  ReadinessStrength,
} from "../../api/schema";

/*
 * The release criterion editor. Limits and wording mirror
 * core/domain/release_readiness.py (validated_criterion, validated_settings), so
 * the form says what the server would refuse before it is sent.
 */

export const MAX_NAME = 80;
export const MAX_EVIDENCE = 600;
export const MAX_MATCHERS = 12;
export const MAX_VALUE = 80;
export const MAX_LEAD = 60;
export const MAX_LABELS = 10;

/** The words a draft's summary and text may fill in; nothing else, and never Jira text. */
export const PLACEHOLDERS = [
  "scope",
  "scope_kind",
  "project",
  "project_key",
  "release",
  "pod",
  "criterion",
  "evidence",
  "stage",
  "due_date",
  "delivery_date",
  "working_days",
  "console_link",
  "creator",
] as const;

export const MATCHER_LABELS: Record<ReadinessMatcherKind, string> = {
  label: "Label",
  issue_type: "Issue type",
  title_phrase: "Title phrase",
  title_words: "Title words",
  epic: "Epic",
};

/** A label, a type or a whole phrase is strong evidence; loose words or an epic only a hint. */
export const DEFAULT_STRENGTH: Record<ReadinessMatcherKind, ReadinessStrength> = {
  label: "evidence",
  issue_type: "evidence",
  title_phrase: "evidence",
  title_words: "candidate",
  epic: "candidate",
};

export const APPLIES_LABELS: Record<ReadinessAppliesTo, string> = {
  release: "Each release, or the project while it has none",
  project: "Each project",
  pod: "Each pod",
};

export type MatcherDraft = {
  kind: ReadinessMatcherKind;
  value: string;
  strength: ReadinessStrength;
};

export type CriterionDraft = {
  criterionId: string;
  name: string;
  evidence: string;
  appliesTo: ReadinessAppliesTo;
  requiredBefore: DeliveryStage;
  leadText: string;
  blocking: boolean;
  needsDone: boolean;
  whenLabelsText: string;
  whenTypesText: string;
  matchers: MatcherDraft[];
  projectKey: string;
  issueType: string;
  summary: string;
  description: string;
  labelsText: string;
  enabled: boolean;
};

export function newCriterionDraft(): CriterionDraft {
  return {
    criterionId: "",
    name: "",
    evidence: "",
    appliesTo: "release",
    requiredBefore: "production",
    leadText: "10",
    blocking: true,
    needsDone: true,
    whenLabelsText: "",
    whenTypesText: "",
    matchers: [newMatcher("label")],
    projectKey: "",
    issueType: "",
    summary: "{criterion} for {scope}",
    description: "",
    labelsText: "",
    enabled: true,
  };
}

export function newMatcher(kind: ReadinessMatcherKind = "title_phrase"): MatcherDraft {
  return { kind, value: "", strength: DEFAULT_STRENGTH[kind] };
}

export function draftFromCriterion(criterion: ReadinessCriterionDto): CriterionDraft {
  return {
    criterionId: criterion.criterion_id ?? "",
    name: criterion.name,
    evidence: criterion.evidence,
    appliesTo: criterion.applies_to,
    requiredBefore: criterion.required_before ?? "production",
    leadText: String(criterion.lead_working_days ?? 10),
    blocking: (criterion.severity ?? "blocking") === "blocking",
    needsDone: criterion.needs_done ?? true,
    whenLabelsText: (criterion.when_labels ?? []).join(", "),
    whenTypesText: (criterion.when_types ?? []).join(", "),
    matchers: criterion.matchers.map((item) => ({
      kind: item.kind,
      value: item.value,
      strength: item.strength,
    })),
    projectKey: criterion.draft?.project_key ?? "",
    issueType: criterion.draft?.issue_type ?? "",
    summary: criterion.draft?.summary ?? "{criterion} for {scope}",
    description: criterion.draft?.description ?? "",
    labelsText: (criterion.draft?.labels ?? []).join(", "),
    enabled: criterion.enabled ?? true,
  };
}

export function criterionFromDraft(draft: CriterionDraft): ReadinessCriterionDto {
  return {
    criterion_id: draft.criterionId,
    version: 0,
    name: draft.name.trim().replace(/\s+/g, " "),
    evidence: draft.evidence.trim().replace(/\s+/g, " "),
    applies_to: draft.appliesTo,
    required_before: draft.requiredBefore,
    lead_working_days: Number.parseInt(draft.leadText, 10),
    severity: draft.blocking ? "blocking" : "advisory",
    needs_done: draft.needsDone,
    when_labels: list(draft.whenLabelsText),
    when_types: list(draft.whenTypesText),
    matchers: draft.matchers
      .filter((item) => item.value.trim())
      .map((item) => ({ kind: item.kind, value: item.value.trim(), strength: item.strength })),
    draft: {
      project_key: draft.projectKey.trim().toUpperCase(),
      issue_type: draft.issueType.trim(),
      summary: draft.summary.trim() || "{criterion} for {scope}",
      description: draft.description.trim(),
      labels: list(draft.labelsText),
    },
    enabled: draft.enabled,
  };
}

/** What stops a save, in the server's words where it has them. */
export function draftProblems(draft: CriterionDraft): string[] {
  const problems: string[] = [];
  const name = draft.name.trim();
  if (!name || name.length > MAX_NAME)
    problems.push(`Give it a name of 1 to ${MAX_NAME} characters.`);
  const evidence = draft.evidence.trim();
  if (!evidence || evidence.length > MAX_EVIDENCE) {
    problems.push(`Say what counts as evidence in 1 to ${MAX_EVIDENCE} characters.`);
  }
  const lead = Number(draft.leadText);
  if (!/^\d+$/.test(draft.leadText.trim()) || lead > MAX_LEAD) {
    problems.push(`The lead is 0 to ${MAX_LEAD} working days.`);
  }
  const ways = draft.matchers.filter((item) => item.value.trim());
  if (ways.length === 0 || ways.length > MAX_MATCHERS) {
    problems.push(`Give 1 to ${MAX_MATCHERS} ways to find its evidence.`);
  }
  if (ways.some((item) => item.value.trim().length > MAX_VALUE)) {
    problems.push(`Each way to find evidence is at most ${MAX_VALUE} characters.`);
  }
  if (ways.some((item) => item.kind === "label" && /\s/.test(item.value.trim()))) {
    problems.push("A Jira label has no spaces.");
  }
  const labels = [...list(draft.labelsText), ...list(draft.whenLabelsText)];
  if (labels.some((label) => /\s/.test(label))) problems.push("A Jira label has no spaces.");
  if (list(draft.labelsText).length > MAX_LABELS) {
    problems.push(`A draft has at most ${MAX_LABELS} labels.`);
  }
  const key = draft.projectKey.trim();
  if (key && !/^[A-Z][A-Z0-9_]+$/i.test(key)) {
    problems.push("A Jira project key is capital letters and digits, such as CHK.");
  }
  const unknown = [
    ...unknownPlaceholders(draft.summary),
    ...unknownPlaceholders(draft.description),
  ];
  if (unknown.length > 0) {
    problems.push(
      `The draft uses ${[...new Set(unknown)].map((name) => `{${name}}`).join(", ")}, which OpenProgram does not fill in.`,
    );
  }
  return [...new Set(problems)];
}

function unknownPlaceholders(text: string): string[] {
  const known = new Set<string>(PLACEHOLDERS);
  return [...text.matchAll(/\{([A-Za-z_]+)\}/g)]
    .map((match) => match[1])
    .filter((name) => !known.has(name));
}

/** "each release · blocking · before production" for a criterion's card. */
export function appliesLine(
  criterion: Pick<ReadinessCriterionDto, "applies_to" | "severity" | "lead_working_days">,
): string {
  const scope = { release: "each release", project: "each project", pod: "each pod" }[
    criterion.applies_to
  ];
  const lead = criterion.lead_working_days ?? 10;
  return `${scope} · ${criterion.severity ?? "blocking"} · ${lead} working ${lead === 1 ? "day" : "days"} before the date`;
}

/** "Found by label security-review, title phrase "security review"; title words "pen test" only a hint". */
export function foundByLine(criterion: Pick<ReadinessCriterionDto, "matchers">): string {
  const describe = (item: ReadinessCriterionDto["matchers"][number]) =>
    item.kind === "label" || item.kind === "issue_type" || item.kind === "epic"
      ? `${MATCHER_LABELS[item.kind].toLowerCase()} ${item.value}`
      : `${MATCHER_LABELS[item.kind].toLowerCase()} "${item.value}"`;
  const strong = criterion.matchers.filter((item) => item.strength === "evidence").map(describe);
  const weak = criterion.matchers.filter((item) => item.strength === "candidate").map(describe);
  const parts = [
    strong.length ? `Found by ${strong.join(", ")}` : null,
    weak.length ? `${weak.join(", ")} only a hint` : null,
  ].filter((part): part is string => part !== null);
  return `${parts.join("; ")}.`;
}

/** The settings form's values, checked as the server checks them. */
export function settingsProblems(
  settings: Pick<ReadinessSettingsDto, "issue_type" | "labels">,
): string[] {
  const problems: string[] = [];
  const type = settings.issue_type.trim();
  if (!type || type.length > 60) problems.push("The issue type is 1 to 60 characters.");
  const labels = settings.labels ?? [];
  if (labels.length > MAX_LABELS || labels.some((label) => /\s/.test(label))) {
    problems.push(`New issues get at most ${MAX_LABELS} labels, each without spaces.`);
  }
  return problems;
}

export function list(text: string): string[] {
  return [
    ...new Set(
      text
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}
