import type {
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

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "x-correlation-id": crypto.randomUUID(),
    },
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
  portfolioHeatmap: (asOf?: string) =>
    requestJson<PortfolioHeatmapResponse>(withAsOf("/portfolio/heatmap", asOf)),
};

function withAsOf(path: string, asOf?: string): string {
  if (!asOf) {
    return path;
  }
  return `${path}?as_of=${encodeURIComponent(asOf)}`;
}
