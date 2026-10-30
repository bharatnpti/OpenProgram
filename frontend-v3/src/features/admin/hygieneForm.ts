// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DeliveryStage,
  HygieneConditionKind,
  HygieneIssueField,
  HygieneOwnerKind,
  HygienePreviewScopeDto,
  HygieneRuleDto,
  HygieneRuleNeed,
  HygieneRuleScope,
  HygieneRuleSeverity,
  HygieneSettingsDto,
  HygieneStageFilter,
  ReadinessMatcherKind,
} from "../../api/schema";

/*
 * The Jira hygiene rule editor. Limits and wording mirror core/domain/hygiene.py
 * (validated_rule, validated_settings), so the form says what the server would
 * refuse before it is sent. A rule lists only until an admin switches it to ask.
 */

export const MAX_NAME = 80;
export const MAX_SELECT = 8;
export const MAX_VALUE = 80;
export const MAX_LABELS = 10;
export const MAX_FIX = 300;
export const MAX_DAYS = 60;
export const MAX_POINTS = 1000;

export const CONDITION_LABELS: Record<HygieneConditionKind, string> = {
  added_after_baseline_without_label: "Added after planning without a change label",
  missing_field: "A field is empty",
  missing_label: "A label is missing",
  missing_gate_kind: "The gates' scan found no items of a kind",
  stale_in_status: "No Jira change for some working days in a stage",
  past_due_open: "Still open past its due date",
  too_big: "More story points than a limit",
};

export const FIELD_LABELS: Record<HygieneIssueField, string> = {
  story_points: "Story points",
  due_date: "Due date",
  parent: "Epic or parent",
  fix_version: "Fix version",
  priority: "Priority",
  assignee: "Assignee",
  components: "Component",
  severity: "Severity (the Jira connection's severity field)",
};

export const OWNER_LABELS: Record<HygieneOwnerKind, string> = {
  assignee: "The assignee",
  reporter: "The reporter",
  profile: "The pod's person with a profile",
  pod_scrum_master: "The pod's scrum master",
  decision_owner: "Whoever decides on the project",
};

export const SCOPE_LABELS: Record<HygieneRuleScope, string> = {
  project: "Each project's issues",
  release: "Each release's issues",
  pod: "Each pod's issues",
};

export const STAGE_FILTER_LABELS: Record<HygieneStageFilter, string> = {
  any: "In any stage",
  before: "Before a stage",
  at_or_past: "In a stage or later",
};

export const SELECT_LABELS: Record<ReadinessMatcherKind, string> = {
  issue_type: "Issue type",
  label: "Label",
  title_phrase: "Title phrase",
  title_words: "Title words",
  epic: "Epic",
};

export type SelectDraft = { kind: ReadinessMatcherKind; value: string };

export type RuleDraft = {
  ruleId: string;
  name: string;
  scope: HygieneRuleScope;
  everyType: boolean;
  stageFilter: HygieneStageFilter;
  stage: DeliveryStage;
  matchers: SelectDraft[];
  condition: HygieneConditionKind;
  labelsText: string;
  field: HygieneIssueField;
  gateKind: string;
  conditionStage: DeliveryStage;
  daysText: string;
  pointsText: string;
  severity: HygieneRuleSeverity;
  need: HygieneRuleNeed;
  ownerKind: HygieneOwnerKind;
  ownerProfile: string;
  suggestedFix: string;
  enabled: boolean;
};

export function newRuleDraft(): RuleDraft {
  return {
    ruleId: "",
    name: "",
    scope: "project",
    everyType: false,
    stageFilter: "any",
    stage: "in_development",
    matchers: [],
    condition: "missing_field",
    labelsText: "",
    field: "story_points",
    gateKind: "acceptance",
    conditionStage: "in_development",
    daysText: "5",
    pointsText: "8",
    severity: "list_only",
    need: "fix",
    ownerKind: "pod_scrum_master",
    ownerProfile: "",
    suggestedFix: "",
    enabled: true,
  };
}

