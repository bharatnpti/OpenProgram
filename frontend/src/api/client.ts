import type {
  CheckinPreferenceResponse,
  CheckinPreferenceUpdateRequest,
  FocusResponse,
  GraphTreeDto,
  HealthResponse,
  PodBlockersResponse,
  PodCheckinsResponse,
  PortfolioHeatmapResponse,
  ProgramTreeResponse,
  ProjectProgressResponse,
  ReadyResponse,
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
  return (await response.json()) as T;
}

export const apiClient = {
  health: () => requestJson<HealthResponse>("/health"),
  ready: () => requestJson<ReadyResponse>("/ready"),
  programTree: (programId: string) =>
    requestJson<GraphTreeDto>(`/graph/programs/${programId}/tree`),
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
