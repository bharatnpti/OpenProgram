import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";

/**
 * Early warnings in one read (`GET /portfolio/warnings`): every readable
 * project's, one project's, or one pod's part of it. The key starts as the
 * index's does, so one change refreshes every level's read.
 */
export function useWarnings(
  scope: { projectId?: string | null; podId?: string | null },
  enabled = true,
) {
  const projectId = scope.projectId ?? null;
  const podId = scope.podId ?? null;
  return useQuery({
    queryKey: ["portfolio", "warnings", projectId ?? "", podId ?? ""],
    queryFn: () => apiClient.portfolioWarnings({ projectId, podId }),
    enabled,
  });
}

/** Scope control in one read: with `podId`, the pod's part of each project it works on. */
export function useScopeRead(
  scope: { projectId?: string | null; podId?: string | null },
  enabled = true,
) {
  const projectId = scope.projectId ?? null;
  const podId = scope.podId ?? null;
  return useQuery({
    queryKey: ["portfolio", "scope", projectId ?? "", podId ?? ""],
    queryFn: () => apiClient.portfolioScope({ projectId, podId }),
    enabled,
  });
}
