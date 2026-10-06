import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  GateBoardResponse,
  GateItemResponse,
  GateTemplateDto,
  IssueGatesResponse,
  TrackedQuestionResponse,
} from "../../api/schema";
import {
  daysWaiting,
  draftFromTemplate,
  draftProblems,
  evaluationLabel,
  evidenceProblem,
  gateSummary,
  issueRows,
  itemsByKind,
  kindKey,
  mayReadGateBoard,
  maySignOff,
  newKindDraft,
  passedWithoutLine,
  questionRows,
  scanSummary,
  signOffLine,
  splitList,
  templateFromDraft,
  withArticle,
  withLabel,
} from "./gates.ts";

const ACCEPTANCE: GateTemplateDto = {
  template_id: "business-acceptance",
  name: "Business acceptance",
  guards_stage: "production",
  issue_types: [],
  enabled: true,
  kinds: [
    {
      key: "acceptance",
      label: "Acceptance criterion",
      sign_off_roles: ["po", "mgr"],
      evidence_required: false,
      headings: ["Acceptance criteria", "AC"],
      gherkin: false,
    },
  ],
};

function item(overrides: Partial<GateItemResponse>): GateItemResponse {
  return {
    item_id: "i1",
    issue_key: "CHK-1",
    template_id: "business-acceptance",
    kind: "acceptance",
    text: "Pays with a saved card",
    status: "pending",
    source: "description",
    source_ref: "description",
    created_by: "scan",
    signed_by: null,
    signed_at: null,
    evidence_url: null,
    note: "",
    ...overrides,
  };
}

function issue(key: string, overrides: Partial<IssueGatesResponse> = {}): IssueGatesResponse {
  return {
    key,
    title: `${key} work`,
    stage: "in_development",
    status: "In Progress",
    evaluations: [
      {
        template_id: "business-acceptance",
        state: "open",
        met: 0,
        total: 1,
        suggested: 0,
        missing_kinds: [],
      },
    ],
    items: [],
    passed_without: [],
    ...overrides,
  };
}

function question(overrides: Partial<TrackedQuestionResponse>): TrackedQuestionResponse {
  return {
    question_id: "q1",
    issue_key: "CHK-1",
    comment_ref: "10",
    asked_by: "qa",
    asked_by_name: "Quinn",
    asked_to: "po",
    asked_to_name: "Pat",
    asked_at: "2026-09-28T09:00:00Z",
    summary: "Is 3-D Secure in scope?",
    status: "not_yet",
    confirmed: true,
    status_set_by_person: false,
    answered_ref: null,
    ...overrides,
  };
}

function board(issues: IssueGatesResponse[], questions: TrackedQuestionResponse[] = []) {
  return {
    project_id: "checkout",
    release_id: null,
    templates: [ACCEPTANCE],
    issues,
    questions,
    actor_names: {},
  } satisfies GateBoardResponse;
}

test("the issues that need someone come first, past-the-gate before suggestions", () => {
  const rows = issueRows(
    board([
      issue("CHK-1"),
      issue("CHK-2", { items: [item({ status: "suggested" })] }),
      issue("CHK-3", { stage: "production", passed_without: ["Business acceptance"] }),
    ]),
    "attention",
  );

  assert.deepEqual(
    rows.map((row) => row.key),
    ["CHK-3", "CHK-2"],
  );
});

test("the summary counts passed issues, suggestions and open questions", () => {
  const passed = issue("CHK-1", {
    evaluations: [
      {
        template_id: "business-acceptance",
        state: "passed",
        met: 2,
        total: 2,
        suggested: 0,
        missing_kinds: [],
      },
    ],
  });
  const summary = gateSummary(
    board(
      [passed, issue("CHK-2", { items: [item({ status: "suggested" })] })],
      [question({}), question({ question_id: "q2", confirmed: false })],
    ),
  );

  assert.deepEqual(summary, {
    issues: 2,
    passedAll: 1,
    passedWithout: 0,
    suggestions: 1,
    openQuestions: 1,
    questionsToConfirm: 1,
  });
});

test("an evaluation says what it counts", () => {
  const base = issue("CHK-1").evaluations[0];
  assert.equal(evaluationLabel({ ...base, met: 1, total: 3 }), "1 of 3 met");
  assert.equal(evaluationLabel({ ...base, total: 0, suggested: 2 }), "2 suggestions");
  assert.equal(
    evaluationLabel({ ...base, total: 0, suggested: 0, state: "missing" }),
    "Nothing confirmed",
  );
  assert.equal(
    passedWithoutLine(
      issue("CHK-9", { passed_without: ["Business acceptance", "Engineering delivery"] }),
      "Production",
    ),
    "CHK-9 reached Production without Business acceptance and Engineering delivery passing.",
  );
});

