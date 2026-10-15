import assert from "node:assert/strict";
import { test } from "node:test";

import type { ScopeProjectDto } from "../../api/schema";
import {
  addedWords,
  foundWords,
  labelHeading,
  ownerWords,
  planningWords,
  withoutLabelWords,
} from "./scopeWords.ts";

function project(over: Partial<ScopeProjectDto> = {}): ScopeProjectDto {
  return {
    id: "p-atlas",
    name: "Atlas",
    program_id: null,
    program_name: null,
    plan: {
      planned_on: "2026-05-26",
      planned: 8,
      added: 3,
      without_label: 2,
      without_label_percent: 67,
      change_label: "AGREED",
      removed: 1,
      added_days: null,
    },
    added_keys: ["ATL-3", "ATL-4", "ATL-5"],
    without_label_keys: ["ATL-4", "ATL-5"],
    scope_day: "2026-06-15",
    hygiene_open: 1,
    ...over,
  };
}

test("what joined after planning, counted, with the tenant's own label", () => {
  assert.equal(withoutLabelWords(project()), "2 (67%)");
  assert.equal(withoutLabelWords(project({ plan: null })), "—");
  assert.equal(addedWords(project()), "+3");
  assert.equal(addedWords(project({ plan: null })), "No baseline");
  assert.equal(labelHeading([project(), project({ plan: null })]), "Without the AGREED label");
  const other = project();
  other.plan = { ...other.plan!, change_label: "CR" };
  assert.equal(labelHeading([project(), other]), "Without the change label");
  assert.equal(
    planningWords(project()),
    "8 planned on Tue 26 May · 1 removed · scope as of Mon 15 Jun",
  );
});

test("a finding's owner by name, else the team, and when it was found", () => {
  assert.equal(
    ownerWords({ owner_name: "Ben Marsh", owner_team: "Atlas Edge" }),
    "Ben Marsh · Atlas Edge",
  );
  assert.equal(ownerWords({ owner_name: null, owner_team: "Atlas Edge" }), "Atlas Edge");
  assert.equal(ownerWords({ owner_name: null, owner_team: null }), "Nobody named yet");
  assert.equal(foundWords({ found_on: "2026-06-12", open_days: 3 }), "Fri 12 Jun · 3 days");
  assert.equal(foundWords({ found_on: "2026-06-15", open_days: 0 }), "Mon 15 Jun · today");
});
