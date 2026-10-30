import assert from "node:assert/strict";
import test from "node:test";

import type { RequestTrailStepDto } from "../../api/schema";
import {
  countTiles,
  countsFootnote,
  longestWaitFirst,
  matchesSearch,
  statusWords,
  trailLine,
} from "./requestWords.ts";

const say = { day: (iso: string) => `day(${iso.slice(0, 10)})`, time: () => "10:01" };
const step = (over: Partial<RequestTrailStepDto>): RequestTrailStepDto => ({
  kind: "asked",
  state: "done",
  at: null,
  on: null,
  who: null,
  role: null,
  by: null,
  dm: null,
  dm_attempts: 0,
  dm_at: null,
  eta: null,
  project_name: null,
  report: null,
  computed: false,
  ...over,
});

test("the asked step says the DM as it went, and never claims one that was not sent", () => {
  const sent = trailLine(
    step({ at: "2026-10-06T10:00:00Z", who: "Noah", dm: "sent", dm_at: "2026-10-06T10:01:00Z" }),
    say,
  );
  assert.equal(sent.title, "Asked Noah directly");
  assert.equal(sent.detail, "day(2026-10-06), 10:01 · DM sent 10:01");

  const failed = trailLine(step({ who: "Noah", dm: "not_delivered", dm_attempts: 5 }), say);
  assert.equal(failed.detail, "DM not delivered after 5 attempts");
  assert.doesNotMatch(failed.detail ?? "", /DM sent/);

  assert.equal(trailLine(step({ who: "Noah", dm: "none" }), say).detail, "No DM was sent");
  assert.match(
    trailLine(step({ dm: "retrying", dm_attempts: 2 }), say).detail ?? "",
    /trying again/,
  );
});

test("an acknowledgement carries the when its reply gave", () => {
  const line = trailLine(
    step({ kind: "acknowledged", at: "2026-10-06T14:00:00Z", by: "Noah", eta: "by Thursday" }),
    say,
  );
  assert.equal(line.title, "Acknowledged by Noah");
  assert.equal(line.detail, "day(2026-10-06), 10:01 · expects it by Thursday");
  assert.equal(
    trailLine(step({ kind: "acknowledged", by: "Noah" }), say).detail,
    "When was not recorded",
  );
});

test("a matrix step says it is computed, and whether a day report carried it", () => {
  const named = trailLine(
    step({
      kind: "named",
      state: "done",
      on: "2026-10-08",
      who: "Ira",
      role: "Scrum master",
      report: "sent",
      project_name: "Checkout Revamp",
      computed: true,
    }),
    say,
  );
  assert.equal(
    named.title,
    "Named to Ira (scrum master) in Checkout Revamp's day report of day(2026-10-08)",
  );
  assert.equal(named.computed, true);
  assert.equal(named.due, false);
  assert.match(named.detail ?? "", /escalation matrix/);

  const due = trailLine(
    step({
      kind: "named",
      state: "due",
      on: "2026-10-11",
      who: "Asha",
      role: "Manager",
      report: "not_sent",
      computed: true,
    }),
    say,
  );
  assert.equal(due.title, "Due to Asha (manager) from day(2026-10-11)");
  assert.equal(due.due, true);
  assert.match(due.detail ?? "", /nobody was told/);

  const none = trailLine(
    step({
      kind: "named",
      state: "due",
      on: "2026-10-11",
      role: "Manager",
      report: "no_report",
      project_name: "Insights",
    }),
    say,
  );
  // No name for a reader who reads none: the role stands in.
  assert.equal(none.title, "Reaches the manager from day(2026-10-11)");
  assert.match(none.detail ?? "", /Insights has no day report/);
});

test("the close says who resolved it, or that the merge landed", () => {
  assert.equal(trailLine(step({ kind: "resolved", by: "Noah" }), say).title, "Resolved by Noah");
  assert.equal(trailLine(step({ kind: "cleared" }), say).title, "Cleared: the merge landed");
  assert.equal(
    trailLine(step({ kind: "unmatched", who: "the payments lead", by: "Liam" }), say).title,
    "“the payments lead” matched nobody",
  );
});

test("the status cell says how long an ask has waited, or when it was resolved", () => {
  const open = {
    status: "open" as const,
    created_at: "2026-10-07T09:00:00Z",
    updated_at: "2026-10-07T09:00:00Z",
  };
  assert.deepEqual(statusWords(open, "2026-10-13", say), { word: "Open · 6d", tone: "warning" });
  assert.equal(
    statusWords(
      { ...open, status: "resolved", updated_at: "2026-10-09T11:00:00Z" },
      "2026-10-13",
      say,
    ).word,
    "Resolved day(2026-10-09)",
  );
  assert.equal(
    statusWords({ ...open, status: "needs_resolution" }, "2026-10-13", say).word,
    "Needs who · 6d",
  );
});

test("the board puts the longest wait first and searches every word", () => {
  const rows = [
    { id: "b", created_at: "2026-10-05T00:00:00Z" },
    { id: "a", created_at: "2026-09-20T00:00:00Z" },
  ];
  assert.deepEqual(
    [...rows].sort(longestWaitFirst).map((row) => row.id),
    ["a", "b"],
  );
  assert.equal(matchesSearch("Liam Noah review refund PR", "noah refund"), true);
  assert.equal(matchesSearch("Liam Noah review refund PR", "noah ledger"), false);
});

test("an executive's tiles count asks and never name anyone", () => {
  const counts = {
    as_of: "2026-10-13",
    open: 4,
    acknowledged: 2,
    needs_resolution: 1,
    waiting_over_a_week: 3,
    not_delivered: 0,
    resolved_recently: 5,
    resolved_window_days: 30,
  };
  assert.deepEqual(
    countTiles(counts).map((tile) => [tile.label, tile.value]),
    [
      ["Open", 4],
      ["Acknowledged", 2],
      ["Waiting over a week", 3],
      ["Resolved", 5],
    ],
  );
  assert.equal(countsFootnote(counts), "1 ask names someone nobody matched yet.");
  assert.equal(countsFootnote({ ...counts, needs_resolution: 0 }), null);
});