export function draftFromRule(rule: HygieneRuleDto): RuleDraft {
  const base = newRuleDraft();
  const select = rule.select ?? { matchers: [], stage_filter: "any", every_type: false };
  const condition = rule.condition;
  const owner = rule.owner ?? { kind: "pod_scrum_master", profile: "" };
  const days = condition.working_days ?? 0;
  return {
    ruleId: rule.rule_id ?? "",
    name: rule.name,
    scope: rule.scope ?? "project",
    everyType: select.every_type ?? false,
    stageFilter: select.stage_filter ?? "any",
    stage: select.stage ?? base.stage,
    matchers: (select.matchers ?? []).map((item) => ({ kind: item.kind, value: item.value })),
    condition: condition.kind,
    labelsText: (condition.labels ?? []).join(", "),
    field: condition.field ?? base.field,
    gateKind: condition.gate_kind || base.gateKind,
    conditionStage: condition.stage ?? base.conditionStage,
    daysText:
      condition.kind === "stale_in_status" || condition.kind === "past_due_open"
        ? String(days)
        : base.daysText,
    pointsText: condition.points_over ? String(condition.points_over) : base.pointsText,
    severity: rule.severity ?? "list_only",
    need: rule.need ?? "fix",
    ownerKind: owner.kind,
    ownerProfile: owner.profile ?? "",
    suggestedFix: rule.suggested_fix ?? "",
    enabled: rule.enabled ?? true,
  };
}

export function ruleFromDraft(draft: RuleDraft): HygieneRuleDto {
  const kind = draft.condition;
  const labels = splitLabels(draft.labelsText);
  return {
    rule_id: draft.ruleId,
    version: 0,
    name: draft.name.trim(),
    scope: draft.scope,
    select: {
      matchers: draft.matchers
        .filter((item) => item.value.trim() !== "")
        .map((item) => ({ kind: item.kind, value: item.value.trim() })),
      stage_filter: draft.stageFilter,
      stage: draft.stageFilter === "any" ? null : draft.stage,
      every_type: draft.everyType,
    },
    condition: {
      kind,
      labels:
        kind === "missing_label" || kind === "added_after_baseline_without_label" ? labels : [],
      field: kind === "missing_field" ? draft.field : null,
      gate_kind: kind === "missing_gate_kind" ? draft.gateKind.trim() : "",
      stage: kind === "stale_in_status" ? draft.conditionStage : null,
      working_days:
        kind === "stale_in_status" || kind === "past_due_open" ? toInt(draft.daysText) : 0,
      points_over: kind === "too_big" ? Number(draft.pointsText) : 0,
    },
    severity: draft.severity,
    need: draft.need,
    owner: {
      kind: draft.ownerKind,
      profile: draft.ownerKind === "profile" ? draft.ownerProfile : "",
    },
    suggested_fix: draft.suggestedFix.trim().replace(/\s+/g, " "),
    enabled: draft.enabled,
  };
}

/** Everything the server would refuse, in its words, before anything is sent. */
export function draftProblems(draft: RuleDraft): string[] {
  const problems: string[] = [];
  const name = draft.name.trim();
  if (!name || name.length > MAX_NAME)
    problems.push(`A rule needs a name of 1 to ${MAX_NAME} characters.`);
  if (draft.matchers.length > MAX_SELECT) {
    problems.push(`A rule narrows its issues with at most ${MAX_SELECT} matchers.`);
  }
  for (const item of draft.matchers) {
    const value = item.value.trim();
    if (value.length > MAX_VALUE) {
      problems.push(`Each matcher needs a value of 1 to ${MAX_VALUE} characters.`);
    } else if (item.kind === "label" && /\s/.test(value)) {
      problems.push(`The label "${value}" has a space; Jira labels have none.`);
    }
  }
  const labels = splitLabels(draft.labelsText);
  if (labels.some((label) => /\s/.test(label)) || labels.length > MAX_LABELS) {
    problems.push(`Name at most ${MAX_LABELS} labels, each without spaces.`);
  }
  const kind = draft.condition;
  if (kind === "missing_label" && labels.length === 0) {
    problems.push("Name the label, or labels, an issue should have.");
  }
  if (kind === "missing_gate_kind" && !/^[a-z][a-z0-9_]{0,39}$/.test(draft.gateKind.trim())) {
    problems.push("Name the gate item kind, such as acceptance.");
  }
  const days = toInt(draft.daysText);
  if (kind === "stale_in_status" && !(days >= 1 && days <= MAX_DAYS)) {
    problems.push(`The days without a change are 1 to ${MAX_DAYS}.`);
  }
  if (kind === "past_due_open" && !(days >= 0 && days <= MAX_DAYS)) {
    problems.push(`The grace after the due date is 0 to ${MAX_DAYS} working days.`);
  }
  const points = Number(draft.pointsText);
  if (kind === "too_big" && !(points > 0 && points <= MAX_POINTS)) {
    problems.push(`The story points an issue may have are above 0 and at most ${MAX_POINTS}.`);
  }
  if (draft.ownerKind === "profile" && !draft.ownerProfile) {
    problems.push("Pick the profile whose holder is asked.");
  }
  const fix = draft.suggestedFix.trim();
  if (fix.length > MAX_FIX) problems.push(`The suggested fix is at most ${MAX_FIX} characters.`);
  const unknown = unknownPlaceholders(fix);
  if (unknown.length > 0) {
    problems.push(
      `The suggested fix uses ${unknown.map((item) => `{${item}}`).join(", ")}, which OpenProgram does not fill in.`,
    );
  }
  return problems;
}

