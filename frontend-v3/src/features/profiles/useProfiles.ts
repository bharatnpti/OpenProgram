import { useQueries, useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { MemberProfileResponse } from "../../api/schema";

/** Each pod's people's profiles, by pod then member: one read per pod, shared with the pod panel. */
export function usePodsProfiles(podIds: string[]) {
  const queries = useQueries({
    queries: podIds.map((podId) => ({
      queryKey: ["pod", podId, "profiles"],
      queryFn: () => apiClient.podProfiles(podId),
    })),
  });
  const byPod: Record<string, Record<string, MemberProfileResponse>> = {};
  podIds.forEach((podId, index) => {
    byPod[podId] = Object.fromEntries(
      (queries[index]?.data?.members ?? []).map((member) => [member.member_id, member]),
    );
  });
  return byPod;
}

/** The tenant's profile catalog. Everyone reads it; it changes rarely. */
export function useProfileCatalog() {
  return useQuery({
    queryKey: ["profiles", "catalog"],
    queryFn: () => apiClient.profiles(),
    staleTime: 5 * 60_000,
  });
}

/** A pod's people with their role and profile, and whether the caller sets them. */
export function usePodProfiles(podId: string, enabled = true) {
  return useQuery({
    queryKey: ["pod", podId, "profiles"],
    queryFn: () => apiClient.podProfiles(podId),
    enabled: enabled && podId !== "",
  });
}

/** Profiles nobody checked yet, with suggestions: every pod the caller sets, or one. */
export function useProfileReview(podId?: string, enabled = true) {
  return useQuery({
    queryKey: ["profiles", "review", podId ?? "all"],
    queryFn: () => apiClient.profileReview(podId),
    enabled,
  });
}
