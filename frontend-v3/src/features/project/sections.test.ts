import assert from "node:assert/strict";
import { test } from "node:test";

import { PROJECT_ORDER, projectAnchors, projectSections } from "./sections.ts";
import { scopeParts } from "./scopeWords.ts";

test("the Project page's order lives in one place: date, scope, quality, questions, work", () => {
  assert.deepEqual(PROJECT_ORDER, [
    "answer",
    "timeline",
    "scope",
    "requirements",
    "gates",
    "readiness",
    "questions",
    "warnings",
    "work",
  ]);
  const drawn = projectSections({
    work: "w",
    questions: "q",
    answer: "a",
    timeline: null,
    gates: "g",
    warnings: false,
  }).map((section) => section.key);
  assert.deepEqual(drawn, ["answer", "gates", "questions", "work"]);
});

test("the in-page index names the sections drawn, and is left out when it would be short", () => {
  assert.deepEqual(
    projectAnchors(["answer", "timeline", "scope", "gates", "questions", "warnings", "work"]).map(
      (anchor) => anchor.id,
    ),
    ["timeline", "scope", "gates", "questions", "warnings", "work"],
  );
  // A developer's page: gates, questions and the pods are enough without an index.
  assert.deepEqual(projectAnchors(["answer", "gates", "questions"]), []);
});

test("the scope line counts work items, from the baseline and Jira hygiene", () => {
  const since = (added: number, labelled: number, removed: number) => ({
    added,
    added_with_label: labelled,
    removed,
    added_keys: [],
    added_with_label_keys: [],
    removed_keys: [],
  });
  const baseline = (added: number, labelled: number, removed: number) =>
    ({
      in_scope: 10,
      active: {
        requirements: 8,
        planned_on: "2026-10-05",
        change_label: "CR",
        since: since(added, labelled, removed),
      },
    }) as unknown as Parameters<typeof scopeParts>[0];
  assert.deepEqual(scopeParts(baseline(2, 1, 0), { open: 3 }), [
    "8 at planning (5 Oct)",
    "+2 added since, 1 without the CR label",
    "3 open Jira hygiene findings",
  ]);
  assert.deepEqual(scopeParts(baseline(2, 2, 1), { open: 0 }), [
    "8 at planning (5 Oct)",
    "+2 added since, all with the CR label",
    "1 removed",
    "no open Jira hygiene finding",
  ]);
  // A scrum master reads hygiene only; before planning, the count in scope.
  assert.deepEqual(scopeParts(undefined, { open: 1 }), ["1 open Jira hygiene finding"]);
  assert.deepEqual(scopeParts({ in_scope: 4, active: null }, undefined), [
    "Planning is not marked complete yet · 4 in scope today",
  ]);
  assert.equal(scopeParts(undefined, undefined), null);
});
