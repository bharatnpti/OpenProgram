import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { PortfolioDeliveryResponse, VerdictFilter } from "../../api/schema";

/** How many projects one page asks for; "Show more" asks for the next. */
export const PAGE = 50;

/**
 * Will we make the date?, a page at a time: one bulk read per page, from
 * the kept forecast rows. The viewing day travels with every read (api/asOf.ts).
 */
export function usePortfolio(filters: {
  q: string;
  verdicts: readonly VerdictFilter[];
  program: string | null;
}) {
  return useInfiniteQuery({
    queryKey: ["portfolio", "delivery", filters.q, [...filters.verdicts].sort(), filters.program],
    initialPageParam: 0,
    // A new filter keeps the tiles and the list on screen until its answer comes.
    placeholderData: keepPreviousData,
    queryFn: ({ pageParam }) =>
      apiClient.portfolioDelivery({
        q: filters.q || undefined,
        verdicts: filters.verdicts,
        programId: filters.program,
        offset: pageParam,
        limit: PAGE,
      }),
    getNextPageParam: (last: PortfolioDeliveryResponse) => {
      const next = last.offset + last.projects.length;
      return next < last.total ? next : undefined;
    },
  });
}
