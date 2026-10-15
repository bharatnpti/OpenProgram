import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { useViewingDate } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { formatDay, formatTime } from "../../lib/format";
import { localIso } from "../../lib/viewingDate";
import { VizPanel } from "../portfolio/VizPanel";
import { feedByDay, feedSourceWords } from "./feedWords";

/** The newest items the panel lists; the read returns at most this many. */
const FEED_LIMIT = 50;

/**
 * The last 7 days across the portfolio (`GET /portfolio/feed`): check-ins,
 * Jira moves, merge requests, warnings and requests, newest first, by day. An
 * executive reads it with each person said as their team, never a name (the
 * server's rule). It is the activity up to now, so on a past day it says so.
 */
export function RecentActivity() {
  const { chosen } = useViewingDate();
  const feed = useQuery({
    queryKey: ["portfolio", "feed"],
    queryFn: () => apiClient.portfolioFeed(null, FEED_LIMIT),
  });
  const days = feedByDay(feed.data?.items ?? [], (instant) => localIso(new Date(instant)));
  return (
    <VizPanel
      title="Last 7 days"
      titleId="recent-activity-h"
      note={chosen ? "Up to now, not the day shown" : "Newest first"}
    >
      <PanelState
        isLoading={feed.isLoading}
        error={feed.error}
        onRetry={() => void feed.refetch()}
        isEmpty={days.length === 0}
        emptyText="Nothing was recorded across the portfolio in the last 7 days."
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          {days.map((day) => (
            <section key={day.day} aria-label={formatDay(day.day)}>
              <h4 className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
                {formatDay(day.day)}
              </h4>
              <ul>
                {day.items.map((item, index) => (
                  <li
                    key={`${item.observed_at}-${index}`}
                    className="flex min-w-0 items-baseline gap-3 border-t border-grey-border py-2 text-[14px] first:border-t-0"
                  >
                    <span className="w-[44px] flex-none text-[12px] text-grey-secondary tabular-nums">
                      {formatTime(item.observed_at)}
                    </span>
                    <span className="min-w-0 flex-1 text-grey-body break-words">
                      {item.summary}
                    </span>
                    <span className="flex-none text-[12px] font-bold text-grey-secondary">
                      {feedSourceWords(item.source)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </PanelState>
    </VizPanel>
  );
}
