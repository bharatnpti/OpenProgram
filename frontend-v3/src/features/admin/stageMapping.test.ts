import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryStagesResponse } from "../../api/schema";
import {
  draftFromResponse,
  issueCount,
  issuesPerStep,
  moveStatus,
  otherNames,
  parseNames,
  placementOf,
  requestFromDraft,
  sameDraft,
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
  assert.deepEqual(others.excluded, []);
  assert.equal(others.total, 3);
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
});

test("names split on commas and lines, without repeats", () => {
  assert.deepEqual(parseNames("Story, bug\nStory ,  Epic  "), ["Story", "bug", "Epic"]);
});

test("a mapping survives the draft, and a change is noticed", () => {
  const draft = draftFromResponse(RESPONSE);
  assert.deepEqual(requestFromDraft(draft).excluded_statuses, ["Won't Do"]);
  assert.ok(sameDraft(draft, draftFromResponse(RESPONSE)));
  assert.ok(!sameDraft(draft, moveStatus(draft, "Blocked", "in_development")));
});

test("issues are counted per step, and the ones left out apart", () => {
  const counts = issuesPerStep([
    { status: "green", issues: 8, issue_types: [], stage: "raised", mapped: false },
    { status: "Done", issues: 3, issue_types: ["Story"], stage: "production", mapped: true },
    { status: "Won't Do", issues: 2, issue_types: [], stage: null, mapped: true },
  ]);
  assert.equal(counts.steps.raised, 8);
  assert.equal(counts.steps.production, 3);
  assert.equal(counts.steps.groomed, 0);
  assert.equal(counts.notCounted, 2);
  assert.equal(issueCount(1), "1 issue");
});
