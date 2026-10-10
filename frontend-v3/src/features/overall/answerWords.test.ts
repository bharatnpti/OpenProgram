import assert from "node:assert/strict";
import { test } from "node:test";

import { answerChip, answerEdge, answerMeta, gateAnswer, scopeAnswer } from "./answerWords.ts";

type Verdict = "on_track" | "at_risk" | "off_track" | "done" | "no_date" | "not_enough_data";

function scope(over: {
  verdict: Verdict;
  target?: string | null;
  p50?: string | null;
  p85?: string | null;
  latest?: string | null;
  key?: string | null;
  undated?: number;
  total?: number;
  open?: number;
}) {
  return {
    verdict: over.verdict,
    target: over.target === undefined ? "2026-12-15" : over.target,
    target_source: "committed" as const,
    commitment: {
      target_date: null,
      original_date: null,
      times_moved: 0,
      moved_days: null,
      changes: [],
    },
    history: {
      p50: over.p50 ?? null,
      p85: over.p85 ?? null,
      remaining: 13,
      unit: "requirements",
      sample_days: 3,
      completed_in_sample: 0,
      reason: null,
      needed_days: 10,
    },
    team: {
      latest: over.latest ?? null,
      latest_key: over.key ?? null,
      dated: 7,
      undated: over.undated ?? 0,
    },
    total: over.total ?? 18,
    open: over.open ?? 13,
  };
}

test("the answer moves the verdict's cause up into one sentence about the project", () => {
  assert.equal(
    scopeAnswer(
      scope({ verdict: "at_risk", latest: "2026-10-09", key: "CHK-4", undated: 6 }),
      "Checkout Revamp",
    ),
    "Checkout Revamp is at risk because 6 open requirements have no ETA or due date.",
  );
  assert.equal(
    scopeAnswer(
      scope({ verdict: "at_risk", p50: "2026-12-02", p85: "2026-12-21" }),
      "Checkout Revamp",
    ),
    "Checkout Revamp is at risk because history is 50% likely to finish by Wed 2 Dec, in time, but 85% likely only by Mon 21 Dec, after the delivery date.",
  );
  assert.equal(
    scopeAnswer(
      scope({ verdict: "at_risk", latest: "2026-11-01", undated: 3 }),
      "Payments Pod, the pod you run,",
    ),
    "Payments Pod, the pod you run, is at risk because 3 open requirements have no ETA or due date.",
  );
});

test("every verdict has its sentence, and nothing counted says so", () => {
  assert.equal(
    scopeAnswer(scope({ verdict: "no_date", target: null, undated: 8 }), "Checkout Revamp"),
    "Checkout Revamp has no committed date, so there is nothing to judge the forecast against.",
  );
  assert.equal(
    scopeAnswer(
      scope({ verdict: "on_track", p50: "2026-12-01", p85: "2026-12-10" }),
      "Checkout Revamp",
    ),
    "Checkout Revamp is on track: history is 85% likely to finish by Thu 10 Dec, by the delivery date.",
  );
  assert.equal(
    scopeAnswer(
      scope({ verdict: "off_track", latest: "2027-01-08", key: "CHK-9" }),
      "Checkout Revamp",
    ),
    "Checkout Revamp is off track because the team's latest date, Fri 8 Jan (CHK-9), is after the delivery date.",
  );
  assert.equal(
    scopeAnswer(scope({ verdict: "not_enough_data", undated: 8 }), "Checkout Revamp"),
    "Checkout Revamp: not enough history to forecast, and none of the 8 open requirements has an ETA or due date to go by.",
  );
  assert.equal(
    scopeAnswer(scope({ verdict: "done", open: 0 }), "Release 1.0"),
    "Release 1.0 is done: every requirement is in production.",
  );
  assert.equal(
    scopeAnswer(scope({ verdict: "done", total: 0, open: 0 }), "Payments Pod"),
    "Payments Pod has no requirements counted yet, so there is nothing to forecast.",
  );
});

