import type { components } from "./generated";

export type NodeKind = components["schemas"]["NodeKind"];
export type EdgeKind = components["schemas"]["EdgeKind"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type AuthStatusResponse = components["schemas"]["AuthStatusResponse"];
export type LogoutResponse = components["schemas"]["LogoutResponse"];
export type GraphNodeDto = components["schemas"]["GraphNodeDto"];
export type GraphEdgeDto = components["schemas"]["GraphEdgeDto"];
export type GraphTreeDto = components["schemas"]["GraphTreeDto"];
export type ConfigNodeResponse = components["schemas"]["ConfigNodeResponse"];
export type ConfigNodeCreateRequest = components["schemas"]["ConfigNodeCreateRequest"];
export type ConfigNodeUpdateRequest = components["schemas"]["ConfigNodeUpdateRequest"];
export type ConfigEdgeResponse = components["schemas"]["ConfigEdgeResponse"];
export type DirectoryItemResponse = components["schemas"]["DirectoryItemResponse"];
export type DirectoryUserResponse = components["schemas"]["DirectoryUserResponse"];
export type DirectorySearchResponse = components["schemas"]["DirectorySearchResponse"];
export type DirectorySyncResponse = components["schemas"]["DirectorySyncResponse"];
export type MemberFromDirectoryRequest = components["schemas"]["MemberFromDirectoryRequest"];
export type ChatSimulatorMessageResponse = components["schemas"]["ChatSimulatorMessageResponse"];
export type ChatSimulatorMessagesResponse = components["schemas"]["ChatSimulatorMessagesResponse"];
export type ChatSimulatorReplyRequest = components["schemas"]["ChatSimulatorReplyRequest"];
export type ChatSimulatorReplyResponse = components["schemas"]["ChatSimulatorReplyResponse"];
export type ChatSimulatorStatusResponse = components["schemas"]["ChatSimulatorStatusResponse"];
export type StatusSource = components["schemas"]["StatusSource"];
export type Rag = components["schemas"]["Rag"];
export type EntityRefDto = components["schemas"]["EntityRefDto"];
export type RollupFactorDto = components["schemas"]["RollupFactorDto"];
export type FocusResponse = components["schemas"]["FocusResponse"];
export type MyStatusResponse = components["schemas"]["MyStatusResponse"];
export type StatusCorrectionRequest = components["schemas"]["StatusCorrectionRequest"];
export type BlockerDetailDto = components["schemas"]["BlockerDetailDto"];
export type BlockerCorrectionItemDto = components["schemas"]["BlockerCorrectionItemDto"];
export type CheckinPreferenceResponse = components["schemas"]["CheckinPreferenceResponse"];
export type CheckinPreferenceUpdateRequest =
  components["schemas"]["CheckinPreferenceUpdateRequest"];
export type PodMemberLinkRequest = components["schemas"]["PodMemberLinkRequest"];
export type ProgramProjectLinkRequest = components["schemas"]["ProgramProjectLinkRequest"];
export type MemberTaskAssignmentRequest = components["schemas"]["MemberTaskAssignmentRequest"];
export type PodBlockersResponse = components["schemas"]["PodBlockersResponse"];
export type PodCheckinsResponse = components["schemas"]["PodCheckinsResponse"];
export type ProjectProgressResponse = components["schemas"]["ProjectProgressResponse"];
export type WorkstreamProgressResponse = components["schemas"]["WorkstreamProgressResponse"];
export type ProgramTreeResponse = components["schemas"]["ProgramTreeResponse"];
export type PortfolioHeatmapResponse = components["schemas"]["PortfolioHeatmapResponse"];
export type NodeTrendResponse = components["schemas"]["NodeTrendResponse"];
export type TrendPointDto = components["schemas"]["TrendPointDto"];
export type CheckinDispatchRequest = components["schemas"]["CheckinDispatchRequest"];
export type WorkflowDispatchResponse = components["schemas"]["WorkflowDispatchResponse"];
export type RiskEvidenceDto = components["schemas"]["RiskEvidenceDto"];
export type RiskFindingResponse = components["schemas"]["RiskFindingResponse"];
export type DriftFindingResponse = components["schemas"]["DriftFindingResponse"];
export type ProjectRisksResponse = components["schemas"]["ProjectRisksResponse"];
export type PortfolioRisksResponse = components["schemas"]["PortfolioRisksResponse"];
export type CrossPersonRequestResponse = components["schemas"]["CrossPersonRequestResponse"];
export type CrossPersonRequestsResponse = components["schemas"]["CrossPersonRequestsResponse"];
export type CrossPersonRequestStatus = components["schemas"]["CrossPersonRequestStatus"];
export type CrossPersonRequestStatusUpdateRequest =
  components["schemas"]["CrossPersonRequestStatusUpdateRequest"];
export type EscalationContactDto = components["schemas"]["EscalationContactDto"];
export type PodEscalationContactsResponse = components["schemas"]["PodEscalationContactsResponse"];
export type PodEscalationContactsUpdateRequest =
  components["schemas"]["PodEscalationContactsUpdateRequest"];
export type IdentityLinkResponse = components["schemas"]["IdentityLinkResponse"];
export type IdentityLinkUpdateRequest = components["schemas"]["IdentityLinkUpdateRequest"];
export type IdentityAutoMatchResponse = components["schemas"]["IdentityAutoMatchResponse"];
export type IdentityAutoMatchMemberDto = components["schemas"]["IdentityAutoMatchMemberDto"];
export type UnmappedMemberResponse = components["schemas"]["UnmappedMemberResponse"];
export type BriefKind = components["schemas"]["BriefKind"];
export type NarrativeBriefResponse = components["schemas"]["NarrativeBriefResponse"];
export type NarrativeBriefsResponse = components["schemas"]["NarrativeBriefsResponse"];
export type WriteBackAdoptionResponse = components["schemas"]["WriteBackAdoptionResponse"];
export type WriteBackConsent = components["schemas"]["WriteBackConsent"];
export type WritebackConsentResponse = components["schemas"]["WritebackConsentResponse"];
export type WritebackConsentUpdateRequest = components["schemas"]["WritebackConsentUpdateRequest"];

export interface WorkItemFlowResponse {
  id: string;
  name: string;
  state: string;
  item_type: string;
  repo: string | null;
  branch: string | null;
  pr_id: string | null;
  workstream_ids: string[];
  age_days: number | null;
  cycle_time_days: number | null;
  last_transition_at: string | null;
}

export interface WorkstreamFlowResponse {
  workstream_id: string;
  workstream_name: string;
  as_of: string;
  active_count: number;
  features_in_flight: number;
  completed_count: number;
  stale_count: number;
  abandoned_count: number;
  avg_cycle_time_days: number | null;
  avg_pr_age_days: number | null;
  work_items: WorkItemFlowResponse[];
}

export interface WorkstreamFlowSummaryResponse {
  workstream_id: string;
  workstream_name: string;
  active_count: number;
  features_in_flight: number;
  completed_count: number;
  stale_count: number;
  abandoned_count: number;
  avg_cycle_time_days: number | null;
  avg_pr_age_days: number | null;
}

export interface PortfolioFlowResponse {
  as_of: string;
  active_count: number;
  features_in_flight: number;
  completed_count: number;
  stale_count: number;
  abandoned_count: number;
  avg_cycle_time_days: number | null;
  avg_pr_age_days: number | null;
  workstreams: WorkstreamFlowSummaryResponse[];
}

export interface PortfolioFeedItemResponse {
  source: string;
  kind: string;
  summary: string;
  entity_ref: {
    tenant_id: string;
    kind: string;
    id: string;
  };
  observed_at: string;
  details: Record<string, unknown>;
}

export interface PortfolioFeedResponse {
  since: string | null;
  as_of: string;
  items: PortfolioFeedItemResponse[];
}

export interface AskRequest {
  question: string;
  as_of?: string | null;
}

export interface AskResponse {
  answer: string;
  references: string[];
  tools_used: string[];
  trace_id: string;
}
