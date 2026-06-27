import type {
  CheckinPreferenceResponse,
  CheckinPreferenceUpdateRequest,
  ChatSimulatorMessagesResponse,
  ChatSimulatorReplyRequest,
  ChatSimulatorReplyResponse,
  ChatSimulatorStatusResponse,
  CheckinDispatchRequest,
  ConfigEdgeResponse,
  ConfigNodeCreateRequest,
  ConfigNodeResponse,
  ConfigNodeUpdateRequest,
  DirectoryItemResponse,
  DirectorySearchResponse,
  DirectorySyncResponse,
  MemberFromDirectoryRequest,
  FocusResponse,
  GraphTreeDto,
  HealthResponse,
  MemberTaskAssignmentRequest,
  PodMemberLinkRequest,
  PodBlockersResponse,
  PodCheckinsResponse,
  PortfolioHeatmapResponse,
  ProgramProjectLinkRequest,
  ProgramTreeResponse,
  ProjectProgressResponse,
  ReadyResponse,
  WorkflowDispatchResponse,
} from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function requestJson<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method,
    headers: {
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      "x-correlation-id": crypto.randomUUID(),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  health: () => requestJson<HealthResponse>("/health"),
  ready: () => requestJson<ReadyResponse>("/ready"),
  programTree: (programId: string) =>
    requestJson<GraphTreeDto>(`/graph/programs/${programId}/tree`),
  programs: (asOf?: string) => requestJson<DirectoryItemResponse[]>(withAsOf("/programs", asOf)),
  pods: (asOf?: string) => requestJson<DirectoryItemResponse[]>(withAsOf("/pods", asOf)),
  projects: (asOf?: string) => requestJson<DirectoryItemResponse[]>(withAsOf("/projects", asOf)),
  focus: (asOf?: string) => requestJson<FocusResponse>(withAsOf("/me/focus", asOf)),
  podBlockers: (podId: string, asOf?: string) =>
    requestJson<PodBlockersResponse>(withAsOf(`/pods/${podId}/blockers`, asOf)),
  podCheckins: (podId: string, asOf?: string) =>
    requestJson<PodCheckinsResponse>(withAsOf(`/pods/${podId}/checkins`, asOf)),
  projectProgress: (projectId: string, asOf?: string) =>
    requestJson<ProjectProgressResponse>(withAsOf(`/projects/${projectId}/progress`, asOf)),
  personaProgramTree: (programId: string, asOf?: string) =>
    requestJson<ProgramTreeResponse>(withAsOf(`/programs/${programId}/tree`, asOf)),
  portfolioHeatmap: (asOf?: string, programRootId?: string) =>
    requestJson<PortfolioHeatmapResponse>(
      withQuery("/portfolio/heatmap", { as_of: asOf, program_root_id: programRootId }),
    ),
  checkinPreference: () => requestJson<CheckinPreferenceResponse>("/me/checkin-preference"),
  updateCheckinPreference: (input: CheckinPreferenceUpdateRequest) =>
    requestJson<CheckinPreferenceResponse>("/me/checkin-preference", {
      method: "PUT",
      body: input,
    }),
  dispatchCheckin: (input: CheckinDispatchRequest) =>
    requestJson<WorkflowDispatchResponse>("/admin/workflows/checkin/dispatch", {
      method: "POST",
      body: input,
    }),
  chatSimulatorStatus: () =>
    requestJson<ChatSimulatorStatusResponse>("/test/chat-simulator/status"),
  chatSimulatorMessages: () =>
    requestJson<ChatSimulatorMessagesResponse>("/test/chat-simulator/messages"),
  replyChatSimulatorMessage: (messageId: string, input: ChatSimulatorReplyRequest) =>
    requestJson<ChatSimulatorReplyResponse>(`/test/chat-simulator/messages/${messageId}/reply`, {
      method: "POST",
      body: input,
    }),
  resetChatSimulatorState: () =>
    requestJson<void>("/test/chat-simulator/state", { method: "DELETE" }),
  configPrograms: () => requestJson<ConfigNodeResponse[]>("/config/programs"),
  createConfigProgram: (input: ConfigNodeCreateRequest) =>
    requestJson<ConfigNodeResponse>("/config/programs", { method: "POST", body: input }),
  updateConfigProgram: (id: string, input: ConfigNodeUpdateRequest) =>
    requestJson<ConfigNodeResponse>(`/config/programs/${id}`, { method: "PUT", body: input }),
  deleteConfigProgram: (id: string) =>
    requestJson<void>(`/config/programs/${id}`, { method: "DELETE" }),
  configProjects: () => requestJson<ConfigNodeResponse[]>("/config/projects"),
  createConfigProject: (input: ConfigNodeCreateRequest) =>
    requestJson<ConfigNodeResponse>("/config/projects", { method: "POST", body: input }),
  updateConfigProject: (id: string, input: ConfigNodeUpdateRequest) =>
    requestJson<ConfigNodeResponse>(`/config/projects/${id}`, { method: "PUT", body: input }),
  deleteConfigProject: (id: string) =>
    requestJson<void>(`/config/projects/${id}`, { method: "DELETE" }),
  configPods: () => requestJson<ConfigNodeResponse[]>("/config/pods"),
  createConfigPod: (input: ConfigNodeCreateRequest) =>
    requestJson<ConfigNodeResponse>("/config/pods", { method: "POST", body: input }),
  updateConfigPod: (id: string, input: ConfigNodeUpdateRequest) =>
    requestJson<ConfigNodeResponse>(`/config/pods/${id}`, { method: "PUT", body: input }),
  deleteConfigPod: (id: string) => requestJson<void>(`/config/pods/${id}`, { method: "DELETE" }),
  configMembers: () => requestJson<ConfigNodeResponse[]>("/config/members"),
  searchDirectory: (query = "", limit = 25, offset = 0) =>
    requestJson<DirectorySearchResponse>(
      withQuery("/config/directory/users", {
        query,
        limit: String(limit),
        offset: String(offset),
      }),
    ),
  syncDirectory: () =>
    requestJson<DirectorySyncResponse>("/config/directory/sync", {
      method: "POST",
    }),
  addMembersFromDirectory: (externalIds: string[]) =>
    requestJson<ConfigNodeResponse[]>("/config/members/from-directory", {
      method: "POST",
      body: { external_ids: externalIds } satisfies MemberFromDirectoryRequest,
    }),
  createConfigMember: (input: ConfigNodeCreateRequest) =>
    requestJson<ConfigNodeResponse>("/config/members", { method: "POST", body: input }),
  updateConfigMember: (id: string, input: ConfigNodeUpdateRequest) =>
    requestJson<ConfigNodeResponse>(`/config/members/${id}`, { method: "PUT", body: input }),
  deleteConfigMember: (id: string) =>
    requestJson<void>(`/config/members/${id}`, { method: "DELETE" }),
  linkProjectProgram: (projectId: string, input: ProgramProjectLinkRequest) =>
    requestJson<ConfigEdgeResponse>(`/config/projects/${projectId}/program`, {
      method: "POST",
      body: input,
    }),
  unlinkProjectProgram: (projectId: string, programId?: string) =>
    requestJson<void>(
      withQuery(`/config/projects/${projectId}/program`, { program_id: programId }),
      { method: "DELETE" },
    ),
  linkPodProject: (podId: string, projectId: string) =>
    requestJson<ConfigEdgeResponse>(`/config/pods/${podId}/projects/${projectId}`, {
      method: "POST",
    }),
  unlinkPodProject: (podId: string, projectId: string) =>
    requestJson<void>(`/config/pods/${podId}/projects/${projectId}`, { method: "DELETE" }),
  linkPodMember: (podId: string, memberId: string, input: PodMemberLinkRequest) =>
    requestJson<ConfigEdgeResponse>(`/config/pods/${podId}/members/${memberId}`, {
      method: "POST",
      body: input,
    }),
  unlinkPodMember: (podId: string, memberId: string) =>
    requestJson<void>(`/config/pods/${podId}/members/${memberId}`, { method: "DELETE" }),
  assignMemberTask: (memberId: string, input: MemberTaskAssignmentRequest) =>
    requestJson<ConfigEdgeResponse>(`/config/members/${memberId}/tasks`, {
      method: "POST",
      body: input,
    }),
  unassignMemberTask: (memberId: string, taskId: string) =>
    requestJson<void>(withQuery(`/config/members/${memberId}/tasks`, { task_id: taskId }), {
      method: "DELETE",
    }),
  configMemberCheckinPreference: (memberId: string) =>
    requestJson<CheckinPreferenceResponse>(`/config/members/${memberId}/checkin-preference`),
  updateConfigMemberCheckinPreference: (memberId: string, input: CheckinPreferenceUpdateRequest) =>
    requestJson<CheckinPreferenceResponse>(`/config/members/${memberId}/checkin-preference`, {
      method: "PUT",
      body: input,
    }),
  configCheckinPreferences: () =>
    requestJson<CheckinPreferenceResponse[]>("/config/checkin-preferences"),
};

function withAsOf(path: string, asOf?: string): string {
  if (!asOf) {
    return path;
  }
  return `${path}?as_of=${encodeURIComponent(asOf)}`;
}

function withQuery(path: string, params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      search.set(key, value);
    }
  });
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}
