import assert from "node:assert/strict";
import { test } from "node:test";

import type { ScopeDeliveryResponse } from "../../api/schema";
import {
  committedBy,
  compactForecast,
  forecastGap,
  historyWords,
  pastDue,
  teamWords,
  verdictChip,
  verdictRag,
  verdictWeight,
} from "./dateStripWords.ts";

const scope = (over: Partial<ScopeDeliveryResponse> = {}): ScopeDeliveryResponse => ({
  scope_kind: "project",
  scope_id: "project-checkout",
  project_id: "project-checkout",
  name: "Checkout Revamp",
  commitment: {
    target_date: "2026-10-30",
    original_date: "2026-10-23",
    times_moved: 1,
    moved_days: 7,
    changes: [
      {
        target_date: "2026-10-23",
        changed_at: "2026-09-14T09:00:00Z",
        changed_by: "U1003",
        changed_by_name: "Mina Patel",
        note: "",
      },
      {
        target_date: "2026-10-30",
        changed_at: "2026-10-07T09:00:00Z",
        changed_by: "U1003",
        changed_by_name: "Mina Patel",
        note: "scope grew",
      },
    ],
  } as ScopeDeliveryResponse["commitment"],
  target: "2026-10-30",
  target_source: "committed",
  jira_release_date: null,
  history: {
    p50: "2026-11-04",
    p85: "2026-11-12",
    remaining: 17,
    unit: "requirements",
    sample_days: 21,
    completed_in_sample: 6,
    reason: null,
  },
  team: { latest: "2026-11-03", latest_key: "CHK-103", dated: 14, undated: 3 },
  verdict: "off_track",
  reasons: [],
  total: 24,
  open: 17,
  ...over,
});

test("the forecast's gap to the committed date: late in danger, only the 85% late in warning", () => {
  assert.deepEqual(forecastGap("2026-10-30", "2026-11-11", "2026-11-20"), {
    tone: "danger",
    text: "+12 days",
  });
  assert.deepEqual(forecastGap("2026-10-30", "2026-10-31", null), {
    tone: "danger",
    text: "+1 day",
  });
  assert.deepEqual(forecastGap("2026-10-30", "2026-10-28", "2026-11-04"), {
    tone: "warning",
    text: "85%: +5 days",
  });
  assert.deepEqual(forecastGap("2026-10-30", "2026-10-20", "2026-10-30"), {
    tone: "success",
    text: "on time",
  });
  assert.equal(forecastGap(null, "2026-10-20", null), null, "no committed date, no gap");
  assert.equal(forecastGap("2026-10-30", null, null), null, "no forecast, no gap");
});

test("too little history says how much there is against what a forecast needs", () => {
  const short = {
    ...scope().history,
    p50: null,
    p85: null,
    sample_days: 2,
    reason: "Only 2 working days of history; a forecast needs 10.",
  };
  assert.equal(historyWords(short), "2 of 10 working days");
  assert.equal(
    historyWords({ ...short, reason: null, sample_days: 1 }),
    "1 working day of history",
  );
});

test("under the committed date: who set it, when, and how often it moved", () => {
  assert.equal(committedBy(scope()), "by Mina Patel · Wed 7 Oct · moved once, +7 days");
  assert.equal(
    committedBy(
      scope({ target_source: "jira_release", commitment: { ...scope().commitment, changes: [] } }),
    ),
    "from the Jira release",
  );
  assert.equal(committedBy(scope({ target: null })), null);
  assert.equal(
    committedBy(
      scope({
        commitment: { ...scope().commitment, times_moved: 0, moved_days: null, changes: [] },
      }),
    ),
    null,
  );
});

test("the team's date names its item and how many have none", () => {
  assert.equal(teamWords(scope().team), "CHK-103 · 3 without a date");
  assert.equal(
    teamWords({ latest: "2026-10-15", latest_key: null, dated: 4, undated: 0 }),
    "latest of 4 dated",
  );
});

test("the verdict colours the edge; nothing counted is grey and says so", () => {
  assert.equal(verdictRag("off_track", true), "red");
  assert.equal(verdictRag("at_risk", true), "amber");
  assert.equal(verdictRag("on_track", true), "green");
  assert.equal(verdictRag("not_enough_data", true), "unknown");
  assert.equal(verdictRag("off_track", false), "unknown");
  assert.deepEqual(verdictChip(scope()), { label: "Off track", tone: "danger" });
  assert.deepEqual(verdictChip(scope({ total: 0 })), {
    label: "Nothing in scope",
    tone: "neutral",
  });
});

test("worst first: off track, at risk, can't judge, no date, on track, done", () => {
  const order = (
    ["done", "on_track", "no_date", "not_enough_data", "at_risk", "off_track"] as const
  )
    .map((verdict) => scope({ verdict }))
    .sort((a, b) => verdictWeight(b) - verdictWeight(a))
    .map((s) => s.verdict);
  assert.deepEqual(order, [
    "off_track",
    "at_risk",
    "not_enough_data",
    "no_date",
    "on_track",
    "done",
  ]);
});

test("a task is past due once its day has gone by, unless the tracker has it done", () => {
  assert.equal(pastDue("2026-10-05", "2026-10-06"), true);
  assert.equal(pastDue("2026-10-06", "2026-10-06"), false, "due today is not past");
  assert.equal(pastDue("2026-10-05", "2026-10-06", "Done"), false);
  assert.equal(pastDue("2026-10-05", "2026-10-06", " closed "), false);
  assert.equal(pastDue("2026-10-05", "2026-10-06", "In Review"), true);
  assert.equal(pastDue(null, "2026-10-06"), false);
  assert.equal(pastDue("2026-10-05", null), false);
});

test("the compact strip's forecast in a few words", () => {
  assert.equal(compactForecast(scope()), "forecast: Wed 4 Nov, +5 days");
  const short = { ...scope().history, p50: null, p85: null };
  assert.equal(
    compactForecast(scope({ history: short })),
    "not enough history · team says Tue 3 Nov",
    "the team's date stands in for a forecast",
  );
  assert.equal(
    compactForecast(
      scope({ history: short, team: { latest: null, latest_key: null, dated: 0, undated: 4 } }),
    ),
    "forecast: not enough history",
  );
  assert.equal(compactForecast(scope({ total: 0 })), "nothing to forecast");
});