test("the chip and the edge follow the verdict; no date is red and needs no chip", () => {
  const noDate = scope({ verdict: "no_date", target: null });
  assert.equal(answerChip(noDate), null);
  assert.equal(answerEdge(noDate), "red");
  assert.deepEqual(answerChip(scope({ verdict: "at_risk" })), {
    label: "At risk",
    tone: "warning",
  });
  assert.equal(answerEdge(scope({ verdict: "at_risk" })), "amber");
  assert.equal(answerEdge(scope({ verdict: "done", total: 0, open: 0 })), "unknown");
});

test("the meta line carries how much is done, by points when the requirements have them", () => {
  assert.equal(
    answerMeta({ total: 18, open: 13 }, "the whole project", null),
    "5 of 18 requirements in production · 28% by count · the whole project",
  );
  assert.equal(
    answerMeta({ total: 24, open: 17 }, "the whole project", { percent: 58.2, hasPoints: true }),
    "7 of 24 requirements in production · 58% by story points · the whole project",
  );
  assert.equal(answerMeta({ total: 0, open: 0 }, "Release 1.0", null), "Release 1.0");
});

const template = (id: string, name: string) => ({
  template_id: id,
  name,
  guards_stage: "production" as const,
  kinds: [],
  enabled: true,
});

test("a developer's answer is where the gates stand and what waits on them", () => {
  const issue = (
    key: string,
    states: ("passed" | "missing" | "failed")[],
    without: string[],
    suggested = 0,
  ) => ({
    key,
    title: key,
    stage: "production" as const,
    status: null,
    evaluations: states.map((state, i) => ({
      template_id: `t${i}`,
      state,
      met: 0,
      total: 0,
      suggested: 0,
      missing_kinds: [],
    })),
    items: Array.from({ length: suggested }, (_, i) => ({
      item_id: `${key}-${i}`,
      issue_key: key,
      template_id: "t0",
      kind: "acceptance",
      text: "x",
      status: "suggested" as const,
      source: "description" as const,
      source_ref: "",
      created_by: "scan",
      signed_by: null,
      signed_at: null,
      evidence_url: null,
      note: "",
    })),
    passed_without: without,
  });
  const question = (to: string, toName: string, status: "not_yet" | "answered") => ({
    question_id: to + status,
    issue_key: "CHK-12",
    comment_ref: "",
    asked_by: "U1005",
    asked_by_name: "Zoe Almeida",
    asked_to: to,
    asked_to_name: toName,
    asked_at: "2026-10-06T10:00:00Z",
    summary: "?",
    status,
    confirmed: true,
    status_set_by_person: false,
    answered_ref: null,
  });
  const board = {
    templates: [template("t0", "Business acceptance"), template("t1", "Engineering delivery")],
    issues: [
      issue("CHK-3", ["missing", "missing"], ["Business acceptance", "Engineering delivery"], 7),
      issue("CHK-4", ["passed", "passed"], []),
    ],
    questions: [
      question("U1002", "Liam Chen", "not_yet"),
      question("U1003", "Mina Patel", "not_yet"),
    ],
  };
  assert.deepEqual(gateAnswer(board, { id: "U1002", name: "Liam Chen" }), {
    text: "1 of 2 requirements passed every gate, and 1 moved on without passing.",
    meta: "7 suggestions from Jira wait to be kept or dismissed · 1 question was asked of you",
    edge: "red",
  });
  // A question typed with the person's name is theirs too; an answered one waits on nobody.
  const typed = {
    ...board,
    questions: [
      question("Liam Chen", "Liam Chen", "not_yet"),
      question("U1002", "Liam Chen", "answered"),
    ],
  };
  assert.match(
    gateAnswer(typed, { id: "U1002", name: "Liam Chen" }).meta ?? "",
    /1 question was asked of you$/,
  );
  assert.equal(
    gateAnswer({ ...board, templates: [] }, { id: null, name: null }).text,
    "No gates are switched on for this project.",
  );
});
