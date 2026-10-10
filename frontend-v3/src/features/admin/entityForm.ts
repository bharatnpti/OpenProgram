// The edit form for programs, projects, pods and workstreams, as pure logic: reading a node into
// form text, checking it, and building the request. Type imports only, so `node --test` runs it.
//
// The request carries only what changed. The backend merges a node's metadata field by field, so
// a field left out stays as it is: saving a name never overwrites a Jira key that a sync or
// another admin changed in the meantime.
import type { ConfigNodeResponse, ConfigNodeUpdateRequest } from "../../api/schema";
import { formatDate } from "../../lib/format.ts";
import { listNames, plural, reposOf, type EditableKind } from "./structure.ts";

export type EntityForm = {
  name: string;
  description: string;
  code: string;
  jiraProjectKey: string;
  jiraBoardId: string;
  jiraBaseJql: string;
  jiraFilterJql: string;
  /** One repository per line; commas and spaces separate too. */
  repos: string;
  type: string;
  phase: string;
  ownerId: string;
  tpmId: string;
  smId: string;
  targetDate: string;
  confidence: string;
  summary: string;
};

/** Suggestions, not a rule: the backend reads only `phase = done` and `target_date`. */
export const WORKSTREAM_TYPES: { value: string; label: string }[] = [
  { value: "feature", label: "Feature" },
  { value: "adhoc", label: "Ad hoc" },
  { value: "incident", label: "Incident" },
  { value: "migration", label: "Migration" },
  { value: "experiment", label: "Experiment" },
  { value: "ops", label: "Operations" },
];

export const WORKSTREAM_PHASES: { value: string; label: string }[] = [
  { value: "discovery", label: "Discovery" },
  { value: "build", label: "Build" },
  { value: "review", label: "Review" },
  { value: "rollout", label: "Rollout" },
  { value: "done", label: "Done" },
  { value: "paused", label: "Paused" },
];

/** A metadata value as text; anything that is not a string reads as empty. */
export function metaText(node: ConfigNodeResponse, key: string): string {
  const value = node.metadata[key];
  return typeof value === "string" ? value : "";
}

function metaNumber(node: ConfigNodeResponse, key: string): number | null {
  const value = node.metadata[key];
  return typeof value === "number" ? value : null;
}

export function formFromNode(node: ConfigNodeResponse): EntityForm {
  const confidence = metaNumber(node, "confidence");
  return {
    name: node.name,
    description: node.description ?? "",
    code: node.code ?? "",
    jiraProjectKey: node.jira_project_key ?? "",
    jiraBoardId: node.jira_board_id ?? "",
    jiraBaseJql: node.jira_base_jql ?? "",
    jiraFilterJql: node.jira_filter_jql ?? "",
    repos: reposOf(node).join("\n"),
    type: metaText(node, "type"),
    phase: metaText(node, "phase"),
    ownerId: metaText(node, "owner_id"),
    tpmId: metaText(node, "tpm_id"),
    smId: metaText(node, "sm_id"),
    targetDate: metaText(node, "target_date"),
    confidence: confidence === null ? "" : String(confidence),
    summary: metaText(node, "summary"),
  };
}

/** The repositories in a text box: one per line, commas and spaces also separate, no repeats. */
export function repoList(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).filter(Boolean))];
}

const orNull = (value: string | null | undefined): string | null =>
  (value ?? "").trim() === "" ? null : (value ?? "").trim();

/** 0 to 1, or null for empty; anything else is not a confidence. */
export function confidenceValue(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : "invalid";
}

