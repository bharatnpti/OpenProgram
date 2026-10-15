// The last 7 days' activity in words, pure: what each source is called, and
// the items by day, newest first. Type imports, and runtime imports by their
// .ts path, so `node --test` runs it as written.
import type { PortfolioFeedItemResponse } from "../../api/schema";

/** Where an item came from, in a word or two. */
export function feedSourceWords(source: string): string {
  const known: Record<string, string> = {
    check_in: "Check-in",
    checkin: "Check-in",
    vcs: "Git",
    issue_tracker: "Jira",
    risk: "Warning",
    request: "Request",
    calendar: "Calendar",
  };
  return known[source] ?? source.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}

export type FeedDay = { day: string; items: PortfolioFeedItemResponse[] };

/**
 * The items by the day they were seen, on the reader's calendar (`dayOf`
 * turns an instant into its YYYY-MM-DD there), newest day first and newest
 * first within it.
 */
export function feedByDay(
  items: readonly PortfolioFeedItemResponse[],
  dayOf: (instant: string) => string,
): FeedDay[] {
  const days = new Map<string, PortfolioFeedItemResponse[]>();
  [...items]
    .sort((a, b) => b.observed_at.localeCompare(a.observed_at))
    .forEach((item) => {
      const day = dayOf(item.observed_at);
      days.set(day, [...(days.get(day) ?? []), item]);
    });
  return [...days.entries()].map(([day, list]) => ({ day, items: list }));
}
