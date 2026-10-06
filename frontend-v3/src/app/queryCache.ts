import { hashKey, type QueryKey } from "@tanstack/react-query";

import { currentViewingAsOf } from "../api/asOf";

/**
 * The cache key of every query, with the day it was read for.
 *
 * Reads get the viewing date from the API client (`api/asOf.ts`), not from
 * their query keys, so without this a past day's answer would be cached under
 * today's key and shown again on today. Today keeps the plain key, so code that
 * reads or sets cached data today behaves exactly as before. Invalidating by a
 * key prefix still reaches every day's copy.
 */
export function viewingDayQueryHash(queryKey: QueryKey): string {
  const day = currentViewingAsOf();
  return day ? hashKey([...queryKey, { viewingAsOf: day }]) : hashKey(queryKey);
}

/**
 * For a query whose answer is the same whatever day is viewed, such as the
 * tenant's logo: one cache entry, not one per day, so it doesn't refetch and
 * flicker when the day changes.
 */
export const sameOnEveryDay = { queryKeyHashFn: hashKey } as const;

/**
 * The tenant's branding (`GET /config/branding`), read by the header on every
 * screen. Whatever changes the logo invalidates this key.
 */
export const BRANDING_QUERY_KEY = ["branding"] as const;
