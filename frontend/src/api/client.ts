import type { GraphTreeDto, HealthResponse, ReadyResponse } from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

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
};
