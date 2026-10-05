import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryStagesResponse } from "../../api/schema";
import { draftFromResponse, moveStatus, parseNames, placementOf } from "./stageMapping.ts";

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

test("names split on commas and lines, without repeats", () => {
  assert.deepEqual(parseNames("Story, bug\nStory ,  Epic  "), ["Story", "bug", "Epic"]);
});
