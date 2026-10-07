import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  GateTemplateDto,
  IssueGatesResponse,
  TrackedQuestionResponse,
} from "../../api/schema";
import {
  evaluationChip,
  evaluationLine,
  evidenceProblem,
  itemsByKind,
  questionProblem,
  questionRows,
  questionsSummary,
  scanSummary,
} from "./gateWords.ts";

const template: GateTemplateDto = {
  template_id: "engineering-delivery",
  name: "Engineering delivery",
  guards_stage: "business_testing",
  enabled: true,
  issue_types: [],
  kinds: [
    {
      key: "test_case",
      label: "Test case",
      sign_off_roles: ["dev", "sm"],
      evidence_required: true,
      gherkin: true,
    },
  ],
};

test("a gate cell uses the kind's label, not its key, and says nothing twice", () => {
  const nothing = {
    template_id: "engineering-delivery",
    state: "missing" as const,
    met: 0,
    total: 0,
    suggested: 0,
    missing_kinds: ["test_case"],
  };
  assert.equal(evaluationChip(nothing), "Not started");
  assert.equal(evaluationLine(nothing, template), "No test case kept yet");
  const part = { ...nothing, state: "open" as const, met: 2, total: 3, suggested: 1 };
  assert.equal(evaluationChip(part), "Open");
  assert.equal(
    evaluationLine({ ...part, missing_kinds: [] }, template),
    "2 of 3 met · 1 suggested from Jira to keep or dismiss",
  );
});

test("suggestions and kept items are grouped per kind; dismissed ones are gone", () => {
  const item = (id: string, status: "suggested" | "pending" | "dismissed" | "met") => ({
    item_id: id,
    issue_key: "CHK-1",
    template_id: "engineering-delivery",
    kind: "test_case",
    text: id,
    status,
    source: "description" as const,
    source_ref: "description",
    created_by: "scan",
    signed_by: null,
    signed_at: null,
    evidence_url: null,
    note: "",
  });
  const issue: IssueGatesResponse = {
    key: "CHK-1",
    title: "Payment intent API",
    stage: "in_testing",
    status: "In QA",
    evaluations: [],
    items: [item("a", "suggested"), item("b", "pending"), item("c", "dismissed"), item("d", "met")],
    passed_without: [],
  };
  const [group] = itemsByKind(issue, template);
  assert.deepEqual(
    group.suggested.map((i) => i.item_id),
    ["a"],
  );
  assert.deepEqual(
    group.confirmed.map((i) => i.item_id),
    ["b", "d"],
  );
});

test("a test case is met only with an https link to its evidence", () => {
  assert.equal(evidenceProblem("", true, "met"), "Add a link to the evidence to mark this met.");
  assert.equal(evidenceProblem("", true, "failed"), null);
  assert.equal(
    evidenceProblem("ci/run/42", false, "met"),
    "The evidence is a link starting with https://.",
  );
  assert.equal(evidenceProblem("https://ci.example/run/42", true, "met"), null);
});

test("a rescan says what it read, and what it could not", () => {
  assert.equal(
    scanSummary({ read: 3, unchanged: 2, failed: 0, suggested_items: 4, questions: 1 }),
    "Read 3 issues: 4 new suggestions, 1 question. 2 issues had not changed.",
  );
  assert.equal(
    scanSummary({ read: 0, unchanged: 0, failed: 8, suggested_items: 0, questions: 0 }),
    "8 issues could not be read from Jira.",
  );
  assert.equal(
    scanSummary({ read: 0, unchanged: 5, failed: 0, suggested_items: 0, questions: 0 }),
    "Nothing changed in Jira since the last read (5 issues).",
  );
});

test("questions to keep come first, then open ones longest waiting, then answered", () => {
  const q = (id: string, confirmed: boolean, status: "not_yet" | "answered", asked: string) =>
    ({
      question_id: id,
      issue_key: "CHK-1",
      comment_ref: "",
      asked_by: "U1",
      asked_by_name: "",
      asked_to: "U2",
      asked_to_name: "",
      asked_at: asked,
      summary: id,
      status,
      confirmed,
      status_set_by_person: false,
      answered_ref: null,
    }) satisfies TrackedQuestionResponse;
  const rows = questionRows([
    q("answered-old", true, "answered", "2026-09-01"),
    q("open-new", true, "not_yet", "2026-10-05"),
    q("suggested", false, "not_yet", "2026-10-06"),
    q("open-old", true, "not_yet", "2026-09-20"),
    q("answered-new", true, "answered", "2026-10-02"),
  ]);
  assert.deepEqual(
    rows.map((r) => r.question_id),
    ["suggested", "open-old", "open-new", "answered-new", "answered-old"],
  );
  assert.equal(questionProblem(" ", "x"), "Say who has to answer.");
  assert.equal(questionProblem("Mina Patel", ""), "Write what was asked.");
  assert.equal(questionProblem("Mina Patel", "Is 24 hours enough?"), null);
});

test("the questions summary counts only kept ones as waiting, and says what a reader can do", () => {
  const read = [
    { confirmed: false, status: "not_yet" as const },
    { confirmed: false, status: "not_yet" as const },
  ];
  // Two read from Jira and none kept: "not yet" on their rows was a claim nobody tracks.
  assert.equal(
    questionsSummary(read, false),
    "2 questions · 0 not answered yet · 2 read from Jira, not kept",
  );
  assert.equal(
    questionsSummary(read, true),
    "2 questions · 0 not answered yet · 2 read from Jira to keep or dismiss",
  );
  assert.equal(
    questionsSummary(
      [
        { confirmed: true, status: "partly" as const },
        { confirmed: true, status: "answered" as const },
      ],
      true,
    ),
    "2 questions · 1 not answered yet",
  );
  assert.equal(questionsSummary([], true), "0 questions · 0 not answered yet");
});
