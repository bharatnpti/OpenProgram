import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { PortfolioFeedItemResponse } from "../../api/schema";
import { feedByDay, feedSourceWords } from "./feedWords.ts";

const item = (summary: string, observed_at: string): PortfolioFeedItemResponse => ({
  source: "vcs",
  kind: "pull_request_opened",
  summary,
  entity_ref: null,
  observed_at,
  details: {},
  person_name: null,
});

describe("the last 7 days", () => {
  test("each source in a word or two", () => {
    assert.equal(feedSourceWords("vcs"), "Git");
    assert.equal(feedSourceWords("issue_tracker"), "Jira");
    assert.equal(feedSourceWords("check_in"), "Check-in");
    assert.equal(feedSourceWords("some_new_source"), "Some new source");
  });

  test("by the reader's day, newest day first and newest first within it", () => {
    const days = feedByDay(
      [
        item("a", "2026-10-13T09:00:00Z"),
        item("b", "2026-10-14T23:30:00Z"),
        item("c", "2026-10-14T08:00:00Z"),
      ],
      // A reader two hours east: 23:30 UTC is the next day for them.
      (instant) => new Date(Date.parse(instant) + 2 * 3600_000).toISOString().slice(0, 10),
    );
    assert.deepEqual(
      days.map((day) => [day.day, day.items.map((entry) => entry.summary)]),
      [
        ["2026-10-15", ["b"]],
        ["2026-10-14", ["c"]],
        ["2026-10-13", ["a"]],
      ],
    );
    assert.deepEqual(
      feedByDay([], (instant) => instant),
      [],
    );
  });
});
