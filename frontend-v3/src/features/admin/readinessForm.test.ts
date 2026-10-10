import assert from "node:assert/strict";
import { test } from "node:test";

import type { ReadinessCriterionDto } from "../../api/schema";
import {
  appliesLine,
  criterionFromDraft,
  draftFromCriterion,
  draftProblems,
  foundByLine,
  newCriterionDraft,
  newMatcher,
  settingsProblems,
} from "./readinessForm.ts";

const SECURITY: ReadinessCriterionDto = {
  criterion_id: "security-review",
  version: 2,
  name: "Security review",
  evidence: "A security review of the release's changes.",
  applies_to: "release",
  required_before: "production",
  lead_working_days: 10,
  severity: "blocking",
  needs_done: true,
  when_labels: [],
  when_types: [],
  matchers: [
    { kind: "label", value: "security-review", strength: "evidence" },
    { kind: "title_phrase", value: "security review", strength: "evidence" },
    { kind: "title_words", value: "pen test", strength: "candidate" },
  ],
  draft: {
    project_key: "",
    issue_type: "",
    summary: "{criterion} for {scope}",
    description: "",
    labels: ["security-review"],
  },
  enabled: true,
};

test("a criterion round-trips through the form", () => {
  const back = criterionFromDraft(draftFromCriterion(SECURITY));
  assert.deepEqual({ ...back, version: SECURITY.version }, SECURITY);
});

test("a new criterion starts blocking, before production, ten working days ahead", () => {
  const draft = newCriterionDraft();
  assert.equal(draft.blocking, true);
  assert.equal(draft.requiredBefore, "production");
  assert.equal(draft.leadText, "10");
  assert.deepEqual(newMatcher("title_words"), {
    kind: "title_words",
    value: "",
    strength: "candidate",
  });
});

test("the form says what the server would refuse", () => {
  const draft = {
    ...newCriterionDraft(),
    leadText: "61",
    matchers: [{ kind: "label" as const, value: "two words", strength: "evidence" as const }],
    summary: "{assignee}: {criterion}",
    projectKey: "1X",
  };
  assert.deepEqual(draftProblems(draft), [
    "Give it a name of 1 to 80 characters.",
    "Say what counts as evidence in 1 to 600 characters.",
    "The lead is 0 to 60 working days.",
    "A Jira label has no spaces.",
    "A Jira project key is capital letters and digits, such as CHK.",
    "The draft uses {assignee}, which OpenProgram does not fill in.",
  ]);
  assert.deepEqual(draftProblems(draftFromCriterion(SECURITY)), []);
});

test("a card says where it applies and how it is found, in words", () => {
  assert.equal(appliesLine(SECURITY), "each release · blocking · 10 working days before the date");
  assert.equal(
    foundByLine(SECURITY),
    'Found by label security-review, title phrase "security review"; title words "pen test" only a hint.',
  );
});

test("settings are checked as the server checks them", () => {
  assert.deepEqual(settingsProblems({ issue_type: "Task", labels: ["release-readiness"] }), []);
  assert.deepEqual(settingsProblems({ issue_type: " ", labels: ["two words"] }), [
    "The issue type is 1 to 60 characters.",
    "New issues get at most 10 labels, each without spaces.",
  ]);
});