test("items are split per kind into suggestions and confirmed ones", () => {
  const [group] = itemsByKind(
    issue("CHK-1", {
      items: [
        item({ item_id: "a", status: "suggested" }),
        item({ item_id: "b", status: "met" }),
        item({ item_id: "c", template_id: "engineering-delivery", kind: "test_case" }),
      ],
    }),
    ACCEPTANCE,
  );

  assert.deepEqual(
    group.suggested.map((entry) => entry.item_id),
    ["a"],
  );
  assert.deepEqual(
    group.confirmed.map((entry) => entry.item_id),
    ["b"],
  );
});

test("only the named roles, or an admin, sign a kind off", () => {
  const [kind] = ACCEPTANCE.kinds;
  assert.equal(maySignOff(kind, ["dev"]), false);
  assert.equal(maySignOff(kind, ["dev", "po"]), true);
  assert.equal(maySignOff(kind, ["admin"]), true);
  assert.equal(signOffLine(kind), "Signed off by product owner or manager.");
  assert.equal(
    signOffLine({ ...kind, sign_off_roles: ["dev"], evidence_required: true }),
    "Signed off by developer, with a link to the evidence.",
  );
});

test("whoever reads the project's progress or works its gates reads the board", () => {
  // Developer and scrum master: they sign test cases off, but read no progress.
  assert.equal(mayReadGateBoard({ canReadProjectProgress: false, canEditGates: true }), true);
  // Executive: reads it, changes nothing.
  assert.equal(mayReadGateBoard({ canReadProjectProgress: true, canEditGates: false }), true);
  assert.equal(mayReadGateBoard({ canReadProjectProgress: false, canEditGates: false }), false);
});

test("evidence is a link, and needed only to mark a kind that asks for it met", () => {
  assert.equal(evidenceProblem("", true, "met"), "Add a link to the evidence to mark this met.");
  assert.equal(evidenceProblem("", true, "waived"), null);
  assert.equal(
    evidenceProblem("ci/run/7", false, "met"),
    "The evidence is a link starting with https://.",
  );
  assert.equal(evidenceProblem("https://ci.example.com/run/7", true, "met"), null);
});

test("questions to confirm come first, then the longest waiting", () => {
  const rows = questionRows(
    [
      question({ question_id: "late", asked_at: "2026-10-02T09:00:00Z" }),
      question({ question_id: "early", asked_at: "2026-09-21T09:00:00Z" }),
      question({ question_id: "new", confirmed: false, asked_at: "2026-10-04T09:00:00Z" }),
      question({ question_id: "done", status: "answered" }),
    ],
    false,
  );

  assert.deepEqual(
    rows.map((row) => row.question_id),
    ["new", "early", "late"],
  );
  assert.equal(daysWaiting("2026-09-28T23:30:00Z", "2026-10-05"), 7);
});

test("a scan says what it read and found", () => {
  assert.equal(
    scanSummary({ read: 3, unchanged: 2, failed: 0, suggested_items: 4, questions: 1 }),
    "Read 3 issues: 4 new suggestions, 1 question. 2 issues had not changed.",
  );
  assert.equal(
    scanSummary({ read: 0, unchanged: 5, failed: 0, suggested_items: 0, questions: 0 }),
    "Nothing changed in Jira since the last read (5 issues).",
  );
});

test("a template survives the editor, and a new kind's key follows its label", () => {
  const draft = draftFromTemplate(ACCEPTANCE);
  assert.deepEqual(templateFromDraft(draft), ACCEPTANCE);

  const added = withLabel(newKindDraft(), "Security review");
  assert.equal(added.key, "security_review");
  assert.equal(withLabel(draft.kinds[0], "Criterion").key, "acceptance");
  assert.equal(kindKey("  Données / tests "), "données_tests");
  assert.deepEqual(splitList("Story, story,  Bug\nEpic,"), ["Story", "Bug", "Epic"]);
  assert.equal(withArticle("Acceptance criterion"), "an acceptance criterion");
  assert.equal(withArticle("Test case"), "a test case");
});

test("the editor says what the server would refuse", () => {
  const draft = {
    ...draftFromTemplate(ACCEPTANCE),
    name: " ",
    kinds: [
      { ...newKindDraft(), signOffRoles: [] },
      withLabel(newKindDraft(), "Review"),
      withLabel(newKindDraft(), "review"),
    ],
  };

  assert.deepEqual(draftProblems(draft), [
    "Give the gate a name.",
    "Kind 1 needs a label.",
    "Say who signs off Kind 1.",
    'Two kinds are both called "review".',
  ]);
});
