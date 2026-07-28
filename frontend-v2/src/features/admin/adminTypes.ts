import { z } from "zod";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse } from "../../api/schema";

export const nodeSchema = z.object({
  id: z.string().min(1, "ID is required"),
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
  code: z.string().optional(),
  jira_project_key: z.string().optional(),
  jira_base_jql: z.string().optional(),
  jira_board_id: z.string().optional(),
  jira_filter_jql: z.string().optional(),
  github_repos: z.string().optional(),
  type: z.string().optional(),
  phase: z.string().optional(),
  owner_id: z.string().optional(),
  tpm_id: z.string().optional(),
  sm_id: z.string().optional(),
  target_date: z.string().optional(),
  confidence: z.string().optional(),
  summary: z.string().optional(),
});

export type NodeFormValues = z.infer<typeof nodeSchema>;

export type EntityKind = "programs" | "projects" | "workstreams" | "pods" | "members";

export const entityLabels: Record<EntityKind, string> = {
  programs: "Programs",
  projects: "Projects",
  workstreams: "Workstreams",
  pods: "Pods",
  members: "Members",
};

export const entityKinds = Object.keys(entityLabels) as EntityKind[];

export const workstreamTypes = ["feature", "adhoc", "incident", "migration", "experiment", "ops"];
export const workstreamPhases = ["discovery", "build", "review", "rollout", "done", "paused"];

export const weekdayOptions = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
];

export type ConfirmState =
  | {
      open: true;
      title: string;
      description: string;
      confirmLabel: string;
      destructive?: boolean;
      onConfirm: () => void;
    }
  | { open: false };

export function saveNode(
  kind: EntityKind,
  values: NodeFormValues,
  editing: ConfigNodeResponse | null,
) {
  const payload = {
    id: values.id,
    name: values.name,
    description: blankToNull(values.description),
    code: blankToNull(values.code),
    ...(kind === "projects"
      ? {
          jira_project_key: blankToNull(values.jira_project_key),
          jira_base_jql: blankToNull(values.jira_base_jql),
          jira_board_id: blankToNull(values.jira_board_id),
          github_repos: repoList(values.github_repos),
        }
      : {}),
    ...(kind === "pods"
      ? {
          jira_filter_jql: blankToNull(values.jira_filter_jql),
          github_repos: repoList(values.github_repos),
        }
      : {}),
    ...(kind === "workstreams"
      ? {
          metadata: workstreamMetadata(values),
        }
      : {}),
  };
  if (editing) {
    if (kind === "programs") return apiClient.updateConfigProgram(editing.id, payload);
    if (kind === "projects") return apiClient.updateConfigProject(editing.id, payload);
    if (kind === "workstreams") return apiClient.updateConfigWorkstream(editing.id, payload);
    if (kind === "pods") return apiClient.updateConfigPod(editing.id, payload);
    return apiClient.updateConfigMember(editing.id, payload);
  }
  if (kind === "programs") return apiClient.createConfigProgram(payload);
  if (kind === "projects") return apiClient.createConfigProject(payload);
  if (kind === "workstreams") return apiClient.createConfigWorkstream(payload);
  if (kind === "pods") return apiClient.createConfigPod(payload);
  return apiClient.createConfigMember(payload);
}

export function deleteNode(kind: EntityKind, id: string) {
  if (kind === "programs") return apiClient.deleteConfigProgram(id);
  if (kind === "projects") return apiClient.deleteConfigProject(id);
  if (kind === "workstreams") return apiClient.deleteConfigWorkstream(id);
  if (kind === "pods") return apiClient.deleteConfigPod(id);
  return apiClient.deleteConfigMember(id);
}

export function filterNodes(nodes: ConfigNodeResponse[], query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return nodes;
  return nodes.filter((node) =>
    [
      node.id,
      node.name,
      node.description ?? "",
      node.code ?? "",
      node.jira_project_key ?? "",
      node.jira_base_jql ?? "",
      node.jira_board_id ?? "",
      node.jira_filter_jql ?? "",
      (node.github_repos ?? []).join(" "),
      metadataString(node, "type"),
      metadataString(node, "phase"),
      metadataString(node, "owner_id"),
      metadataString(node, "tpm_id"),
      metadataString(node, "sm_id"),
      metadataString(node, "target_date"),
      metadataString(node, "summary"),
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalized),
  );
}

export function defaultNodeValues(): NodeFormValues {
  return {
    id: "",
    name: "",
    description: "",
    code: "",
    jira_project_key: "",
    jira_base_jql: "",
    jira_board_id: "",
    jira_filter_jql: "",
    github_repos: "",
    type: "",
    phase: "",
    owner_id: "",
    tpm_id: "",
    sm_id: "",
    target_date: "",
    confidence: "",
    summary: "",
  };
}

export function editValuesFromNode(node: ConfigNodeResponse): NodeFormValues {
  return {
    id: node.id,
    name: node.name,
    description: node.description ?? "",
    code: node.code ?? "",
    jira_project_key: node.jira_project_key ?? "",
    jira_base_jql: node.jira_base_jql ?? "",
    jira_board_id: node.jira_board_id ?? "",
    jira_filter_jql: node.jira_filter_jql ?? "",
    github_repos: (node.github_repos ?? []).join("\n"),
    type: metadataString(node, "type"),
    phase: metadataString(node, "phase"),
    owner_id: metadataString(node, "owner_id"),
    tpm_id: metadataString(node, "tpm_id"),
    sm_id: metadataString(node, "sm_id"),
    target_date: metadataString(node, "target_date"),
    confidence: metadataNumberString(node, "confidence"),
    summary: metadataString(node, "summary"),
  };
}

export function blankToNull(value: string | undefined): string | null {
  const normalized = value?.trim() ?? "";
  return normalized ? normalized : null;
}

export function repoList(value: string | undefined): string[] {
  const seen = new Set<string>();
  const repos: string[] = [];
  for (const item of (value ?? "").split(/[,\n]/)) {
    const repo = item.trim();
    if (!repo || seen.has(repo)) continue;
    seen.add(repo);
    repos.push(repo);
  }
  return repos;
}

export function workstreamMetadata(values: NodeFormValues) {
  return {
    type: blankToNull(values.type),
    phase: blankToNull(values.phase),
    owner_id: blankToNull(values.owner_id),
    tpm_id: blankToNull(values.tpm_id),
    sm_id: blankToNull(values.sm_id),
    target_date: blankToNull(values.target_date),
    confidence: confidenceValue(values.confidence),
    summary: blankToNull(values.summary),
  };
}

export function confidenceValue(value: string | undefined): number | null {
  const normalized = value?.trim() ?? "";
  if (!normalized) return null;
  const parsed = Number(normalized);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(1, parsed));
}

export function metadataString(node: ConfigNodeResponse, key: string): string {
  const value = node.metadata[key];
  return typeof value === "string" ? value : "";
}

export function metadataNumberString(node: ConfigNodeResponse, key: string): string {
  const value = node.metadata[key];
  return typeof value === "number" ? String(value) : "";
}

export function confirmUnlink(
  askConfirm: (state: ConfirmState) => void,
  title: string,
  onConfirm: () => void,
) {
  askConfirm({
    open: true,
    title,
    description:
      "This removes an existing graph relationship. The underlying nodes remain configured.",
    confirmLabel: "Unlink",
    destructive: true,
    onConfirm,
  });
}

export function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "Operation failed.";
}
