import assert from "node:assert/strict";
import { test } from "node:test";

import type { HygieneRuleDto } from "../../api/schema";
import {
  draftFromRule,
  draftProblems,
  modeLine,
  newRuleDraft,
  ownerLine,
  previewLine,
  ruleFromDraft,
  settingsBody,
  settingsProblems,
  splitLabels,
  unknownPlaceholders,
} from "./hygieneForm.ts";

const EXAMPLE: HygieneRuleDto = {
  rule_id: "story-without-estimate",
  version: 0,
  name: "Story without an estimate",
  scope: "project",
  select: {
    matchers: [{ kind: "issue_type", value: "Story" }],
    stage_filter: "before",
    stage: "in_development",
    every_type: false,
  },
  condition: {
    kind: "missing_field",
    labels: [],
    field: "story_points",
    gate_kind: "",
    stage: null,
    working_days: 0,
    points_over: 0,
  },
  severity: "list_only",
  need: "fix",
  owner: { kind: "pod_scrum_master", profile: "" },
  blocking_before: null,
  suggested_fix: "Estimate {key} before it starts.",
  enabled: true,
};

test("an example round-trips through the form unchanged", () => {
  const back = ruleFromDraft(draftFromRule(EXAMPLE));
  assert.deepEqual(back.select, EXAMPLE.select);
  assert.deepEqual(back.condition, EXAMPLE.condition);
  assert.deepEqual(back.owner, EXAMPLE.owner);
  assert.equal(back.rule_id, EXAMPLE.rule_id);
  assert.equal(back.severity, "list_only");
  assert.deepEqual(draftProblems(draftFromRule(EXAMPLE)), []);
});

test("only the chosen condition's own values are sent", () => {
  const draft = {
    ...newRuleDraft(),
    name: "No movement",
    condition: "stale_in_status" as const,
    labelsText: "ignored",
    daysText: "7",
  };
  const rule = ruleFromDraft(draft);
  assert.equal(rule.condition.kind, "stale_in_status");
  assert.equal(rule.condition.working_days, 7);
  assert.equal(rule.condition.stage, "in_development");
  assert.deepEqual(rule.condition.labels, []);
  assert.equal(rule.condition.field, null);
  assert.equal(rule.select?.stage, null);
});

test("the change label rule sends the tenant's labels, or none to follow the baseline", () => {
  const draft = {
    ...newRuleDraft(),
    name: "Added after planning",
    condition: "added_after_baseline_without_label" as const,
  };
  assert.deepEqual(ruleFromDraft(draft).condition.labels, []);
  assert.deepEqual(ruleFromDraft({ ...draft, labelsText: "agreed-change, CR" }).condition.labels, [
    "agreed-change",
    "CR",
  ]);
});

test("the form says what the server would refuse", () => {
  const problems = draftProblems({
    ...newRuleDraft(),
    name: " ",
    matchers: [{ kind: "label", value: "two words" }],
    condition: "stale_in_status",
    daysText: "0",
    ownerKind: "profile",
    ownerProfile: "",
    suggestedFix: "Ask {customer} about {key}",
  });
  assert.deepEqual(problems, [
    "A rule needs a name of 1 to 80 characters.",
    'The label "two words" has a space; Jira labels have none.',
    "The days without a change are 1 to 60.",
    "Pick the profile whose holder is asked.",
    "The suggested fix uses {customer}, which OpenProgram does not fill in.",
  ]);
  assert.deepEqual(
    draftProblems({ ...newRuleDraft(), name: "Labels", condition: "missing_label" }),
    ["Name the label, or labels, an issue should have."],
  );
  assert.deepEqual(
    draftProblems({ ...newRuleDraft(), name: "Big", condition: "too_big", pointsText: "0" }),
    ["The story points an issue may have are above 0 and at most 1000."],
  );
});

test("labels and placeholders", () => {
  assert.deepEqual(splitLabels(" CR, cr ,, scope-change "), ["CR", "scope-change"]);
  assert.deepEqual(unknownPlaceholders("{key} {nope} {title} {nope}"), ["nope"]);
});

test("the scan's settings", () => {
  assert.deepEqual(settingsBody({ enabled: true, labelsText: "CR, agreed", daysText: "5" }), {
    enabled: true,
    change_labels: ["CR", "agreed"],
    stale_working_days: 5,
  });
  assert.deepEqual(settingsProblems({ enabled: true, labelsText: "a b", daysText: "0" }), [
    "Name at most 10 change labels, each without spaces.",
    "The days without a change are 1 to 60.",
  ]);
});

test("what Try on a project says per scope", () => {
  const scope = { kind: "project" as const, id: "checkout", name: "Checkout Revamp" };
  assert.equal(
    previewLine({ scope, looked_at: 12, found: 3, issues: [], needs_baseline: false }),
    "Checkout Revamp: 3 of 12 issues break it.",
  );
  assert.equal(
    previewLine({ scope, looked_at: 1, found: 1, issues: [], needs_baseline: false }),
    "Checkout Revamp: 1 of 1 issue breaks it.",
  );
  assert.match(
    previewLine({ scope, looked_at: 4, found: 0, issues: [], needs_baseline: true }),
    /no planning baseline yet/,
  );
  assert.equal(modeLine({ severity: "list_only", need: "fix" }), "Lists only");
  assert.equal(modeLine({ severity: "ask", need: "decision" }), "Asks the owner for a decision");
});

test("the rule card names the owner, a profile by its label", () => {
  assert.equal(ownerLine({ kind: "assignee", profile: "" }, {}), "Owner: the assignee");
  assert.equal(
    ownerLine({ kind: "profile", profile: "qa" }, { qa: "QA and test" }),
    "Owner: the pod's QA and test",
  );
  assert.equal(ownerLine(undefined, {}), "Owner: the pod's scrum master");
});
