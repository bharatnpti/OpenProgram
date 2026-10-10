import assert from "node:assert/strict";
import { test } from "node:test";

import type { ReadinessFindingResponse } from "../../api/schema";
import {
  answerLine,
  decisionWords,
  draftFacts,
  footerWords,
  linkTarget,
  reasonProblem,
  releaseLineWords,
  rowGroups,
  stateLine,
  stateTone,
  urgentLine,
} from "./readinessWords.ts";

type Finding = ReadinessFindingResponse;

function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: "rf_1",
    criterion: {
      criterion_id: "security-review",
      name: "Security review",
      severity: "blocking",
      required_before: "production",
      evidence: "A security review.",
      needs_done: true,
    },
    scope: { kind: "release", id: "rel-1", name: "Release 1" },
    state: "missing",
    done: false,
    decided_by: "rules",
    held: false,
    evidence: [],
    candidates: [],
    reason: "",
    urgency: {
      kind: "due_soon",
      due_on: "2026-10-21",
      working_days_left: 8,
      delivery_date: "2026-11-04",
      stage_key: null,
    },
    person_decision: null,
    suggestion: null,
    can: {
      create: false,
      create_off_reason: null,
      link: true,
      not_applicable: false,
      dismiss: true,
      edit: true,
      reopen: false,
      draft: false,
    },
    checked_at: "2026-10-10T07:45:00Z",
    ...overrides,
  };
}

test("each row says its state and how soon it is needed, in words", () => {
  assert.equal(stateLine(finding()), "Missing · due 21 Oct (8 working days)");
  assert.equal(
    stateLine(
      finding({ urgency: { ...finding().urgency, kind: "overdue", working_days_left: -2 } }),
    ),
    "Missing · was due 21 Oct",
  );
  assert.equal(
    stateLine(
      finding({
        urgency: { ...finding().urgency, kind: "stage_reached", stage_key: "CHK-44" },
      }),
    ),
    "Missing · CHK-44 reached production",
  );
  assert.equal(
    stateLine(
      finding({
        urgency: {
          kind: "no_date",
          due_on: null,
          working_days_left: null,
          delivery_date: null,
          stage_key: null,
        },
      }),
    ),
    "Missing · no date",
  );
  assert.equal(
    stateLine(
      finding({
        state: "unsure",
        urgency: { ...finding().urgency, kind: "later" },
        candidates: [{ issue_key: "CHK-13", title: "x", why: "title_words" }],
      }),
    ),
    "Unsure · title words only",
  );
  assert.equal(stateLine(finding({ state: "covered", done: false })), "Covered · in progress");
  assert.equal(stateLine(finding({ state: "covered", done: true })), "Covered · done");
  assert.equal(stateLine(finding({ state: "not_applicable" })), "Not applicable");
});

test("colour means the state only: red missing, amber unsure, green done", () => {
  assert.equal(stateTone(finding()), "danger");
  assert.equal(stateTone(finding({ state: "unsure" })), "warning");
  assert.equal(stateTone(finding({ state: "covered", done: true })), "success");
  assert.equal(stateTone(finding({ state: "covered" })), "info");
  assert.equal(stateTone(finding({ state: "not_applicable" })), "neutral");
});

test("the answer line counts what is missing and unsure, leaving out not applicable", () => {
  const findings = [
    finding(),
    finding({ state: "unsure" }),
    finding({ state: "covered", done: true }),
    finding({ state: "not_applicable" }),
  ];
  assert.equal(
    answerLine({ findings }, "Release 1"),
    "Release 1 is missing 1 of 3 criteria it needs before production; 1 more is unsure.",
  );
  assert.equal(
    answerLine({ findings: [finding({ state: "unsure" }), finding({ state: "covered" })] }, "R"),
    "R has evidence for 1 of 2 criteria it needs before production; 1 is unsure.",
  );
  assert.equal(
    answerLine({ findings: [finding({ state: "covered", done: true })] }, "R"),
    "R has evidence in Jira for the one criterion it needs before production.",
  );
});

test("the most urgent blocking gap leads; held and advisory ones never do", () => {
  assert.equal(urgentLine({ findings: [finding()] }), "Security review is due in 8 working days.");
  assert.equal(urgentLine({ findings: [finding({ held: true })] }), null);
  assert.equal(
    urgentLine({
      findings: [finding({ criterion: { ...finding().criterion, severity: "advisory" } })],
    }),
    null,
  );
  assert.equal(
    urgentLine({
      findings: [
        finding({
          scope: { kind: "pod", id: "pod-pay", name: "Payments Pod" },
          urgency: { ...finding().urgency, kind: "stage_reached", stage_key: "CHK-2" },
        }),
      ],
    }),
    "CHK-2 reached production without it: Security review for Payments Pod.",
  );
});

test("pod rows sit under their pod, after the release's and the project's", () => {
  const pod = finding({ scope: { kind: "pod", id: "pod-pay", name: "Payments Pod" } });
  const project = finding({ scope: { kind: "project", id: "p", name: "Checkout" } });
  const groups = rowGroups([pod, finding(), project]);
  assert.deepEqual(
    groups.map((group) => [group.heading, group.rows.length]),
    [
      [null, 2],
      ["Payments Pod", 1],
    ],
  );
});

test("a link is a Jira key or an https record; a reason is 3 to 300 characters", () => {
  assert.deepEqual(linkTarget(" chk-13 "), { issue_key: "CHK-13", note: "" });
  assert.deepEqual(linkTarget("https://records.example/1", " ok "), {
    evidence_url: "https://records.example/1",
    note: "ok",
  });
  assert.deepEqual(linkTarget("http://records.example/1"), {
    problem: "A record's link starts with https://.",
  });
  assert.deepEqual(linkTarget("security"), { problem: "A Jira key looks like CHK-12." });
  assert.equal(reasonProblem("no"), "Give a reason of 3 to 300 characters.");
  assert.equal(reasonProblem("Internal API only"), null);
});

test("release lines, footer, decisions and draft facts", () => {
  assert.equal(
    releaseLineWords({ name: "Release 1", missing: 1, unsure: 1, total: 6 }),
    "Release 1: 1 missing, 1 unsure",
  );
  assert.equal(
    releaseLineWords({ name: "Release 2", missing: 0, unsure: 0, total: 4 }),
    "Release 2: all 4 covered",
  );
  const time = (iso: string) => iso.slice(11, 16);
  const agent = {
    enabled: true,
    create_in_jira: false,
    writeback_enabled: false,
    last_run_at: "2026-10-10T07:45:00Z",
    last_run_status: "ok" as const,
    data_as_of: "2026-10-10T07:00:00Z",
    stale: false,
  };
  assert.equal(footerWords(agent, time), "Checked 07:45 · Jira data from 07:00");
  assert.match(
    footerWords({ ...agent, stale: true }, time),
    /^The Jira sync is failing or behind; this shows Jira as of 07:00\./,
  );
  assert.equal(
    decisionWords({
      kind: "not_applicable",
      by: "U1001",
      by_name: "Asha Rao",
      at: "2026-10-02T10:00:00Z",
      reason: "Internal API only",
      issue_key: "",
      url: "",
      note: "",
      created: false,
    }),
    "Not applicable: Internal API only (Asha Rao, 2 Oct)",
  );
  assert.equal(
    draftFacts({ project_key: "CHK", issue_type: "Task", labels: ["security-review"] }),
    "CHK · Task · security-review · unassigned",
  );
});
