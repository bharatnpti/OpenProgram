import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";

/**
 * The heat map read for a programme: each node's colour today and why. One
 * key, which the names index (app/directory.ts `useNames`) shares, so the heat
 * view, the programme's health and People & roles ask for it once.
 */
export function useHeatmap(programId: string, enabled = true) {
  return useQuery({
    queryKey: ["portfolio", "heatmap", programId],
    queryFn: () => apiClient.portfolioHeatmap(undefined, programId),
    enabled: enabled && programId !== "",
  });
}
