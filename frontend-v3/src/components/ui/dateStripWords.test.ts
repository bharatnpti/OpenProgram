import assert from "node:assert/strict";
import { test } from "node:test";

import type { ScopeDeliveryResponse } from "../../api/schema";
import {
  committedBy,
  compactForecast,
  compactForecastParts,
  compactParts,
  forecastGap,
  forecastTone,
  historyWords,
  missingDate,
  pastDue,
  teamTone,
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
    needed_days: 10,
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

test("the working days a forecast needs are the tenant's, as the server says", () => {
  const short = { ...scope().history, p50: null, p85: null, sample_days: 3 };
  assert.equal(
    historyWords({
      ...short,
      needed_days: 5,
      reason: "Only 3 working days of history; a forecast needs 5.",
    }),
    "3 of 5 working days",
  );
  assert.equal(
    historyWords({
      ...short,
      needed_days: 20,
      reason: "Only 3 working days of history; a forecast needs 20.",
    }),
    "3 of 20 working days",
  );
  // Enough history, nothing finished in it: the days it has, not a count against the minimum.
  assert.equal(
    historyWords({
      ...short,
      sample_days: 12,
      needed_days: 5,
      reason: "Nothing reached production in the last 12 working days.",
    }),
    "12 working days of history",
  );
  // No history at all says so in the reason; the cell gives the plain count.
  assert.equal(
    historyWords({
      ...short,
      sample_days: 0,
      needed_days: 20,
      reason: "No history yet: a forecast needs 20 working days of daily snapshots.",
    }),
    "0 working days of history",
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

test("the forecast's colour follows the server's verdict rule against the committed date", () => {
  // core/domain/forecast.py verdict: p85 <= target on track, p50 <= target at risk, else off track.
  assert.equal(forecastTone("2026-10-30", "2026-10-20", "2026-10-30"), "success", "85% on the day");
  assert.equal(forecastTone("2026-10-30", "2026-10-20", "2026-10-26"), "success");
  assert.equal(forecastTone("2026-10-30", "2026-10-30", "2026-11-04"), "warning", "50% on the day");
  assert.equal(forecastTone("2026-10-30", "2026-10-28", "2026-11-04"), "warning");
  assert.equal(forecastTone("2026-10-30", "2026-10-31", "2026-11-12"), "danger");
  assert.equal(forecastTone("2026-10-30", "2026-10-31", null), "danger");
  assert.equal(forecastTone("2026-10-30", "2026-10-20", null), "success", "no 85% date to miss");
  // Too little history, or no date to meet: grey, never coloured.
  assert.equal(forecastTone("2026-10-30", null, null), "neutral");
  assert.equal(forecastTone(null, "2026-10-20", "2026-10-26"), "neutral");
});

test("the gap chip takes the forecast's colour", () => {
  for (const [p50, p85] of [
    ["2026-10-20", "2026-10-26"],
    ["2026-10-28", "2026-11-04"],
    ["2026-11-02", "2026-11-12"],
  ] as const) {
    assert.equal(forecastGap("2026-10-30", p50, p85)?.tone, forecastTone("2026-10-30", p50, p85));
  }
});

test("the team's date: red when later, amber with undated work, green when all dated in time", () => {
  const team = (latest: string | null, undated = 0) => ({ latest, undated });
  assert.equal(teamTone("2026-10-30", team("2026-11-03")), "danger");
  assert.equal(teamTone("2026-10-30", team("2026-11-03", 2)), "danger", "late beats undated");
  assert.equal(teamTone("2026-10-30", team("2026-10-15", 1)), "warning");
  assert.equal(teamTone("2026-10-30", team("2026-10-30")), "success", "on the day is in time");
  assert.equal(teamTone("2026-10-30", team(null, 3)), "neutral", "no date from the team");
  assert.equal(teamTone(null, team("2026-10-15")), "neutral", "no committed date to meet");
});

test("no committed date is to act on only when there is work to deliver", () => {
  assert.equal(missingDate({ target: null, total: 8 }), true);
  assert.equal(missingDate({ target: "2026-10-30", total: 8 }), false);
  assert.equal(missingDate({ target: null, total: 0 }), false, "nothing counted yet");
});

test("the compact strip says each thing once", () => {
  assert.deepEqual(compactParts(scope()), {
    date: "2026-10-30",
    forecast: [{ text: "forecast: Wed 4 Nov, +5 days", tone: "danger" }],
    chip: { label: "Off track", tone: "danger" },
  });
  const short = { ...scope().history, p50: null, p85: null };
  // "Not enough history to forecast" is the chip: the line does not say it again.
  assert.deepEqual(compactParts(scope({ verdict: "not_enough_data", history: short })), {
    date: "2026-10-30",
    forecast: [{ text: "team says Tue 3 Nov", tone: "danger" }],
    chip: { label: "Not enough history to forecast", tone: "neutral" },
  });
  // "No committed date" is the line: no chip says it again, and nothing is coloured.
  assert.deepEqual(
    compactParts(
      scope({
        verdict: "no_date",
        target: null,
        history: short,
        team: { latest: null, latest_key: null, dated: 0, undated: 2 },
      }),
    ),
    {
      date: null,
      forecast: [{ text: "forecast: not enough history", tone: "neutral" }],
      chip: null,
    },
  );
  assert.deepEqual(compactParts(scope({ total: 0, target: null })).chip, {
    label: "Nothing in scope",
    tone: "neutral",
  });
});

test("the compact strip's forecast in a few words, each part in its colour", () => {
  assert.equal(compactForecast(scope()), "forecast: Wed 4 Nov, +5 days");
  const onTime = { ...scope().history, p50: "2026-10-20", p85: "2026-10-28" };
  assert.deepEqual(compactForecastParts(scope({ history: onTime, verdict: "on_track" })), [
    { text: "forecast: Tue 20 Oct, on time", tone: "success" },
  ]);
  const tail = { ...scope().history, p50: "2026-10-28", p85: "2026-11-04" };
  assert.deepEqual(compactForecastParts(scope({ history: tail, verdict: "at_risk" })), [
    { text: "forecast: Wed 28 Oct, 85%: +5 days", tone: "warning" },
  ]);
  const short = { ...scope().history, p50: null, p85: null };
  assert.equal(
    compactForecast(scope({ history: short })),
    "not enough history · team says Tue 3 Nov",
    "the team's date stands in for a forecast",
  );
  // The history's part stays grey; only the team's date is judged.
  assert.deepEqual(
    compactForecastParts(
      scope({
        history: short,
        team: { latest: "2026-10-15", latest_key: "CHK-109", dated: 5, undated: 1 },
      }),
    ),
    [
      { text: "not enough history", tone: "neutral" },
      { text: "team says Thu 15 Oct", tone: "warning" },
    ],
  );
  assert.equal(
    compactForecast(
      scope({ history: short, team: { latest: null, latest_key: null, dated: 0, undated: 4 } }),
    ),
    "forecast: not enough history",
  );
  assert.equal(compactForecast(scope({ total: 0 })), "nothing to forecast");
  // Under a "Forecast" heading the words do not say "forecast" again.
  assert.deepEqual(compactForecastParts(scope(), { headed: true }), [
    { text: "Wed 4 Nov, +5 days", tone: "danger" },
  ]);
  assert.deepEqual(
    compactForecastParts(
      scope({ history: short, team: { latest: null, latest_key: null, dated: 0, undated: 4 } }),
      { headed: true },
    ),
    [{ text: "not enough history", tone: "neutral" }],
  );
  // A forecast with no committed date to meet is said, not coloured.
  assert.deepEqual(compactForecastParts(scope({ target: null, verdict: "no_date" })), [
    { text: "forecast: Wed 4 Nov", tone: "neutral" },
  ]);
});
