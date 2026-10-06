import type {
  EscalationMatrixRequest,
  EscalationMatrixResponse,
  EscalationOverviewResponse,
  GateBoardResponse,
  GateItemResponse,
  GateScanResponse,
  GateTemplateDto,
  GateTemplatesResponse,
  ItemStatus,
  QuestionStatus,
  TrackedQuestionResponse,
  CommitmentResponse,
  DeliveryDateRequest,
  PodDeliveryResponse,
  ProjectDeliveryResponse,
  ReleaseCandidateResponse,
  ReleaseRequest,
  ReleaseResponse,
  ConnectionResponse,
  ConnectionTestRequest,
  ConnectionTestResponse,
  ConnectionUpdateRequest,
  DayReportRequest,
  DayReportResponse,
  DayReportSetupResponse,
  DeliveryStagesResponse,
  DeliveryStagesUpdateRequest,
  ObservedStatusResponse,
  ReportPreviewResponse,
  ReportRunResponse,
  RequirementsResponse,
  CheckinPreferenceResponse,
  CheckinPreferenceUpdateRequest,
  ChatSimulatorMessagesResponse,
  ChatSimulatorReplyRequest,
  ChatSimulatorReplyResponse,
  ChatSimulatorStatusResponse,
  ChatSimulatorUserMessageRequest,
  ChatSimulatorUserMessageResponse,
  CheckinDispatchRequest,
  ConfigEdgeResponse,
  ConfigNodeCreateRequest,
  ConfigNodeResponse,
  ConfigNodeUpdateRequest,
  CrossPersonRequestsResponse,
  CrossPersonRequestResponse,
  CrossPersonRequestStatus,
  CrossPersonRequestStatusUpdateRequest,
  MyRequestRelation,
  AskRequest,
  AskResponse,
  AuthStatusResponse,
  DevUsersResponse,
  BriefKind,
  NarrativeBriefsResponse,
  WriteBackAdoptionResponse,
  EscalationCandidateResponse,
  PodEscalationContactsResponse,
  PodEscalationContactsUpdateRequest,
  IdentityLinkResponse,
  IdentityLinkUpdateRequest,
  IdentityAutoMatchResponse,
  UnmappedMemberResponse,
  WritebackConsentResponse,
  WritebackConsentUpdateRequest,
  TenantWritebackResponse,
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
  PodRollupResponse,
  PodTasksResponse,
  NodeKind,
  NodeTrendResponse,
  PortfolioHeatmapResponse,
  PortfolioAttentionResponse,
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
  SelfCheckinPreferenceUpdateRequest,
  StatusCorrectionRequest,
  WorkstreamFlowResponse,
  WorkstreamProgressResponse,
  WorkflowDispatchResponse,
  SyncStatusResponse,
  BrandingResponse,
  TenantLogoUploadRequest,
} from "./schema";
import { withViewingAsOf } from "./asOf";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME ?? "openprogram_csrf";
const CSRF_HEADER_NAME = import.meta.env.VITE_AUTH_CSRF_HEADER_NAME ?? "x-csrf-token";
const DEV_USER_HEADER = "x-openprogram-dev-user";
const DEV_ROLES_HEADER = "x-openprogram-dev-roles";

/** The full address a request for `path` goes to, as people should read it in an error. */
export function apiUrl(path: string): string {
  return new URL(`${API_BASE_URL}${path}`, window.location.origin).href;
}

/**
 * Who the console is acting as, for local demo tenants only.
 *
 * The backend honours these headers exclusively under the dev auth provider in
 * a local environment; anywhere else they are ignored, so sending them always
 * is safe and keeps the request path uniform.
 */
type ActingAs = { id: string; roles: string[] } | null;

let actingAs: ActingAs = null;

export function setActingAs(next: ActingAs): void {
  actingAs = next;
}

function actingAsHeaders(): Record<string, string> {
  if (!actingAs) {
    return {};
  }
  const headers: Record<string, string> = { [DEV_USER_HEADER]: actingAs.id };
  if (actingAs.roles.length > 0) {
    headers[DEV_ROLES_HEADER] = actingAs.roles.join(",");
  }
  return headers;
}

/**
 * Why changes are refused right now, or null when they are allowed.
 *
 * Set while the console views a past day. A confirm or correction sent then is
 * filed against that day, and any other change lands today while the screen
 * shows another one. The buttons are disabled too; this is the backstop. Asking
 * the graph is a question, not a change, and signing out always works.
 */
