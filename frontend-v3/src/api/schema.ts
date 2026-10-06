import type { components } from "./generated";

type Schemas = components["schemas"];

// Auth and identity
export type AuthStatusResponse = Schemas["AuthStatusResponse"];
export type DevUserResponse = Schemas["DevUserResponse"];
export type DevUsersResponse = Schemas["DevUsersResponse"];
export type LogoutResponse = Schemas["LogoutResponse"];

// Directory
export type DirectoryItemResponse = Schemas["DirectoryItemResponse"];
export type Rag = Schemas["Rag"];

// Day reports (Daily view)
export type DayReportResponse = Schemas["DayReportResponse"];
export type DayReportNoteResponse = Schemas["DayReportNoteResponse"];
export type ReportAudienceResponse = Schemas["ReportAudienceResponse"];
export type ReportPreviewResponse = Schemas["ReportPreviewResponse"];
export type ReportSectionResponse = Schemas["ReportSectionResponse"];
export type ReportRunResponse = Schemas["ReportRunResponse"];

// Delivery date and forecast
export type ProjectDeliveryResponse = Schemas["ProjectDeliveryResponse"];
export type ScopeDeliveryResponse = Schemas["ScopeDeliveryResponse"];
export type CommitmentResponse = Schemas["CommitmentResponse"];
export type Verdict = Schemas["Verdict"];

// Requirements by delivery stage
export type DeliveryStage = Schemas["DeliveryStage"];
export type RequirementsResponse = Schemas["RequirementsResponse"];
export type RequirementResponse = Schemas["RequirementResponse"];
export type RequirementStageCountResponse = Schemas["RequirementStageCountResponse"];
export type RequirementTimelinePointResponse = Schemas["RequirementTimelinePointResponse"];

// Acceptance gates and questions
export type GateBoardResponse = Schemas["GateBoardResponse"];
export type GateTemplateDto = Schemas["GateTemplateDto"];
export type IssueGatesResponse = Schemas["IssueGatesResponse"];
export type GateEvaluationResponse = Schemas["GateEvaluationResponse"];
export type GateState = Schemas["GateState"];
export type TrackedQuestionResponse = Schemas["TrackedQuestionResponse"];
export type QuestionStatus = Schemas["QuestionStatus"];

// Risks and escalation
export type ProjectRisksResponse = Schemas["ProjectRisksResponse"];
export type RiskFindingResponse = Schemas["RiskFindingResponse"];
export type DriftFindingResponse = Schemas["DriftFindingResponse"];
export type EscalationMatrixResponse = Schemas["EscalationMatrixResponse"];
