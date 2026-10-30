import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";

/** The levels whose health over the last days the trend read gives. */
export type TrendLevel = "program" | "project" | "pod";

/** The trend read of one programme, project or pod over 30 days, cached by level and id. */
export function useHealthTrend(level: TrendLevel, id: string, enabled = true) {
  return useQuery({
    queryKey: ["portfolio", "trend", level, id],
    queryFn: () => apiClient.nodeTrend(level, id, { windowDays: 30 }),
    enabled: enabled && id !== "",
  });
}