/** The words a suggested fix may fill in; nothing else, and never Jira text. */
export const FIX_PLACEHOLDERS = [
  "key",
  "title",
  "stage",
  "release",
  "label",
  "field",
  "days",
  "baseline_date",
] as const;

export function unknownPlaceholders(text: string): string[] {
  const known = new Set<string>(FIX_PLACEHOLDERS);
  const found = [...text.matchAll(/\{([A-Za-z_]+)\}/g)].map((match) => match[1]);
  return [...new Set(found.filter((name) => !known.has(name)))].sort();
}

export function splitLabels(text: string): string[] {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const part of text.split(",")) {
    const clean = part.trim();
    if (clean && !seen.has(clean.toLowerCase())) {
      seen.add(clean.toLowerCase());
      kept.push(clean);
    }
  }
  return kept;
}

export type SettingsDraft = { enabled: boolean; labelsText: string; daysText: string };

export function settingsDraft(settings: HygieneSettingsDto): SettingsDraft {
  return {
    enabled: settings.enabled,
    labelsText: (settings.change_labels ?? []).join(", "),
    daysText: String(settings.stale_working_days ?? 5),
  };
}

export function settingsBody(draft: SettingsDraft): HygieneSettingsDto {
  return {
    enabled: draft.enabled,
    change_labels: splitLabels(draft.labelsText),
    stale_working_days: toInt(draft.daysText),
  };
}

export function settingsProblems(draft: SettingsDraft): string[] {
  const problems: string[] = [];
  const labels = splitLabels(draft.labelsText);
  if (labels.length > MAX_LABELS || labels.some((label) => /\s/.test(label))) {
    problems.push(`Name at most ${MAX_LABELS} change labels, each without spaces.`);
  }
  const days = toInt(draft.daysText);
  if (!(days >= 1 && days <= MAX_DAYS)) {
    problems.push(`The days without a change are 1 to ${MAX_DAYS}.`);
  }
  return problems;
}

/** "Checkout Revamp: 3 of 12 issues break it." / "...: needs a planning baseline first." */
export function previewLine(row: HygienePreviewScopeDto): string {
  if (row.needs_baseline) {
    return `${row.scope.name}: no planning baseline yet, so nothing counts as added after planning. Mark planning complete on Overall.`;
  }
  if (row.looked_at === 0) return `${row.scope.name}: the rule looks at no issue here.`;
  const of = `${row.found} of ${row.looked_at} ${row.looked_at === 1 ? "issue" : "issues"}`;
  return `${row.scope.name}: ${of} ${row.found === 1 ? "breaks" : "break"} it.`;
}

/** "Owner: the assignee" / "Owner: the pod's QA and test", for the rule cards. */
export function ownerLine(
  owner: HygieneRuleDto["owner"],
  profileLabels: Readonly<Record<string, string>>,
): string {
  const kind = owner?.kind ?? "pod_scrum_master";
  if (kind === "profile" && owner?.profile) {
    return `Owner: the pod's ${profileLabels[owner.profile] ?? owner.profile}`;
  }
  const label = OWNER_LABELS[kind];
  return `Owner: ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

/** "Lists only" / "Asks the owner, a fix" for the rule cards. */
export function modeLine(rule: Pick<HygieneRuleDto, "severity" | "need">): string {
  if (rule.severity !== "ask") return "Lists only";
  return rule.need === "decision" ? "Asks the owner for a decision" : "Asks the owner for a fix";
}

function toInt(text: string): number {
  const clean = text.trim();
  return /^\d+$/.test(clean) ? Number(clean) : Number.NaN;
}
