import assert from "node:assert/strict";
import { test } from "node:test";

import type { HygieneFindingResponse, HygieneRuleRowDto } from "../../api/schema";
import {
  answerLine,
  decisionWords,
  findingsOf,
  footerWords,
  hasSection,
  modeWords,
  openWords,
  ownerWords,
  reasonProblem,
  rowCounts,
} from "./hygieneWords.ts";

function row(overrides: Partial<HygieneRuleRowDto> = {}): HygieneRuleRowDto {
  return {
    rule_id: "story-without-estimate",
    name: "Story without an estimate",
    stated: "Story issues before In development without story points; lists them.",
    severity: "list_only",
    need: "fix",
    condition: "missing_field",
    blocking: false,
    open: 0,
    new_today: 0,
    oldest_days: null,
    held: 0,
    decided: 0,
    ...overrides,
  };
}

function finding(overrides: Partial<HygieneFindingResponse> = {}): HygieneFindingResponse {
  return {
    finding_id: "hf_1",
    rule_id: "story-without-estimate",
    issue_key: "CHK-3",
    title: "Card art",
    scope: { kind: "project", id: "checkout", name: "Checkout Revamp" },
    stage: "raised",
    what: "No story points",
    owner: { name: "Kai Lund", via: "assignee", profile_label: null },
    suggested_fix: "Estimate CHK-3 before it starts.",
    state: "open",
    held: false,
    first_seen_at: "2026-10-12T09:45:00Z",
    open_days: 2,
    resolution: null,
    decision: null,
    can: { waive: true, not_applicable: true, recheck: true, reopen: false },
    ...overrides,
  };
}

test("the answer line counts findings and rules, never people", () => {
  assert.equal(answerLine({ open: 0, rules: [row()] }), "No issue breaks a rule today.");
  assert.equal(
    answerLine({
      open: 5,
      rules: [row({ open: 3, new_today: 1 }), row({ open: 2, new_today: 1 })],
    }),
    "5 open findings under 2 rules, 2 of them new today.",
  );
  assert.equal(
    answerLine({ open: 1, rules: [row({ open: 1, new_today: 1 }), row()] }),
    "1 open finding under 1 rule, all new today.",
  );
});

test("a rule's counts and what it does when it finds something", () => {
  assert.equal(rowCounts(row()), "Nothing open");
  assert.equal(
    rowCounts(row({ open: 4, new_today: 1, oldest_days: 6, decided: 1 })),
    "4 open · 1 new today · oldest 6 days · 1 set aside",
  );
  assert.equal(rowCounts(row({ open: 1, oldest_days: 0 })), "1 open");
  assert.equal(modeWords(row()), "Lists only");
  assert.equal(modeWords(row({ severity: "ask" })), "Asks the owner");
});

test("who is asked, and how they were found", () => {
  assert.equal(ownerWords(finding().owner), "Kai Lund, the assignee");
  assert.equal(
    ownerWords({ name: "Sofia Berg", via: "profile", profile_label: "QA and test" }),
    "Sofia Berg, the pod's QA and test",
  );
  assert.equal(
    ownerWords({ name: "Ira Novak", via: "pod_scrum_master", profile_label: null }),
    "Ira Novak, the pod's scrum master",
  );
  assert.equal(ownerWords({ name: null, via: null, profile_label: null }), "Nobody named yet");
});

test("what a person decided, and how long it has been open", () => {
  assert.equal(decisionWords(finding()), null);
  assert.equal(
    decisionWords(
      finding({
        state: "waived",
        decision: { by_name: "Mina Patel", at: "2026-10-13T10:00:00Z", reason: "spike work" },
      }),
    ),
    "Waived by Mina Patel: spike work",
  );
  assert.equal(
    decisionWords(
      finding({
        state: "not_applicable",
        decision: { by_name: null, at: "2026-10-13T10:00:00Z", reason: "not ours" },
      }),
    ),
    "Set aside by a team member: not ours",
  );
  assert.equal(openWords(finding({ open_days: 0 })), "Open since today");
  assert.equal(openWords(finding({ open_days: 1 })), "Open 1 day");
});

test("a rule's findings: open ones by key first, then what people decided", () => {
  const all = [
    finding({ finding_id: "a", issue_key: "CHK-10" }),
    finding({ finding_id: "b", issue_key: "CHK-9" }),
    finding({ finding_id: "c", issue_key: "CHK-2", state: "waived" }),
    finding({ finding_id: "d", rule_id: "other" }),
  ];
  const { open, decided } = findingsOf(all, "story-without-estimate");
  assert.deepEqual(
    open.map((item) => item.issue_key),
    ["CHK-9", "CHK-10"],
  );
  assert.deepEqual(
    decided.map((item) => item.issue_key),
    ["CHK-2"],
  );
});

test("the footer says when Jira was read, and why something waits", () => {
  const time = (iso: string) => iso.slice(11, 16);
  assert.equal(
    footerWords(
      {
        enabled: true,
        stale: false,
        last_run_at: "2026-10-14T09:45:00Z",
        last_run_status: "ok",
        data_as_of: "2026-10-14T09:00:00Z",
      },
      0,
      time,
    ),
    "Checked 09:45 · Jira data from 09:00.",
  );
  assert.match(
    footerWords(
      {
        enabled: true,
        stale: true,
        last_run_at: null,
        last_run_status: null,
        data_as_of: "2026-10-12T09:00:00Z",
      },
      2,
      time,
    ),
    /^The Jira sync is failing or behind; this shows Jira as of 09:00\./,
  );
  assert.match(
    footerWords(
      { enabled: true, stale: false, last_run_at: null, last_run_status: null, data_as_of: null },
      1,
      time,
    ),
    /1 finding read while the sync was behind waits/,
  );
});

test("a reason is 3 to 300 characters, and the section shows only once set up", () => {
  assert.equal(reasonProblem("no"), "Give a reason of 3 to 300 characters.");
  assert.equal(reasonProblem("Spike work"), null);
  const scan = {
    enabled: false,
    stale: false,
    last_run_at: null,
    last_run_status: null,
    data_as_of: null,
  };
  assert.equal(hasSection({ scan, rules: [] }), false);
  assert.equal(hasSection({ scan: { ...scan, enabled: true }, rules: [] }), true);
});
