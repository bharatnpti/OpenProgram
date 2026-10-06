import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "../../api/client";
import { sameOnEveryDay } from "../../app/queryCache";
import { MY_CHECKIN_PREFERENCE_KEY } from "./schedule";

/**
 * The person's own check-in preference (`GET /me/checkin-preference`), read
 * only once something asks for it. The same on every viewed day.
 */
export function useMyCheckinPreference(enabled: boolean) {
  return useQuery({
    queryKey: MY_CHECKIN_PREFERENCE_KEY,
    queryFn: () => apiClient.checkinPreference(),
    enabled,
    retry: (failureCount, error) => !isNoCheckin(error) && failureCount < 1,
    staleTime: 60_000,
    ...sameOnEveryDay,
  });
}

/**
 * No member record (404: check-ins go only to members) or no own-work scope
 * (403): nobody asks this person, which is not an error to show as one.
 */
export function isNoCheckin(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 403);
}