/** A real calendar day written 2026-10-30, the form the backend reads a target date in. */
export function isIsoDay(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
  const date = new Date(`${text}T12:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === text;
}

export type FormErrors = Partial<Record<keyof EntityForm, string>>;

/**
 * What stops a save. `repoProblem` is the one rule that needs the rest of the structure: the
 * Git sync refuses a pod that lists a repository none of its projects provides, and a project
 * that stops providing one a pod still lists.
 */
export function validate(
  form: EntityForm,
  context: { repoProblem?: string | null } = {},
): FormErrors {
  const errors: FormErrors = {};
  if (form.name.trim() === "") errors.name = "Give it a name.";
  if (form.targetDate.trim() !== "" && !isIsoDay(form.targetDate.trim())) {
    errors.targetDate = "Use a date such as 2026-10-30.";
  }
  if (confidenceValue(form.confidence) === "invalid") {
    errors.confidence = "Use a number from 0 to 1, for example 0.7.";
  }
  if (context.repoProblem) errors.repos = context.repoProblem;
  return errors;
}

/** The request for a save: only the fields that differ from the node as it was loaded. */
export function buildUpdate(
  kind: EditableKind,
  node: ConfigNodeResponse,
  form: EntityForm,
): ConfigNodeUpdateRequest {
  const update: ConfigNodeUpdateRequest = {};
  if (form.name.trim() !== node.name) update.name = form.name.trim();
  if (orNull(form.description) !== orNull(node.description)) {
    update.description = orNull(form.description);
  }
  if ((kind === "project" || node.code) && orNull(form.code) !== orNull(node.code)) {
    update.code = orNull(form.code);
  }
  if (kind === "project") {
    if (orNull(form.jiraProjectKey) !== orNull(node.jira_project_key)) {
      update.jira_project_key = orNull(form.jiraProjectKey);
    }
    if (orNull(form.jiraBoardId) !== orNull(node.jira_board_id)) {
      update.jira_board_id = orNull(form.jiraBoardId);
    }
    if (orNull(form.jiraBaseJql) !== orNull(node.jira_base_jql)) {
      update.jira_base_jql = orNull(form.jiraBaseJql);
    }
  }
  if (kind === "pod" && orNull(form.jiraFilterJql) !== orNull(node.jira_filter_jql)) {
    update.jira_filter_jql = orNull(form.jiraFilterJql);
  }
  if (kind !== "program") {
    const repos = repoList(form.repos);
    if (repos.join("\n") !== reposOf(node).join("\n")) update.github_repos = repos;
  }
  if (kind === "workstream") {
    const metadata: Record<string, string | number | null> = {};
    const text: [keyof EntityForm, string][] = [
      ["type", "type"],
      ["phase", "phase"],
      ["ownerId", "owner_id"],
      ["tpmId", "tpm_id"],
      ["smId", "sm_id"],
      ["targetDate", "target_date"],
      ["summary", "summary"],
    ];
    for (const [field, key] of text) {
      const next = orNull(form[field]);
      if (next !== orNull(metaText(node, key))) metadata[key] = next;
    }
    const confidence = confidenceValue(form.confidence);
    if (confidence !== "invalid" && confidence !== metaNumber(node, "confidence")) {
      metadata.confidence = confidence;
    }
    if (Object.keys(metadata).length > 0) update.metadata = metadata;
  }
  return update;
}

export function hasChanges(update: ConfigNodeUpdateRequest): boolean {
  return Object.keys(update).length > 0;
}

/** The facts under a row's name: what is set on it, in the words a person would use. */
export function nodeDetails(
  kind: EditableKind | "member",
  node: ConfigNodeResponse,
  extras: { people?: number } = {},
): string[] {
  const parts: (string | null | undefined)[] = [];
  const repos = reposOf(node).length;
  if (kind === "program") parts.push(node.description ?? null);
  if (kind === "project") {
    // A code that is only the Jira key said twice is said once.
    parts.push(
      node.code && node.code !== node.jira_project_key ? node.code : null,
      node.jira_project_key ? `Jira ${node.jira_project_key}` : null,
    );
    parts.push(repos > 0 ? plural(repos, "repository", "repositories") : null);
  }
  if (kind === "pod") {
    parts.push(extras.people === undefined ? null : plural(extras.people, "person", "people"));
    parts.push(node.jira_filter_jql ? "Jira filter" : null);
    parts.push(repos > 0 ? plural(repos, "repository", "repositories") : null);
  }
  if (kind === "workstream") {
    const target = metaText(node, "target_date");
    parts.push(metaText(node, "type") || null, metaText(node, "phase") || null);
    parts.push(target ? `target ${formatDate(target)}` : null);
  }
  if (kind === "member") parts.push(metaText(node, "title") || null);
  return parts.filter((part): part is string => Boolean(part));
}

/** "Not in Checkout Revamp: acme/other" for the repository rule on a pod. */
export function podRepoProblem(
  repos: readonly string[],
  scope: readonly string[],
  projectNames: readonly string[],
): string | null {
  const outside = repos.filter((repo) => !scope.includes(repo));
  if (outside.length === 0) return null;
  if (projectNames.length === 0) {
    return "A pod can only read repositories its projects use. Put it on a project first, under Links.";
  }
  return `${listNames(outside)} ${
    outside.length === 1 ? "is" : "are"
  } not in ${listNames(projectNames)}. A pod can only read repositories its projects use, so add ${
    outside.length === 1 ? "it" : "them"
  } to the project first.`;
}

/** "Payments Pod still reads acme/api" for the rule on a project that gives up a repository. */
export function projectRepoProblem(
  stillUsed: readonly { podName: string; repos: readonly string[] }[],
): string | null {
  if (stillUsed.length === 0) return null;
  const several = stillUsed.length > 1 || stillUsed[0].repos.length > 1;
  const reads = stillUsed
    .map((item) => `${item.podName} still reads ${listNames(item.repos)}`)
    .join("; ");
  return `${reads}. Remove ${several ? "them" : "it"} from the pod${
    stillUsed.length > 1 ? "s" : ""
  } first, or keep ${several ? "them" : "it"} here.`;
}
