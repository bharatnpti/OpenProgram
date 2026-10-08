import assert from "node:assert/strict";
import { test } from "node:test";

import { raisedStillOpen, requestsAmong, scopePeople, waitingStillOpen } from "./raised.ts";

test("an ask nobody was matched to still waits on the person who raised it", () => {
  assert.equal(raisedStillOpen("open"), true);
  assert.equal(raisedStillOpen("acknowledged"), true);
  assert.equal(raisedStillOpen("needs_resolution"), true);
  assert.equal(raisedStillOpen("resolved"), false);
  assert.equal(raisedStillOpen("dismissed"), false);
});

test("an ask waits on the person asked while it is open or acknowledged", () => {
  assert.equal(waitingStillOpen("open"), true);
  assert.equal(waitingStillOpen("acknowledged"), true);
  // Nobody was matched to it, so it is in nobody's inbox.
  assert.equal(waitingStillOpen("needs_resolution"), false);
  assert.equal(waitingStillOpen("resolved"), false);
});

const pods = [
  { id: "pod-payments", member_ids: ["U1007", "U1008"], project_ids: ["project-checkout"] },
  { id: "pod-storefront", member_ids: ["U1009"], project_ids: ["project-checkout"] },
  { id: "pod-identity", member_ids: ["U1012"], project_ids: ["project-identity"] },
];

test("a board scope covers the people of the pods, or of every pod on the projects", () => {
  assert.deepEqual([...scopePeople(pods, { podIds: ["pod-payments"] })], ["U1007", "U1008"]);
  assert.deepEqual([...scopePeople(pods, { projectIds: ["project-checkout"] })].sort(), [
    "U1007",
    "U1008",
    "U1009",
  ]);
  assert.equal(scopePeople(pods, {}).size, 0);
});

test("a request is in scope when the person asking or the person asked is", () => {
  const requests = [
    { id: "a", requester_id: "U1007", counterpart_id: "U1001" },
    { id: "b", requester_id: "U1001", counterpart_id: "U1009" },
    { id: "c", requester_id: "U1012", counterpart_id: null },
    { id: "d", requester_id: "U1012", counterpart_id: "U1001" },
  ];
  const people = scopePeople(pods, { projectIds: ["project-checkout"] });
  assert.deepEqual(
    requestsAmong(requests, people).map((request) => request.id),
    ["a", "b"],
  );
});
