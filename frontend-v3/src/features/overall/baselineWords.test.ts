import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDay } from "../../lib/format.ts";
import {
  baselineHeadline,
  earlierLine,
  emptyPlanningForm,
  frozenWords,
  keyList,
  noBaselineWords,
  planningBody,
  planningProblem,
  sinceWords,
  sourceNote,
} from "./baselineWords.ts";

const since = (added: number, labelled: number, removed: number) => ({
  added,
  added_with_label: labelled,
  removed,
  added_keys: [],
  added_with_label_keys: [],
  removed_keys: [],
});

const baseline = {
  number: 2,
  set_at: "2026-10-14T09:30:00Z",
  set_by_name: "Mina Patel",
  planned_on: "2026-10-01",
  requirements: 24,
  open: 18,
  points: 120,
  change_label: "CR",
  since: since(3, 1, 1),
  reconstructed: false,
  snapshot_day: null,
  superseded_at: null,
};

test("a baseline says who set it, when, and what it froze", () => {
  assert.equal(
    baselineHeadline(baseline),
    `Baseline 2, set ${formatDay("2026-10-14")} by Mina Patel.`,
  );
  assert.equal(
    frozenWords(baseline),
    `Planning complete on ${formatDay("2026-10-01")}: 24 requirements, 18 not yet in production, 120 story points.`,
  );
  assert.equal(
    frozenWords({ ...baseline, requirements: 1, open: 1, points: null }),
    `Planning complete on ${formatDay("2026-10-01")}: 1 requirement.`,
  );
});

test("the scope since a baseline says what was added, with or without the change label", () => {
  assert.equal(sinceWords(baseline), "Since then: 3 added (2 without the CR label) and 1 removed.");
  assert.equal(
    sinceWords({ ...baseline, since: since(2, 2, 0) }),
    "Since then: 2 added (all with the CR label).",
  );
  assert.equal(
    sinceWords({ ...baseline, since: since(1, 1, 0) }),
    "Since then: 1 added (with the CR label).",
  );
  assert.equal(
    sinceWords({ ...baseline, since: since(4, 0, 0) }, "Since the first baseline"),
    "Since the first baseline: 4 added (none with the CR label).",
  );
  assert.equal(sinceWords({ ...baseline, since: since(0, 0, 2) }), "Since then: 2 removed.");
  assert.equal(
    sinceWords({ ...baseline, since: since(0, 0, 0) }),
    "Since then: nothing added or removed.",
  );
});

test("a key list marks the labelled keys and counts the ones it leaves out", () => {
  assert.equal(keyList(["CHK-20", "CHK-21"], 2, ["CHK-21"], "CR"), "CHK-20, CHK-21 (CR)");
  const many = Array.from({ length: 12 }, (_, i) => `CHK-${i + 1}`);
  assert.equal(
    keyList(many, 60),
    "CHK-1, CHK-2, CHK-3, CHK-4, CHK-5, CHK-6, CHK-7, CHK-8 and 52 more",
  );
});

test("a rebuilt or earlier snapshot is said; the day's own snapshot needs no note", () => {
  assert.equal(sourceNote(baseline), null);
  assert.match(sourceNote({ ...baseline, reconstructed: true }) ?? "", /^Rebuilt afterwards/);
  assert.equal(
    sourceNote({ ...baseline, snapshot_day: "2026-10-02" }),
    `Read from the snapshot of ${formatDay("2026-10-02")}, the last one kept before that day.`,
  );
});

test("an earlier baseline says when it was replaced", () => {
  assert.equal(
    earlierLine({ ...baseline, number: 1, superseded_at: "2026-10-14T09:30:00Z" }),
    `Baseline 1, set ${formatDay("2026-10-14")} by Mina Patel: 24 requirements; replaced ${formatDay("2026-10-14")}.`,
  );
  assert.match(
    noBaselineWords({ name: "Release 1.0", release_id: "rel-1" }),
    /for Release 1.0 yet/,
  );
  assert.match(noBaselineWords({ name: "Checkout", release_id: null }), /for this project yet/);
});

test("the form checks the day, the label and the grace period before sending", () => {
  const today = "2026-10-12";
  const form = emptyPlanningForm({ label: "CR", grace: 2 });
  assert.equal(planningProblem(form, today), null);
  assert.equal(
    planningProblem({ ...form, when: "earlier" }, today),
    "Pick the day planning was complete.",
  );
  assert.equal(
    planningProblem({ ...form, when: "earlier", day: "2026-10-13" }, today),
    "Pick today or an earlier day.",
  );
  assert.equal(
    planningProblem({ ...form, when: "earlier", day: "2025-10-01" }, today),
    "Pick a day within the last year.",
  );
  assert.equal(
    planningProblem({ ...form, label: "change request" }, today),
    "A change label is one Jira label, with no spaces.",
  );
  assert.equal(
    planningProblem({ ...form, grace: "21" }, today),
    "The grace period is 0 to 20 working days.",
  );
  assert.equal(
    planningProblem({ ...form, grace: "-1" }, today),
    "The grace period is 0 to 20 working days.",
  );
  assert.equal(planningProblem({ ...form, label: "" }, today), null);
});

test("the request leaves the label to the server when none is typed", () => {
  const form = { ...emptyPlanningForm({ label: "CR", grace: 2 }), label: " ", note: " agreed " };
  assert.deepEqual(planningBody(form, ""), {
    release_id: null,
    planned_on: null,
    change_label: null,
    grace_working_days: 2,
    note: "agreed",
  });
  assert.deepEqual(
    planningBody({ ...form, when: "earlier", day: "2026-10-01", label: "CR", grace: "0" }, "rel-1"),
    {
      release_id: "rel-1",
      planned_on: "2026-10-01",
      change_label: "CR",
      grace_working_days: 0,
      note: "agreed",
    },
  );
});
