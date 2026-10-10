// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type { DeliveryStage, GateTemplateDto, ItemKindDto, Role } from "../../api/schema";

/*
 * The gate template editor. Limits and wording mirror core/domain/gates.py
 * (validated_template) and the GateTemplateDto field limits, so the form says
 * what the server would refuse before it is sent.
 */

export const ROLE_NAMES: Record<Role, string> = {
  dev: "Developer",
  sm: "Scrum master",
  po: "Product owner",
  mgr: "Manager",
  exec: "Executive",
  admin: "Admin",
};

/** The roles a gate may name as signing an item off; an admin always may. */
export const SIGN_OFF_ROLES: Role[] = ["dev", "sm", "po", "mgr"];

export const MAX_KINDS = 10;
export const MAX_HEADINGS = 20;
export const MAX_ISSUE_TYPES = 50;
const MAX_NAME = 120;
const MAX_LABEL = 80;

export type KindDraft = {
  key: string;
  label: string;
  signOffRoles: Role[];
  evidenceRequired: boolean;
  headingsText: string;
  gherkin: boolean;
  /** Added in this edit: its key follows its label until saved. */
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

/** A saved item keeps its key, so items already kept on issues still belong to it. */
export function withLabel(kind: KindDraft, label: string): KindDraft {
  return { ...kind, label, key: kind.isNew ? kindKey(label) : kind.key };
}

export function toggleRole(kind: KindDraft, role: Role): KindDraft {
  const signOffRoles = kind.signOffRoles.includes(role)
    ? kind.signOffRoles.filter((item) => item !== role)
    : [...kind.signOffRoles, role];
  return { ...kind, signOffRoles };
}

/** Comma- or newline-separated names, trimmed and without repeats. */
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

/** What the server would refuse, said first by the form. */
export function draftProblems(draft: TemplateDraft): string[] {
  const problems: string[] = [];
  if (!draft.name.trim()) problems.push("Give the gate a name.");
  else if (draft.name.trim().length > MAX_NAME) {
    problems.push(`A gate's name is at most ${MAX_NAME} characters.`);
  }
  if (splitList(draft.issueTypesText).length > MAX_ISSUE_TYPES) {
    problems.push(`A gate names at most ${MAX_ISSUE_TYPES} issue types.`);
  }
  if (draft.kinds.length === 0) problems.push("A gate checks at least one kind of item.");
  if (draft.kinds.length > MAX_KINDS)
    problems.push(`A gate checks at most ${MAX_KINDS} kinds of item.`);
  const keys = new Set<string>();
  draft.kinds.forEach((kind, index) => {
    const name = kind.label.trim() || `Item ${index + 1}`;
    if (!kind.label.trim() || !kind.key) problems.push(`${name} needs a name.`);
    else if (kind.label.trim().length > MAX_LABEL) {
      problems.push(`${name}: a name is at most ${MAX_LABEL} characters.`);
    } else if (keys.has(kind.key))
      problems.push(`Two items are both called “${kind.label.trim()}”.`);
    keys.add(kind.key);
    if (kind.signOffRoles.length === 0) problems.push(`Say who signs off ${name}.`);
    if (splitList(kind.headingsText).length > MAX_HEADINGS) {
      problems.push(`${name}: at most ${MAX_HEADINGS} headings.`);
    }
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

/** "A", "A and B", "A, B and C". */
export function joinNames(names: string[], conjunction = "and"): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} ${conjunction} ${names[names.length - 1]}`;
}

/** "Signed off by the product owner or the manager, with a link to the evidence." */
export function signOffLine(kind: ItemKindDto): string {
  const names = kind.sign_off_roles.map((role) => `the ${ROLE_NAMES[role].toLowerCase()}`);
  const who = names.length === 0 ? "nobody yet" : joinNames(names, "or");
  return `Signed off by ${who}${kind.evidence_required ? ", with a link to the evidence" : ""}.`;
}

/** "Read from “Acceptance criteria” and “AC”, and from Given/When/Then scenarios." */
export function readFromLine(kind: ItemKindDto): string | null {
  const headings = (kind.headings ?? []).map((heading) => `“${heading}”`);
  if (headings.length === 0 && !kind.gherkin) return null;
  const parts = [
    ...(headings.length > 0 ? [`under ${joinNames(headings, "or")}`] : []),
    ...(kind.gherkin ? ["from Given/When/Then scenarios"] : []),
  ];
  return `Suggested from Jira ${parts.join(", and ")}.`;
}

/** "Every requirement", or "Stories and Epics only". */
export function appliesToLine(template: GateTemplateDto): string {
  const types = template.issue_types ?? [];
  if (types.length === 0) return "Every requirement";
  return `${joinNames(types)} only`;
}
