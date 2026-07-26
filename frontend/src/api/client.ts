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
  CrossPersonRequestsResponse,
  CrossPersonRequestResponse,
  CrossPersonRequestStatus,
  CrossPersonRequestStatusUpdateRequest,
  AskRequest,
  AskResponse,
  AuthStatusResponse,
  BriefKind,
  NarrativeBriefsResponse,
  WriteBackAdoptionResponse,
  PodEscalationContactsResponse,
  PodEscalationContactsUpdateRequest,
  IdentityLinkResponse,
  IdentityLinkUpdateRequest,
  IdentityAutoMatchResponse,
  UnmappedMemberResponse,
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
  NodeKind,
  NodeTrendResponse,
  PortfolioHeatmapResponse,
  PortfolioFeedResponse,
  PortfolioFlowResponse,
  PortfolioRisksResponse,
  ProgramProjectLinkRequest,
  ProgramTreeResponse,
  ProjectProgressResponse,
  ProjectRisksResponse,
  ReadyResponse,
  LogoutResponse,
  MyStatusResponse,
  StatusCorrectionRequest,
  WorkstreamFlowResponse,
  WorkstreamProgressResponse,
  WorkflowDispatchResponse,
} from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME ?? "openprogram_csrf";
const CSRF_HEADER_NAME = import.meta.env.VITE_AUTH_CSRF_HEADER_NAME ?? "x-csrf-token";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function requestJson<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method,
    credentials: "include",
    headers: {
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...csrfHeader(options.method),
      "x-correlation-id": crypto.randomUUID(),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    const detail = await parseErrorBody(response);
    throw new ApiError(response.status, errorMessage(response.status, detail), detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  authStatus: () => requestJson<AuthStatusResponse>("/api/v1/auth/status"),
  logout: () => requestJson<LogoutResponse>("/api/v1/auth/logout", { method: "POST" }),
  health: () => requestJson<HealthResponse>("/health"),
  ready: () => requestJson<ReadyResponse>("/ready"),
  programTree: (programId: string) =>
    requestJson<GraphTreeDto>(`/graph/programs/${programId}/tree`),
  programs: (asOf?: string) => requestJson<DirectoryItemResponse[]>(withAsOf("/programs", asOf)),
  pods: (asOf?: string) => requestJson<DirectoryItemResponse[]>(withAsOf("/pods", asOf)),
  projects: (asOf?: string) => requestJson<DirectoryItemResponse[]>(withAsOf("/projects", asOf)),
  workstreams: (asOf?: string) =>
    requestJson<DirectoryItemResponse[]>(withAsOf("/workstreams", asOf)),
  workstream: (workstreamId: string, asOf?: string) =>
    requestJson<DirectoryItemResponse>(withAsOf(`/workstreams/${workstreamId}`, asOf)),
  projectWorkstreams: (projectId: string, asOf?: string) =>
    requestJson<DirectoryItemResponse[]>(withAsOf(`/projects/${projectId}/workstreams`, asOf)),
  focus: (asOf?: string) => requestJson<FocusResponse>(withAsOf("/me/focus", asOf)),
  myStatus: (asOf?: string) => requestJson<MyStatusResponse>(withAsOf("/me/status", asOf)),
  confirmMyStatus: (asOf?: string) =>
    requestJson<MyStatusResponse>(withAsOf("/me/status/confirm", asOf), {
      method: "POST",
    }),
  correctMyStatus: (input: StatusCorrectionRequest, asOf?: string) =>
    requestJson<MyStatusResponse>(withAsOf("/me/status/correct", asOf), {
      method: "POST",
      body: input,
    }),
  podBlockers: (podId: string, asOf?: string) =>
    requestJson<PodBlockersResponse>(withAsOf(`/pods/${podId}/blockers`, asOf)),
  podCheckins: (podId: string, asOf?: string) =>
    requestJson<PodCheckinsResponse>(withAsOf(`/pods/${podId}/checkins`, asOf)),
  projectProgress: (projectId: string, asOf?: string) =>
    requestJson<ProjectProgressResponse>(withAsOf(`/projects/${projectId}/progress`, asOf)),
  workstreamProgress: (workstreamId: string, asOf?: string) =>
    requestJson<WorkstreamProgressResponse>(
      withAsOf(`/workstreams/${workstreamId}/progress`, asOf),
    ),
  personaProgramTree: (programId: string, asOf?: string) =>
    requestJson<ProgramTreeResponse>(withAsOf(`/programs/${programId}/tree`, asOf)),
  portfolioHeatmap: (asOf?: string, programRootId?: string) =>
    requestJson<PortfolioHeatmapResponse>(
      withQuery("/portfolio/heatmap", { as_of: asOf, program_root_id: programRootId }),
    ),
  nodeTrend: (
    level: NodeKind,
    entityId: string,
    options?: { asOf?: string; windowDays?: number },
  ) =>
    requestJson<NodeTrendResponse>(
      withQuery(`/persona/${level}/${entityId}/trend`, {
        as_of: options?.asOf,
        window_days: options?.windowDays ? String(options.windowDays) : undefined,
      }),
    ),
  workstreamFlow: (workstreamId: string, asOf?: string) =>
    requestJson<WorkstreamFlowResponse>(
      withQuery(`/workstreams/${workstreamId}/flow`, { as_of: asOf }),
    ),
  portfolioFlow: (asOf?: string) =>
    requestJson<PortfolioFlowResponse>(withQuery("/portfolio/flow", { as_of: asOf })),
  portfolioFeed: (since?: string | null, limit = 50) =>
    requestJson<PortfolioFeedResponse>(
      withQuery("/portfolio/feed", {
        since: since ?? undefined,
        limit: String(limit),
      }),
    ),
  projectRisks: (projectId: string, asOf?: string) =>
    requestJson<ProjectRisksResponse>(withQuery(`/projects/${projectId}/risks`, { as_of: asOf })),
  portfolioRisks: (asOf?: string) =>
    requestJson<PortfolioRisksResponse>(withQuery("/portfolio/risks", { as_of: asOf })),
  portfolioCrossPersonRequests: (status: CrossPersonRequestStatus | null = "open") =>
    requestJson<CrossPersonRequestsResponse>(
      withQuery("/portfolio/cross-person-requests", {
        status: status ?? undefined,
      }),
    ),
  myCrossPersonRequests: () =>
    requestJson<CrossPersonRequestsResponse>("/me/cross-person-requests"),
  updateCrossPersonRequestStatus: (
    requestId: string,
    input: CrossPersonRequestStatusUpdateRequest,
  ) =>
    requestJson<CrossPersonRequestResponse>(`/cross-person-requests/${requestId}/status`, {
      method: "POST",
      body: input,
    }),
  ask: (input: AskRequest) =>
    requestJson<AskResponse>("/ask", {
      method: "POST",
      body: input,
    }),
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
  configWorkstreams: () => requestJson<ConfigNodeResponse[]>("/config/workstreams"),
  createConfigWorkstream: (input: ConfigNodeCreateRequest) =>
    requestJson<ConfigNodeResponse>("/config/workstreams", { method: "POST", body: input }),
  updateConfigWorkstream: (id: string, input: ConfigNodeUpdateRequest) =>
    requestJson<ConfigNodeResponse>(`/config/workstreams/${id}`, { method: "PUT", body: input }),
  deleteConfigWorkstream: (id: string) =>
    requestJson<void>(`/config/workstreams/${id}`, { method: "DELETE" }),
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
  linkProjectWorkstream: (projectId: string, workstreamId: string) =>
    requestJson<ConfigEdgeResponse>(`/config/projects/${projectId}/workstreams/${workstreamId}`, {
      method: "POST",
    }),
  unlinkProjectWorkstream: (projectId: string, workstreamId: string) =>
    requestJson<void>(`/config/projects/${projectId}/workstreams/${workstreamId}`, {
      method: "DELETE",
    }),
  linkPodWorkstream: (podId: string, workstreamId: string) =>
    requestJson<ConfigEdgeResponse>(`/config/pods/${podId}/workstreams/${workstreamId}`, {
      method: "POST",
    }),
  unlinkPodWorkstream: (podId: string, workstreamId: string) =>
    requestJson<void>(`/config/pods/${podId}/workstreams/${workstreamId}`, {
      method: "DELETE",
    }),
  linkWorkstreamTask: (workstreamId: string, taskId: string) =>
    requestJson<ConfigEdgeResponse>(`/config/workstreams/${workstreamId}/tasks/${taskId}`, {
      method: "POST",
    }),
  unlinkWorkstreamTask: (workstreamId: string, taskId: string) =>
    requestJson<void>(`/config/workstreams/${workstreamId}/tasks/${taskId}`, {
      method: "DELETE",
    }),
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
  podEscalationContacts: (podId: string) =>
    requestJson<PodEscalationContactsResponse>(`/config/pods/${podId}/escalation-contacts`),
  updatePodEscalationContacts: (podId: string, body: PodEscalationContactsUpdateRequest) =>
    requestJson<PodEscalationContactsResponse>(`/config/pods/${podId}/escalation-contacts`, {
      method: "PUT",
      body,
    }),
  configMemberIdentityLink: (memberId: string) =>
    requestJson<IdentityLinkResponse>(`/config/members/${memberId}/identity-link`),
  updateConfigMemberIdentityLink: (memberId: string, body: IdentityLinkUpdateRequest) =>
    requestJson<IdentityLinkResponse>(`/config/members/${memberId}/identity-link`, {
      method: "PUT",
      body,
    }),
  configUnmappedMembers: () => requestJson<UnmappedMemberResponse[]>("/config/members/unmapped"),
  autoMatchConfigIdentityLinks: () =>
    requestJson<IdentityAutoMatchResponse>("/config/members/identity-links/auto-match", {
      method: "POST",
    }),
  personaBriefs: (kind?: BriefKind, limit = 20) =>
    requestJson<NarrativeBriefsResponse>(
      withQuery("/persona/briefs", { kind, limit: String(limit) }),
    ),
  writebackAdoption: (windowDays?: number, limit = 5) =>
    requestJson<WriteBackAdoptionResponse>(
      withQuery("/persona/writeback-adoption", {
        window_days: windowDays ? String(windowDays) : undefined,
        limit: String(limit),
      }),
    ),
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

function csrfHeader(method?: string): Record<string, string> {
  const requestMethod = method ?? "GET";
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(requestMethod.toUpperCase())) {
    return {};
  }
  const token = readCookie(CSRF_COOKIE_NAME);
  return token ? { [CSRF_HEADER_NAME]: token } : {};
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  return (
    document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix))
      ?.slice(prefix.length) ?? null
  );
}

async function parseErrorBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      return await response.json();
    }
    return await response.text();
  } catch {
    return undefined;
  }
}

function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === "object" && detail !== null && "detail" in detail) {
    const value = (detail as { detail?: unknown }).detail;
    if (typeof value === "string") {
      return value;
    }
  }
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (status === 403) {
    return "Role scope does not include this view.";
  }
  if (status === 404) {
    return "The requested resource was not found.";
  }
  return `Request failed with status ${status}.`;
}
