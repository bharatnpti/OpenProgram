import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";

/** One cache entry for the tenant's branding, shared by the header and the admin card. */
export const BRANDING_QUERY_KEY = ["branding"] as const;

export function useBranding() {
  return useQuery({
    queryKey: BRANDING_QUERY_KEY,
    queryFn: apiClient.branding,
    // The logo rarely changes, and changing it here invalidates this query.
    staleTime: 5 * 60_000,
  });
}
