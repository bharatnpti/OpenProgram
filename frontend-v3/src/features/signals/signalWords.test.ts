import assert from "node:assert/strict";
import { test } from "node:test";

import { indexNames, nameLookup } from "../../app/names.ts";
import { findingSubject, flowIsEmpty } from "./signalWords.ts";

const none = {
  active_count: 0,
  features_in_flight: 0,
  completed_count: 0,
  stale_count: 0,
  abandoned_count: 0,
  avg_cycle_time_days: null,
  avg_pr_age_days: null,
  workstreams: [],
};

test("flow that measured nothing is not a row of zeros", () => {
  assert.equal(flowIsEmpty(none), true);
});

test("any count, any average or any workstream makes it a measurement", () => {
  assert.equal(flowIsEmpty({ ...none, active_count: 1 }), false);
  assert.equal(flowIsEmpty({ ...none, stale_count: 2 }), false);
  assert.equal(flowIsEmpty({ ...none, avg_pr_age_days: 0 }), false);
  assert.equal(flowIsEmpty({ ...none, avg_cycle_time_days: 3.5 }), false);
  assert.equal(
    flowIsEmpty({
      ...none,
      workstreams: [
        {
          workstream_id: "ws",
          workstream_name: "Checkout",
          active_count: 0,
          features_in_flight: 0,
          completed_count: 0,
          stale_count: 0,
          abandoned_count: 0,
          avg_cycle_time_days: null,
          avg_pr_age_days: null,
        },
      ],
    }),
    false,
  );
});

const names = nameLookup(indexNames([{ id: "U1", name: "Noah Weber" }]));

test("a finding about a person names the person, never their id", () => {
  assert.equal(findingSubject({ kind: "developer", id: "U1" }, "Noah W.", names), "Noah W.");
  assert.equal(findingSubject({ kind: "developer", id: "U1" }, null, names), "Noah Weber");
  assert.equal(
    findingSubject({ kind: "developer", id: "U9" }, undefined, names),
    "unnamed person (U9)",
  );
});

test("a finding about anything else is kind and id", () => {
  assert.equal(
    findingSubject({ kind: "pod", id: "pod-payments" }, null, names),
    "pod pod-payments",
  );
  assert.equal(findingSubject({ kind: "work_item", id: "CHK-6" }, null, names), "work item CHK-6");
});
