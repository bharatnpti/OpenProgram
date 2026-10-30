import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { ReadinessBoardResponse } from "../../api/schema";
import { sameOnEveryDay } from "../../app/queryCache";

/**
 * A pod's own readiness board (`GET /pods/{id}/readiness`), read as it stands
 * now whatever day is shown. Under the project's key, so an action on a finding
 * refreshes the project's board and the pod's at once.
 */
export function usePodReadiness(projectId: string, podId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["readiness", projectId, "pod", podId],
    queryFn: () => apiClient.podReadiness(podId),
    enabled: enabled && podId !== "",
    ...sameOnEveryDay,
  });
}

/** Whether a pod's readiness board has anything to draw: set up, or something found. */
export function hasPodReadiness(data: ReadinessBoardResponse | undefined): boolean {
  return data !== undefined && (data.agent.enabled || data.findings.length > 0);
}
