import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type DateFacts,
  type OwnerAsks,
  type ProgressFacts,
  RING_CIRCUMFERENCE,
  dateBar,
  dayScale,
  gateRing,
  importantView,
  progressParts,
  questionRows,
  stageStrip,
  tileName,
  whoActs,
} from "./dailyViz.ts";

const TODAY = "2026-10-09";

const shortHistory: DateFacts = {
  verdict: "at_risk",
  target: "2026-12-15",
  target_source: "committed",
  committed_by: "Mina Patel",
  times_moved: 0,
  moved_days: null,
  p50: null,
  p85: null,
  history_days: 3,
  history_needed: 10,
  no_forecast_reason: "Only 3 working days of history; a forecast needs 10.",
  team_latest: "2026-10-09",
  team_latest_key: "CHK-4",
};

const month: DateFacts = {
  ...shortHistory,
  times_moved: 1,
  moved_days: 14,
  p50: "2026-12-02",
  p85: "2026-12-21",
  history_days: null,
  history_needed: null,
  no_forecast_reason: null,
};

const near = (actual: number | undefined, expected: number) =>
  assert.ok(Math.abs((actual ?? NaN) - expected) < 0.6, `${actual} is not about ${expected}`);

test("the date bar with three days of history: the committed date, the team's, no forecast", () => {
  const view = dateBar(shortHistory, TODAY);

  assert.equal(view.date, "Delivery Tue 15 Dec 2026");
  assert.equal(view.verdict, "at risk");
  assert.equal(view.tone, "amber");
  assert.equal(view.sub, "committed by Mina Patel");
  assert.equal(view.caption, null);
  const chart = view.chart;
  assert.ok(chart);
  // Today to the end of December, as the mockup draws it.
  near(chart.committed?.x, 442.2);
  assert.deepEqual(
    chart.ticks.map((tick) => tick.label),
    ["1 Nov", "1 Dec"],
  );
  near(chart.ticks[0].x, 162.3);
  near(chart.ticks[1].x, 353.2);
  assert.equal(chart.forecast, null);
  assert.equal(chart.noForecast?.text, "No forecast yet");
  assert.deepEqual(
    chart.notes.map((note) => note.text),
    ["Team's latest date · CHK-4 · today", "History: 3 of 10 working days"],
  );
  near(chart.team?.x, 16);
  assert.match(chart.label, /No forecast yet: 3 of 10 working days of history\./);
  assert.match(chart.label, /The team's latest date is today, CHK-4\./);
  assert.deepEqual(view.legend, { committed: true, team: true, forecast: null });
});

test("after a month the bar draws the forecast band, 50% to 85%, and how the date moved", () => {
  const view = dateBar(month, TODAY);

  assert.equal(view.sub, "committed by Mina Patel · moved once, +14 days");
  const forecast = view.chart?.forecast;
  assert.ok(forecast);
  assert.equal(forecast.tone, "amber");
  near(forecast.from ?? undefined, 359.5);
  near(forecast.to, 480.4);
  assert.deepEqual(
    forecast.labels.map((label) => label.text),
    ["50% · Wed 2 Dec", "85% · Mon 21 Dec"],
  );
  assert.equal(view.chart?.noForecast, null);
  assert.equal(view.legend.forecast, "range");
  assert.match(view.chart?.label ?? "", /50% likely by Wed 2 Dec, 85% likely by Mon 21 Dec/);
});

test("on a phone the bar is the narrow drawing, with short labels", () => {
  const chart = dateBar(month, TODAY, "narrow").chart;
  assert.ok(chart);
  assert.equal(chart.width, 340);
  near(chart.committed?.x, 269.5);
  assert.deepEqual(
    chart.forecast?.labels.map((label) => label.text),
    ["50% · 2 Dec", "85% · 21 Dec"],
  );
  assert.equal(chart.notes[0].text, "Team · CHK-4 · today");
});

test("with no committed date the bar says so in red and draws nothing it cannot place", () => {
  const view = dateBar(
    {
      ...shortHistory,
      verdict: "no_date",
      target: null,
      target_source: null,
      committed_by: null,
      history_days: null,
      history_needed: null,
      no_forecast_reason: null,
      team_latest: null,
      team_latest_key: null,
    },
    TODAY,
  );
  assert.equal(view.date, "Delivery");
  assert.equal(view.verdict, "no delivery date set");
  assert.equal(view.tone, "red");
  assert.equal(view.sub, null);
  assert.equal(view.chart, null);
});

test("a forecast the report states only at 85% is one tick in the verdict's colour", () => {
  const view = dateBar(
    { ...month, verdict: "on_track", p50: null, committed_by: null, times_moved: 0 },
    TODAY,
  );
  assert.equal(view.tone, "green");
  assert.equal(view.sub, null);
  assert.equal(view.chart?.forecast?.from, null);
  assert.deepEqual(
    view.chart?.forecast?.labels.map((label) => label.text),
    ["85% · Mon 21 Dec"],
  );
  assert.equal(view.legend.forecast, "p85");
});

test("a team date past today stays at the start, and says it is past", () => {
  const view = dateBar({ ...shortHistory, team_latest: "2026-10-05" }, TODAY);
  near(view.chart?.team?.x, 16);
  assert.equal(view.chart?.notes[0].text, "Team's latest date · CHK-4 · Mon 5 Oct, past");
});

const strip: ProgressFacts = {
  percent: 27.8,
  since: "2026-10-08",
  total: 18,
  stages: [
    { stage: "raised", count: 6, previous: 7 },
    { stage: "groomed", count: 0, previous: 0 },
    { stage: "in_development", count: 6, previous: 6 },
    { stage: "in_testing", count: 0, previous: 0 },
    { stage: "business_testing", count: 1, previous: 0 },
    { stage: "production", count: 5, previous: 5 },
  ],
  moves: [
    {
      key: "CHK-16",
      title: "Q4 checkout roadmap review",
      from_stage: "raised",
      to_stage: "in_development",
    },
    {
      key: "CHK-12",
      title: "Promo code validation",
      from_stage: "in_development",
      to_stage: "business_testing",
    },
  ],
  more_moves: 0,
  other_changes: [],
  notes: [],
};

test("the stage strip says each stage's change and draws an arrow per move", () => {
  const view = stageStrip(strip);

  assert.deepEqual(
    view.tiles.map((tile) => [tile.label, tile.count, tile.change]),
    [
      ["Raised", 6, "−1"],
      ["Groomed", 0, "no change"],
      ["In development", 6, "1 in · 1 out"],
      ["In testing", 0, "no change"],
      ["Business testing", 1, "+1"],
      ["Production", 5, "no change"],
    ],
  );
  assert.equal(view.tiles[0].title, "Raised: 6 today, 7 on Thu 8 Oct");
  assert.deepEqual(
    view.arrows.map((arrow) => arrow.label.text),
    ["CHK-16 · skipped Groomed", "CHK-12 · skipped In testing"],
  );
  assert.equal(view.arrows[0].path, "M54.7 62 C54.7 30, 235.3 30, 235.3 61");
  assert.equal(view.listed.length, 0);
  assert.deepEqual(view.all[0], {
    move: "Raised → In development",
    what: "CHK-16 Q4 checkout roadmap review · skipped Groomed",
  });
  assert.equal(view.summary, "2 of 18 moved since Thu 8 Oct, and both skipped a stage.");
  assert.match(view.label, /Raised 6, down 1; Groomed 0; In development 6, 1 in and 1 out/);
  assert.match(view.label, /CHK-16 moved from Raised to In development, skipping Groomed\./);
});

test("on the first day the strip has counts and no changes", () => {
  const view = stageStrip({
    ...strip,
    since: null,
    stages: strip.stages.map((item) => ({ ...item, previous: null })),
    moves: [],
    other_changes: ["The first snapshot is today; changes show from tomorrow."],
  });
  assert.ok(view.tiles.every((tile) => tile.change === "" && !tile.changed));
  assert.equal(view.summary, null);
  assert.equal(view.arrows.length, 0);
});

test("many kinds of move are listed, not drawn, and a new requirement is never an arrow", () => {
  const moves = [
    { key: "A-1", title: "One", from_stage: "raised", to_stage: "groomed" },
    { key: "A-2", title: "Two", from_stage: "groomed", to_stage: "in_development" },
    { key: "A-3", title: "Three", from_stage: "in_testing", to_stage: "in_development" },
  ] as ProgressFacts["moves"];
  const busy = stageStrip({ ...strip, moves, more_moves: 2 });
  assert.equal(busy.arrows.length, 0);
  assert.equal(busy.listed.length, 4);
  assert.equal(busy.listed[2].what, "A-3 Three · moved back");
  assert.equal(busy.listed[3].move, "and 2 more");
  // Not every move is listed, so in and out are not counted: the change is the net.
  assert.equal(busy.tiles[2].change, "no change");

  const fresh = stageStrip({
    ...strip,
    moves: [
      strip.moves[0],
      { key: "CHK-20", title: "Gift cards", from_stage: null, to_stage: "raised" },
    ],
  });
  assert.equal(fresh.arrows.length, 1);
  assert.deepEqual(fresh.listed, [{ move: "New, in Raised", what: "CHK-20 Gift cards" }]);
});

test("a stage's name takes two lines on a tile only when it is long", () => {
  assert.deepEqual(tileName("In development"), ["In", "development"]);
  assert.deepEqual(tileName("Business testing"), ["Business", "testing"]);
  assert.deepEqual(tileName("In testing"), ["In testing"]);
});

test("a gate ring counts each requirement once, with a word for every colour", () => {
  const ring = gateRing({
    name: "Business acceptance",
    guards_stage: "production",
    total: 18,
    passed: 1,
    bypassed: 5,
    failed: 0,
    open: 0,
    missing: 12,
  });
  assert.equal(ring.before, "before production");
  assert.deepEqual(
    ring.rows.map((row) => [row.count, row.words]),
    [
      [1, "passed"],
      [5, "moved on without it"],
      [12, "still to confirm"],
    ],
  );
  assert.equal(
    ring.label,
    "Business acceptance: 1 of 18 passed, 5 moved on without it, 12 still to confirm",
  );
  assert.deepEqual(
    ring.segments.map((segment) => segment.tone),
    ["green", "bypassed", "neutral"],
  );
  const per = RING_CIRCUMFERENCE / 18;
  assert.equal(ring.segments[1].dash.split(" ")[0], (5 * per - 1).toFixed(1));
  assert.equal(ring.segments[1].offset, (-(1 * per + 0.5)).toFixed(1));

  const failing = gateRing({
    name: "Engineering delivery",
    guards_stage: "business_testing",
    total: 3,
    passed: 0,
    bypassed: 0,
    failed: 1,
    open: 2,
    missing: 0,
  });
  assert.deepEqual(
    failing.rows.map((row) => row.words),
    ["passed", "failed", "moved on without it", "open, waiting for sign-off", "still to confirm"],
  );
  // A segment only for a count above nothing.
  assert.deepEqual(
    failing.segments.map((segment) => segment.tone),
    ["red", "amber"],
  );
});

test("Most important names every requirement that went around a gate, by stage", () => {
  const view = importantView({
    drawn: ["committed", "history", "team"],
    bypassed: [
      { key: "CHK-12", stage: "business_testing", gates: ["Engineering delivery"] },
      ...["CHK-13", "CHK-17", "CHK-3", "CHK-5", "CHK-8"].map((key) => ({
        key,
        stage: "production" as const,
        gates: ["Business acceptance", "Engineering delivery"],
      })),
    ],
    risks: 4,
    lines: [],
  });
  assert.deepEqual(view.groups, [
    {
      words: "Reached production without Business acceptance and Engineering delivery",
      keys: ["CHK-3", "CHK-5", "CHK-8", "CHK-13", "CHK-17"],
    },
    { words: "Reached business testing without Engineering delivery", keys: ["CHK-12"] },
  ]);
  assert.equal(
    view.note,
    "The committed date, the history and the team's date are drawn once, in the bar under In short. " +
      "The 4 risks the message lists here are fixes under What we need.",
  );
  assert.equal(view.empty, false);
  assert.equal(importantView({ drawn: [], bypassed: [], risks: 0, lines: [] }).empty, true);
});

const asks: OwnerAsks[] = [
  {
    heading: "Liam Chen",
    named: true,
    asks: [
      {
        need: "review",
        text: "CHK-6: Noah Weber asked for a review",
        detail: "",
        waited_days: 5,
        issue_key: "CHK-6",
        escalated_to: "Asha Rao",
        escalation_label: "Manager",
        needed_most: true,
        open_question: false,
      },
    ],
  },
  {
    heading: "Zoe Almeida",
    named: true,
    asks: [4, 2].map((days) => ({
      need: "fix" as const,
      text: `Pull request open (${days})`,
      detail: "",
      waited_days: days,
      issue_key: null,
      escalated_to: "Ira Novak",
      escalation_label: "Scrum master",
      needed_most: days === 4,
      open_question: false,
    })),
  },
  {
    heading: "Asha Rao",
    named: true,
    asks: [
      {
        need: "review",
        text: "Mina Patel asked for a review",
        detail: "",
        waited_days: 6,
        issue_key: null,
        escalated_to: "Ira Novak",
        escalation_label: "Scrum master",
        needed_most: true,
        open_question: false,
      },
    ],
  },
  {
    heading: "Mina Patel",
    named: true,
    asks: [
      {
        need: "answer",
        text: 'CHK-12: "Does a promo code apply to the subtotal only?"',
        detail: "asked by Sofia Bergmann (partly answered)",
        waited_days: 3,
        issue_key: "CHK-12",
        escalated_to: null,
        escalation_label: null,
        needed_most: false,
        open_question: true,
      },
    ],
  },
  {
    heading: "Nobody named yet",
    named: false,
    asks: [
      {
        need: "fix",
        text: "CHK-1: no test case yet for engineering delivery (before business testing)",
        detail: "",
        waited_days: null,
        issue_key: "CHK-1",
        escalated_to: null,
        escalation_label: null,
        needed_most: false,
        open_question: false,
      },
    ],
  },
];

test("who acts: one line for the escalations, a lane per person, bars against one scale", () => {
  const view = whoActs(asks);

  assert.equal(
    view.escalated,
    "4 escalated: 3 to Ira Novak (Scrum master), 1 to Asha Rao (Manager)",
  );
  assert.deepEqual(
    view.scale.ticks.map((tick) => tick.label),
    ["0", "2", "4", "6 days"],
  );
  assert.deepEqual(
    view.lanes.map((lane) => lane.heading),
    ["Liam Chen", "Zoe Almeida", "Asha Rao", "Mina Patel", "Nobody named yet"],
  );
  const liam = view.lanes[0].asks[0];
  assert.equal(liam.kind, "Review");
  assert.equal(liam.daysText, "5 d");
  near(liam.width, 83.3);
  assert.equal(liam.escalation, "Escalated to Asha Rao (Manager)");
  assert.equal(liam.neededMost, true);
  // The question itself is under Open questions; the lane points there.
  assert.equal(
    view.lanes[3].asks[0].text,
    "CHK-12, the open question below · asked by Sofia Bergmann (partly answered)",
  );
  const undated = view.lanes[4].asks[0];
  assert.equal(undated.daysText, "–");
  assert.equal(undated.width, 0);
  assert.equal(undated.waited, "How long it has waited is not known");
  assert.deepEqual(view.kinds, ["fix", "review", "answer"]);
});

test("a long wait puts a short unit on the scale's last step", () => {
  const view = whoActs([{ ...asks[0], asks: [{ ...asks[0].asks[0], waited_days: 29 }] }]);
  assert.deepEqual(
    view.scale.ticks.map((tick) => tick.label),
    ["0", "10", "20", "30 d"],
  );
});

test("a date bar with nothing to say under it is shorter, as on a tenant new to forecasts", () => {
  // The demo tenant: a committed date, too little history, and In short says no more.
  const view = dateBar(
    {
      ...shortHistory,
      verdict: "not_enough_data",
      target: "2026-10-30",
      committed_by: null,
      history_days: null,
      history_needed: null,
      no_forecast_reason: null,
      team_latest: null,
      team_latest_key: null,
    },
    TODAY,
  );
  assert.equal(view.verdict, "not enough history to forecast");
  assert.equal(view.tone, "neutral");
  assert.equal(view.chart?.height, 82);
  assert.equal(view.chart?.noForecast?.text, "No forecast yet");
  assert.deepEqual(view.chart?.ticks, []);
  assert.deepEqual(view.legend, { committed: true, team: false, forecast: null });
});

test("one escalation target reads as one line, none as nothing", () => {
  const single = whoActs([asks[1]]);
  assert.equal(single.escalated, "2 escalated to Ira Novak (Scrum master)");
  assert.equal(whoActs([asks[3]]).escalated, null);
  assert.equal(whoActs([]).lanes.length, 0);
});

test("the days scale is round, with at most four steps", () => {
  assert.deepEqual(dayScale(6), { max: 6, ticks: [0, 2, 4, 6] });
  assert.deepEqual(dayScale(29), { max: 30, ticks: [0, 10, 20, 30] });
  assert.deepEqual(dayScale(0), { max: 1, ticks: [0, 1] });
  assert.deepEqual(dayScale(5), { max: 6, ticks: [0, 2, 4, 6] });
});

test("open questions read by their column names", () => {
  const rows = questionRows({
    columns: ["Ticket", "What we asked", "Asked to", "Asked on", "Heard back?"],
    rows: [
      ["CHK-12", "Does a promo code apply?", "Mina Patel", "Tue 6 Oct 2026", "Partly"],
      ["CHK-1", "Is it in scope?", "—", "Mon 5 Oct 2026", "Not yet (4 days)"],
    ],
  });
  assert.deepEqual(rows[0], {
    ticket: "CHK-12",
    question: "Does a promo code apply?",
    asked: "Asked to Mina Patel on Tue 6 Oct 2026",
    heard: "Partly",
    tone: "info",
  });
  assert.equal(rows[1].tone, "neutral");
  assert.deepEqual(questionRows(null), []);
});

test("the progress line splits into its number and the rest, in the server's words", () => {
  assert.deepEqual(progressParts("28% complete: 5 of 18 requirements in production."), {
    lead: "28%",
    rest: "complete: 5 of 18 requirements in production.",
  });
  assert.deepEqual(progressParts("No requirements counted yet."), {
    lead: null,
    rest: "No requirements counted yet.",
  });
});
