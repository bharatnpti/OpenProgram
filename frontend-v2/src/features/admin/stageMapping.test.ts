import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryStagesResponse } from "../../api/schema";
import {
  draftFromResponse,
  moveStatus,
  otherNames,
  parseNames,
  placementOf,
  unplacedFirst,
} from "./stageMapping.ts";

const RESPONSE: DeliveryStagesResponse = {
  stages: [
    { stage: "raised", label: "Raised", statuses: ["To Do"] },
    { stage: "groomed", label: "Groomed", statuses: [] },
    { stage: "in_development", label: "In development", statuses: ["In Progress"] },
    { stage: "in_testing", label: "In testing", statuses: ["QA"] },
    { stage: "business_testing", label: "Business testing", statuses: [] },
    { stage: "production", label: "Production", statuses: ["Done"] },
  ],
  excluded_statuses: ["Won't Do"],
  requirement_types: [],
  is_default: false,
  updated_at: null,
  updated_by: null,
};

test("a status is placed whatever its case or spacing", () => {
  const draft = draftFromResponse(RESPONSE);

  assert.equal(placementOf(draft, "in  progress"), "in_development");
  assert.equal(placementOf(draft, "won't do"), "excluded");
  assert.equal(placementOf(draft, "Waiting for Vendor"), null);
});

test("moving a status takes it out of wherever it was", () => {
  let draft = moveStatus(draftFromResponse(RESPONSE), "qa", "business_testing");
  assert.deepEqual(draft.stages.in_testing, []);
  assert.deepEqual(draft.stages.business_testing, ["qa"]);

  draft = moveStatus(draft, "Done", "excluded");
  assert.deepEqual(draft.stages.production, []);
  assert.ok(draft.excluded.includes("Done"));

  draft = moveStatus(draft, "Done", null);
  assert.equal(placementOf(draft, "Done"), null);
});

test("other names leave out every status the tracker carries, whatever its case", () => {
  const draft = moveStatus(draftFromResponse(RESPONSE), "Backlog", "raised");
  const others = otherNames(draft, ["to do", "In Progress", "Won't Do"]);

  assert.deepEqual(others.stages.raised, ["Backlog"]);
  assert.deepEqual(others.stages.in_development, []);
  assert.deepEqual(others.stages.in_testing, ["QA"]);
  assert.deepEqual(others.stages.production, ["Done"]);
  assert.deepEqual(others.excluded, []);
  assert.equal(others.total, 3);
});

test("other names are every name when the tracker carries none", () => {
  const others = otherNames(draftFromResponse(RESPONSE), []);

  assert.deepEqual(others.excluded, ["Won't Do"]);
  assert.equal(others.total, 5);
});

test("statuses nothing places come first, each group in its own order", () => {
  const draft = draftFromResponse(RESPONSE);
  const rows = [
    { status: "To Do", issues: 9 },
    { status: "Waiting for Vendor", issues: 5 },
    { status: "Done", issues: 4 },
    { status: "Blocked", issues: 2 },
  ];

  assert.deepEqual(
    unplacedFirst(rows, draft).map((row) => row.status),
    ["Waiting for Vendor", "Blocked", "To Do", "Done"],
  );
  assert.equal(rows[0].status, "To Do");
});

test("names split on commas and lines, without repeats", () => {
  assert.deepEqual(parseNames("Story, bug\nStory ,  Epic  "), ["Story", "bug", "Epic"]);
});
