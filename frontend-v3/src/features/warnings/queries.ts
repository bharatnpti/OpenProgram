import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";

/**
 * Every readable project's early warnings (`GET /portfolio/warnings`): the one
 * read All projects' Early warnings, its badge and Today's count all say, so
 * they say one number.
 */
export function usePortfolioWarnings(enabled = true) {
  return useQuery({
    queryKey: ["portfolio", "warnings", ""],
    queryFn: () => apiClient.portfolioWarnings({}),
    enabled,
  });
}

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
