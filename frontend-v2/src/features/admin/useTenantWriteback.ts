import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";

// The tenant-wide switch that every member's consent sits behind. The console
// only reads it: switching write-back on is an operator decision, not a toggle.
export function useTenantWriteback(enabled = true) {
  return useQuery({
    queryKey: ["config", "tenant-writeback"],
    queryFn: () => apiClient.configTenantWriteback(),
    enabled,
  });
}
