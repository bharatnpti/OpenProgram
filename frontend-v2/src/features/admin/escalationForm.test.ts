import assert from "node:assert/strict";
import { test } from "node:test";

import type { EscalationMatrixResponse } from "../../api/schema";
import {
  draftFromMatrix,
  draftProblems,
  levelSummary,
  newLevel,
  requestFromDraft,
} from "./escalationForm.ts";

const MATRIX: EscalationMatrixResponse = {
  project_id: "",
  source: "default",
  decision_owner_id: null,
  levels: [
    {
      label: "Scrum master",
      source: "team_scrum_master",
      member_id: null,
      after_days: { fix: 2, decision: 2, answer: 3, review: 2 },
    },
    {
      label: "Manager",
      source: "team_manager",
      member_id: null,
      after_days: { fix: 5, decision: 4, answer: 6 },
    },
  ],
  updated_at: null,
  updated_by: null,
};

test("a matrix survives the editor", () => {
  const request = requestFromDraft(draftFromMatrix(MATRIX));

  assert.deepEqual(request, {
    decision_owner_id: null,
    levels: MATRIX.levels.map((level) => ({ ...level, member_id: null })),
  });
});

test("a kind left empty never reaches the level", () => {
  const draft = draftFromMatrix(MATRIX);
  draft.levels[0].days.review = "";

  assert.deepEqual(requestFromDraft(draft).levels[0].after_days, {
    fix: 2,
    decision: 2,
    answer: 3,
  });
});

test("a new level starts two days after the one below it, and needs its member", () => {
  const draft = draftFromMatrix(MATRIX);
  const added = newLevel(draft.levels[1]);

  assert.deepEqual(added.days, { fix: "7", decision: "6", answer: "8", review: "" });
  assert.deepEqual(draftProblems({ ...draft, levels: [...draft.levels, added] }), [
    "Level 4 needs a name.",
    "Level 4 needs the member it goes to.",
  ]);
});

test("the editor says what the server would refuse", () => {
  const draft = draftFromMatrix(MATRIX);
  draft.levels[1].days.fix = "1";
  draft.levels[0].days.answer = "1.5";

  assert.deepEqual(draftProblems(draft), [
    "Level 2 waits a whole number of days, 0 to 365.",
    "Level 3 is reached before level 2 for fix: give it more days.",
  ]);
});

test("a level reads as a sentence", () => {
  assert.equal(
    levelSummary(MATRIX.levels[1]),
    "Fix after 5 days, decision after 4, answer after 6; never for reviews.",
  );
  assert.equal(
    levelSummary({ label: "x", source: "member", after_days: {} }),
    "Nothing reaches this level.",
  );
});
