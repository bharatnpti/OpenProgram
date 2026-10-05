import type { components } from "./generated";

export type NodeKind = components["schemas"]["NodeKind"];
export type EdgeKind = components["schemas"]["EdgeKind"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type AuthStatusResponse = components["schemas"]["AuthStatusResponse"];
export type DevUserResponse = components["schemas"]["DevUserResponse"];
export type DevUsersResponse = components["schemas"]["DevUsersResponse"];
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
export type ChatSimulatorUserMessageRequest =
  components["schemas"]["ChatSimulatorUserMessageRequest"];
export type ChatSimulatorUserMessageResponse =
  components["schemas"]["ChatSimulatorUserMessageResponse"];
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
export type CheckinDefaultsResponse = components["schemas"]["CheckinDefaultsResponse"];
export type CheckInPreferenceField = components["schemas"]["CheckInPreferenceField"];
export type CheckinPreferenceUpdateRequest =
  components["schemas"]["CheckinPreferenceUpdateRequest"];
export type SelfCheckinPreferenceUpdateRequest =
  components["schemas"]["SelfCheckinPreferenceUpdateRequest"];
export type PodMemberLinkRequest = components["schemas"]["PodMemberLinkRequest"];
export type ProgramProjectLinkRequest = components["schemas"]["ProgramProjectLinkRequest"];
export type MemberTaskAssignmentRequest = components["schemas"]["MemberTaskAssignmentRequest"];
export type PodBlockersResponse = components["schemas"]["PodBlockersResponse"];
export type PodCheckinsResponse = components["schemas"]["PodCheckinsResponse"];
export type PodRollupResponse = components["schemas"]["PodRollupResponse"];
export type PodTasksResponse = components["schemas"]["PodTasksResponse"];
export type PodTaskDto = components["schemas"]["PodTaskDto"];
export type ProjectProgressResponse = components["schemas"]["ProjectProgressResponse"];
export type WorkstreamProgressResponse = components["schemas"]["WorkstreamProgressResponse"];
export type ProgramTreeResponse = components["schemas"]["ProgramTreeResponse"];
export type PortfolioHeatmapResponse = components["schemas"]["PortfolioHeatmapResponse"];
export type PortfolioAttentionResponse = components["schemas"]["PortfolioAttentionResponse"];
export type AttentionSignalDto = components["schemas"]["AttentionSignalDto"];
export type AttentionLinkDto = components["schemas"]["AttentionLinkDto"];
export type NodeTrendResponse = components["schemas"]["NodeTrendResponse"];
export type TrendPointDto = components["schemas"]["TrendPointDto"];
export type CheckinDispatchRequest = components["schemas"]["CheckinDispatchRequest"];
export type WorkflowDispatchResponse = components["schemas"]["WorkflowDispatchResponse"];
export type SyncStatusResponse = components["schemas"]["SyncStatusResponse"];
export type SyncSourceStatusResponse = components["schemas"]["SyncSourceStatusResponse"];
export type SyncTargetStatusResponse = components["schemas"]["SyncTargetStatusResponse"];
export type SyncHealth = components["schemas"]["SyncHealth"];
export type SyncSource = components["schemas"]["SyncSource"];
export type SyncTargetOrigin = components["schemas"]["SyncTargetOrigin"];
export type RiskEvidenceDto = components["schemas"]["RiskEvidenceDto"];
export type RiskFindingResponse = components["schemas"]["RiskFindingResponse"];
export type DriftFindingResponse = components["schemas"]["DriftFindingResponse"];
export type ProjectRisksResponse = components["schemas"]["ProjectRisksResponse"];
export type PortfolioRisksResponse = components["schemas"]["PortfolioRisksResponse"];
export type CrossPersonRequestResponse = components["schemas"]["CrossPersonRequestResponse"];
export type CrossPersonRequestsResponse = components["schemas"]["CrossPersonRequestsResponse"];
export type CrossPersonRequestStatus = components["schemas"]["CrossPersonRequestStatus"];
export type MyRequestRelation = components["schemas"]["MyRequestRelation"];
export type CrossPersonRequestStatusUpdateRequest =
  components["schemas"]["CrossPersonRequestStatusUpdateRequest"];
export type EscalationContactDto = components["schemas"]["EscalationContactDto"];
export type EscalationContactUpdateDto = components["schemas"]["EscalationContactUpdateDto"];
export type EscalationCandidateResponse = components["schemas"]["EscalationCandidateResponse"];
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
export type TenantWritebackResponse = components["schemas"]["TenantWritebackResponse"];
export type WriteBackGateSource = components["schemas"]["WriteBackGateSource"];
export type BrandingResponse = components["schemas"]["BrandingResponse"];
export type ConnectionResponse = components["schemas"]["ConnectionResponse"];
export type ConnectorFieldDto = components["schemas"]["ConnectorFieldDto"];
export type ConnectorFieldOptionDto = components["schemas"]["ConnectorFieldOptionDto"];
export type FieldConditionDto = components["schemas"]["FieldConditionDto"];
export type FieldKind = components["schemas"]["FieldKind"];
export type ConnectorPurpose = components["schemas"]["ConnectorPurpose"];
export type ConnectionUpdateRequest = components["schemas"]["ConnectionUpdateRequest"];
export type ConnectionTestRequest = components["schemas"]["ConnectionTestRequest"];
export type ConnectionTestResponse = components["schemas"]["ConnectionTestResponse"];
export type DeliveryStage = components["schemas"]["DeliveryStage"];
export type DeliveryStagesResponse = components["schemas"]["DeliveryStagesResponse"];
export type DeliveryStagesUpdateRequest = components["schemas"]["DeliveryStagesUpdateRequest"];
export type ObservedStatusResponse = components["schemas"]["ObservedStatusResponse"];
export type RequirementsResponse = components["schemas"]["RequirementsResponse"];
export type RequirementResponse = components["schemas"]["RequirementResponse"];
export type RequirementStageCountResponse = components["schemas"]["RequirementStageCountResponse"];
export type RequirementTimelinePointResponse =
  components["schemas"]["RequirementTimelinePointResponse"];
export type RequirementMoveResponse = components["schemas"]["RequirementMoveResponse"];
export type DayReportRequest = components["schemas"]["DayReportRequest"];
export type DayReportResponse = components["schemas"]["DayReportResponse"];
export type ReportDestinationDto = components["schemas"]["ReportDestinationDto"];
export type DestinationKind = components["schemas"]["DestinationKind"];
export type ReportRunResponse = components["schemas"]["ReportRunResponse"];
export type ReportPreviewResponse = components["schemas"]["ReportPreviewResponse"];
export type ReportDestinationOptionResponse =
  components["schemas"]["ReportDestinationOptionResponse"];
export type ProjectDeliveryResponse = components["schemas"]["ProjectDeliveryResponse"];
export type ScopeDeliveryResponse = components["schemas"]["ScopeDeliveryResponse"];
export type CommitmentResponse = components["schemas"]["CommitmentResponse"];
export type DeliveryDateRequest = components["schemas"]["DeliveryDateRequest"];
export type PodDeliveryResponse = components["schemas"]["PodDeliveryResponse"];
export type ReleaseResponse = components["schemas"]["ReleaseResponse"];
export type ReleaseRequest = components["schemas"]["ReleaseRequest"];
export type ReleaseCandidateResponse = components["schemas"]["ReleaseCandidateResponse"];
export type Verdict = components["schemas"]["Verdict"];
export type ReleaseMatchKind = components["schemas"]["ReleaseMatchKind"];
export type GateTemplateDto = components["schemas"]["GateTemplateDto"];
export type ItemKindDto = components["schemas"]["ItemKindDto"];
export type GateTemplatesResponse = components["schemas"]["GateTemplatesResponse"];
export type GateBoardResponse = components["schemas"]["GateBoardResponse"];
export type IssueGatesResponse = components["schemas"]["IssueGatesResponse"];
export type GateEvaluationResponse = components["schemas"]["GateEvaluationResponse"];
export type GateItemResponse = components["schemas"]["GateItemResponse"];
export type GateScanResponse = components["schemas"]["GateScanResponse"];
export type GateState = components["schemas"]["GateState"];
export type ItemStatus = components["schemas"]["ItemStatus"];
export type ItemSource = components["schemas"]["ItemSource"];
export type TrackedQuestionResponse = components["schemas"]["TrackedQuestionResponse"];
export type QuestionStatus = components["schemas"]["QuestionStatus"];
export type Role = components["schemas"]["Role"];
export type ContactSource = components["schemas"]["ContactSource"];
export type MatrixSource = components["schemas"]["MatrixSource"];
export type NeedType = components["schemas"]["NeedType"];
export type EscalationLevelDto = components["schemas"]["EscalationLevelDto"];
export type EscalationMatrixRequest = components["schemas"]["EscalationMatrixRequest"];
export type EscalationMatrixResponse = components["schemas"]["EscalationMatrixResponse"];
export type EscalationOverviewResponse = components["schemas"]["EscalationOverviewResponse"];
export type ProjectDayReportResponse = components["schemas"]["ProjectDayReportResponse"];
export type DayReportNoteResponse = components["schemas"]["DayReportNoteResponse"];
export type TenantLogoResponse = components["schemas"]["TenantLogoResponse"];
export type TenantLogoUploadRequest = components["schemas"]["TenantLogoUploadRequest"];

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
  /** Who entity_ref is, by name, when it is a person; never a raw chat id. */
  person_name?: string | null;
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

/** A reference with the words a reader knows it by; no label when no node matches. */
export interface AskSourceResponse {
  id: string;
  kind?: NodeKind | null;
  label?: string | null;
}

export interface AskResponse {
  answer: string;
  references: string[];
  tools_used: string[];
  trace_id: string;
  /** The references again, in order, each labelled. Absent from older servers. */
  sources?: AskSourceResponse[];
}