let readOnlyReason: string | null = null;

export function setReadOnlyReason(next: string | null): void {
  readOnlyReason = next;
}

const ALWAYS_ALLOWED_WRITES = ["/ask", "/api/v1/auth/"];

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
  if (
    readOnlyReason &&
    isWriteMethod(options.method) &&
    !ALWAYS_ALLOWED_WRITES.some((prefix) => path.startsWith(prefix))
  ) {
    throw new ApiError(409, readOnlyReason);
  }
  const response = await fetch(`${API_BASE_URL}${withViewingAsOf(path, options.method)}`, {
    method: options.method,
    credentials: "include",
    headers: {
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...csrfHeader(options.method),
      ...actingAsHeaders(),
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
  devUsers: () => requestJson<DevUsersResponse>("/api/v1/auth/dev-users"),
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
  podRollup: (podId: string, asOf?: string) =>
    requestJson<PodRollupResponse>(withAsOf(`/pods/${podId}/rollup`, asOf)),
  podTasks: (podId: string, asOf?: string) =>
    requestJson<PodTasksResponse>(withAsOf(`/pods/${podId}/tasks`, asOf)),
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
  /** Exec Today's headline, next drivers and top signals; times on the reader's clock. */
  portfolioAttention: (asOf?: string, programRootId?: string, timeZone?: string) =>
    requestJson<PortfolioAttentionResponse>(
      withQuery("/portfolio/attention", {
        as_of: asOf,
        program_root_id: programRootId,
        tz: timeZone,
      }),
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
  myCrossPersonRequests: (relation?: MyRequestRelation) =>
    requestJson<CrossPersonRequestsResponse>(withQuery("/me/cross-person-requests", { relation })),
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
  updateCheckinPreference: (input: SelfCheckinPreferenceUpdateRequest) =>
    requestJson<CheckinPreferenceResponse>("/me/checkin-preference", {
      method: "PUT",
      body: input,
    }),
  dispatchCheckin: (input: CheckinDispatchRequest) =>
    requestJson<WorkflowDispatchResponse>("/admin/workflows/checkin/dispatch", {
      method: "POST",
      body: input,
    }),
  syncStatus: () => requestJson<SyncStatusResponse>("/admin/ops/sync-status"),
  chatSimulatorStatus: () =>
    requestJson<ChatSimulatorStatusResponse>("/test/chat-simulator/status"),
  chatSimulatorMessages: (userId?: string) =>
    requestJson<ChatSimulatorMessagesResponse>(
      withQuery("/test/chat-simulator/messages", { user_id: userId }),
    ),
  sendChatSimulatorUserMessage: (userId: string, input: ChatSimulatorUserMessageRequest) =>
    requestJson<ChatSimulatorUserMessageResponse>(
      `/test/chat-simulator/users/${encodeURIComponent(userId)}/messages`,
      { method: "POST", body: input },
    ),
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
  podEscalationCandidates: (podId: string) =>
    requestJson<EscalationCandidateResponse[]>(`/config/pods/${podId}/escalation-candidates`),
  configMemberWritebackConsent: (memberId: string) =>
    requestJson<WritebackConsentResponse>(`/config/members/${memberId}/writeback-consent`),
  updateConfigMemberWritebackConsent: (memberId: string, body: WritebackConsentUpdateRequest) =>
    requestJson<WritebackConsentResponse>(`/config/members/${memberId}/writeback-consent`, {
      method: "PUT",
      body,
    }),
  configTenantWriteback: () => requestJson<TenantWritebackResponse>("/config/tenant/writeback"),
  branding: () => requestJson<BrandingResponse>("/config/branding"),
  uploadBrandingLogo: (body: TenantLogoUploadRequest) =>
    requestJson<BrandingResponse>("/config/branding/logo", { method: "PUT", body }),
  removeBrandingLogo: () => requestJson<void>("/config/branding/logo", { method: "DELETE" }),
  connections: () => requestJson<ConnectionResponse[]>("/config/integrations"),
  saveConnection: (connector: string, body: ConnectionUpdateRequest) =>
    requestJson<ConnectionResponse>(`/config/integrations/${encodeURIComponent(connector)}`, {
      method: "PUT",
      body,
    }),
  removeConnection: (connector: string) =>
    requestJson<void>(`/config/integrations/${encodeURIComponent(connector)}`, {
      method: "DELETE",
    }),
  testConnection: (connector: string, body?: ConnectionTestRequest) =>
    requestJson<ConnectionTestResponse>(
      `/config/integrations/${encodeURIComponent(connector)}/test`,
      { method: "POST", body },
    ),
  deliveryStages: () => requestJson<DeliveryStagesResponse>("/config/delivery/stages"),
  saveDeliveryStages: (body: DeliveryStagesUpdateRequest) =>
    requestJson<DeliveryStagesResponse>("/config/delivery/stages", { method: "PUT", body }),
  observedStatuses: () => requestJson<ObservedStatusResponse[]>("/config/delivery/statuses"),
  previewObservedStatuses: (body: DeliveryStagesUpdateRequest) =>
    requestJson<ObservedStatusResponse[]>("/config/delivery/statuses/preview", {
      method: "POST",
      body,
    }),
  projectRequirements: (projectId: string, asOf?: string, days?: number, releaseId?: string) =>
    requestJson<RequirementsResponse>(
      withQuery(`/projects/${encodeURIComponent(projectId)}/requirements`, {
        as_of: asOf,
        days: days ? String(days) : undefined,
        release_id: releaseId,
      }),
    ),
  projectDelivery: (projectId: string, asOf?: string) =>
    requestJson<ProjectDeliveryResponse>(
      withAsOf(`/projects/${encodeURIComponent(projectId)}/delivery`, asOf),
    ),
  podDelivery: (podId: string, asOf?: string) =>
    requestJson<PodDeliveryResponse>(withAsOf(`/pods/${encodeURIComponent(podId)}/delivery`, asOf)),
  setProjectDate: (projectId: string, body: DeliveryDateRequest) =>
    requestJson<CommitmentResponse>(`/projects/${encodeURIComponent(projectId)}/delivery-date`, {
      method: "PUT",
      body,
    }),
  setPodDate: (projectId: string, podId: string, body: DeliveryDateRequest) =>
    requestJson<CommitmentResponse>(
      `/projects/${encodeURIComponent(projectId)}/pods/${encodeURIComponent(podId)}/delivery-date`,
      { method: "PUT", body },
    ),
  setReleaseDate: (projectId: string, releaseId: string, body: DeliveryDateRequest) =>
    requestJson<CommitmentResponse>(
      `/projects/${encodeURIComponent(projectId)}/releases/${encodeURIComponent(releaseId)}/delivery-date`,
      { method: "PUT", body },
    ),
  escalationOverview: () => requestJson<EscalationOverviewResponse>("/config/escalation"),
  projectEscalation: (projectId: string) =>
    requestJson<EscalationMatrixResponse>(
      `/config/escalation/projects/${encodeURIComponent(projectId)}`,
    ),
  saveTenantEscalation: (body: EscalationMatrixRequest) =>
    requestJson<EscalationMatrixResponse>("/config/escalation/tenant", { method: "PUT", body }),
  saveProjectEscalation: (projectId: string, body: EscalationMatrixRequest) =>
    requestJson<EscalationMatrixResponse>(
      `/config/escalation/projects/${encodeURIComponent(projectId)}`,
      { method: "PUT", body },
    ),
  removeProjectEscalation: (projectId: string) =>
    requestJson<void>(`/config/escalation/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    }),
  gateTemplates: () => requestJson<GateTemplatesResponse>("/config/gates"),
  saveGateTemplate: (body: GateTemplateDto) =>
    requestJson<GateTemplateDto>("/config/gates", { method: "PUT", body }),
  removeGateTemplate: (templateId: string) =>
    requestJson<void>(`/config/gates/${encodeURIComponent(templateId)}`, { method: "DELETE" }),
  gateBoard: (projectId: string, asOf?: string, releaseId?: string) =>
    requestJson<GateBoardResponse>(
      withQuery(`/projects/${encodeURIComponent(projectId)}/gates`, {
        as_of: asOf,
        release_id: releaseId,
      }),
    ),
  scanGates: (projectId: string, releaseId?: string) =>
    requestJson<GateScanResponse>(
      withQuery(`/projects/${encodeURIComponent(projectId)}/gates/scan`, { release_id: releaseId }),
      { method: "POST" },
    ),
  addGateItem: (issueKey: string, body: { template_id: string; kind: string; text: string }) =>
    requestJson<GateItemResponse>(`/issues/${encodeURIComponent(issueKey)}/gate-items`, {
      method: "POST",
      body,
    }),
  confirmGateItem: (itemId: string) =>
    requestJson<GateItemResponse>(`/gate-items/${encodeURIComponent(itemId)}/confirm`, {
      method: "POST",
    }),
  dismissGateItem: (itemId: string) =>
    requestJson<GateItemResponse>(`/gate-items/${encodeURIComponent(itemId)}/dismiss`, {
      method: "POST",
    }),
  signOffGateItem: (
    itemId: string,
    body: { status: ItemStatus; evidence_url?: string | null; note?: string },
  ) =>
    requestJson<GateItemResponse>(`/gate-items/${encodeURIComponent(itemId)}/sign-off`, {
      method: "PUT",
      body,
    }),
  updateQuestion: (
    questionId: string,
    body: { confirmed?: boolean; dismissed?: boolean; status?: QuestionStatus },
  ) =>
    requestJson<TrackedQuestionResponse>(`/questions/${encodeURIComponent(questionId)}`, {
      method: "PUT",
      body,
    }),
  addQuestion: (issueKey: string, body: { asked_to: string; summary: string }) =>
    requestJson<TrackedQuestionResponse>(`/issues/${encodeURIComponent(issueKey)}/questions`, {
      method: "POST",
      body,
    }),
  releases: (projectId: string) =>
    requestJson<ReleaseResponse[]>(`/projects/${encodeURIComponent(projectId)}/releases`),
  releaseCandidates: (projectId: string) =>
    requestJson<ReleaseCandidateResponse[]>(
      `/projects/${encodeURIComponent(projectId)}/release-candidates`,
    ),
  createRelease: (projectId: string, body: ReleaseRequest) =>
    requestJson<ReleaseResponse>(`/projects/${encodeURIComponent(projectId)}/releases`, {
      method: "POST",
      body,
    }),
  removeRelease: (projectId: string, releaseId: string) =>
    requestJson<void>(
      `/projects/${encodeURIComponent(projectId)}/releases/${encodeURIComponent(releaseId)}`,
      { method: "DELETE" },
    ),
  // Day reports: everyone reads them; each report says whether the caller may
  // send it, set it up, or write today's note.
  dayReports: (projectId?: string) =>
    requestJson<DayReportResponse[]>(withQuery("/day-reports", { project_id: projectId })),
  dayReport: (reportId: string) =>
    requestJson<DayReportResponse>(`/day-reports/${encodeURIComponent(reportId)}`),
  dayReportSetup: () => requestJson<DayReportSetupResponse>("/day-reports/setup"),
  createDayReport: (body: DayReportRequest) =>
    requestJson<DayReportResponse>("/day-reports", { method: "POST", body }),
  updateDayReport: (reportId: string, body: DayReportRequest) =>
    requestJson<DayReportResponse>(`/day-reports/${encodeURIComponent(reportId)}`, {
      method: "PUT",
      body,
    }),
  removeDayReport: (reportId: string) =>
    requestJson<void>(`/day-reports/${encodeURIComponent(reportId)}`, { method: "DELETE" }),
  previewDayReport: (reportId: string) =>
    requestJson<ReportPreviewResponse>(`/day-reports/${encodeURIComponent(reportId)}/preview`),
  sendDayReport: (reportId: string) =>
    requestJson<ReportRunResponse>(`/day-reports/${encodeURIComponent(reportId)}/send`, {
      method: "POST",
    }),
  dayReportRuns: (reportId: string) =>
    requestJson<ReportRunResponse[]>(`/day-reports/${encodeURIComponent(reportId)}/runs`),
  writeDayReportNote: (reportId: string, text: string) =>
    requestJson<DayReportResponse>(`/day-reports/${encodeURIComponent(reportId)}/note`, {
      method: "PUT",
      body: { text },
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

function isWriteMethod(method?: string): boolean {
  return ["POST", "PUT", "PATCH", "DELETE"].includes((method ?? "GET").toUpperCase());
}

function csrfHeader(method?: string): Record<string, string> {
  if (!isWriteMethod(method)) {
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
