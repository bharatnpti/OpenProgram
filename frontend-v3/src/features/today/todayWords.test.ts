import assert from "node:assert/strict";
import { test } from "node:test";

import type { JiraChangeDto, ProjectAskDto } from "../../api/schema";
import {
  answerButtons,
  askAge,
  askWho,
  askWhy,
  changeTitle,
  checkinStateWords,
  dueDateWords,
  footerLine,
  gateWords,
  mergeAsks,
  repliesWords,
  openWarningsLine,
  warningsWords,
} from "./todayWords.ts";

const proposal = (gate: JiraChangeDto["gate"], answerable = true): JiraChangeDto => ({
  id: "proposal:1",
  kind: "proposal",
  issue_key: "CHK-4",
  issue_title: "Refund flow",
  why: "You said it is done in today's check-in.",
  asked_on: "2026-10-13",
  target_label: "Done",
  gate,
  answerable,
  holds_checkin: true,
  due_on: null,
  eta_on: null,
});

const gate = (
  state: NonNullable<JiraChangeDto["gate"]>["state"],
  merge_requests: string[] = [],
) => ({
  state,
  yes_allowed: state === "open" || state === "no_change",
  tracker_status: "In Progress",
  merge_requests,
});

const ask = (overrides: Partial<ProjectAskDto> = {}): ProjectAskDto => ({
  need: "fix",
  need_label: "Fix",
  what: "CHK-4: waiting on the sandbox",
  who: "Ira Novak",
  team: "Payments Pod",
  waited_days: 3,
  detail: null,
  issue_key: "CHK-4",
  level: 1,
  escalated_to: null,
  escalation_label: null,
  ...overrides,
});

test("replies say who replied of how many, and the tracker's fills apart, never green", () => {
  assert.deepEqual(repliesWords({ people: 14, replied: 8, inferred: 3, waiting: 3 }), {
    count: "8/14",
    headline: "8 of 14 people replied",
    inferred: "3 filled from Jira, marked as inferred, never green.",
    waiting: "3 not yet.",
  });
  assert.equal(repliesWords({ people: 1, replied: 1, inferred: 0, waiting: 0 }).inferred, null);
  assert.equal(
    repliesWords({ people: 0, replied: 0, inferred: 0, waiting: 0 }).headline,
    "Nobody in your teams is asked yet.",
  );
  assert.equal(
    repliesWords({ people: 4, replied: 0, inferred: 1, waiting: 3 }, "the issue tracker").inferred,
    "1 filled from the issue tracker, marked as inferred, never green.",
  );
});

test("warnings are a count, and none is said as none", () => {
  assert.equal(warningsWords(6), "6 open");
  assert.equal(warningsWords(0), "None open");
  assert.equal(openWarningsLine(19, 3), "19 open across 3 projects.");
  assert.equal(openWarningsLine(0, 1), "None open across 1 project.");
});

test("a change is named by its issue and what a yes does", () => {
  assert.equal(changeTitle(proposal(gate("open"))), "CHK-4: move to Done");
  assert.equal(
    changeTitle({ kind: "due_date", issue_key: "CHK-5", target_label: null }),
    "CHK-5: due date",
  );
});

test("a yes is offered only where the gates let it through; a no for every answerable change", () => {
  assert.deepEqual(answerButtons(proposal(gate("open"))), { yes: true, no: true });
  assert.deepEqual(answerButtons(proposal(gate("no_change"))), { yes: true, no: true });
  for (const closed of ["off", "not_owner", "held_open_mr", "unreadable"] as const) {
    assert.deepEqual(answerButtons(proposal(gate(closed))), { yes: false, no: true }, closed);
  }
  assert.deepEqual(answerButtons(proposal(null, false)), { yes: false, no: false });
});

test("each closed gate says what stands in the way, in plain words", () => {
  const item = proposal(gate("open"));
  assert.equal(gateWords(gate("open"), item), null);
  assert.equal(gateWords(null, item), null);
  assert.equal(
    gateWords(gate("held_open_mr", ["checkout-api !7"]), item),
    "checkout-api !7 is still open, so it can't move to Done yet.",
  );
  assert.equal(
    gateWords(gate("held_open_mr", ["api !1", "web !2"]), item),
    "api !1, web !2 are still open, so it can't move to Done yet.",
  );
  assert.equal(
    gateWords(gate("not_owner"), item),
    "CHK-4 is not assigned to you in Jira any more.",
  );
  assert.match(gateWords(gate("off"), item) ?? "", /only a no can be given/);
  assert.match(gateWords(gate("no_change"), item) ?? "", /^Already Done in Jira/);
  assert.equal(
    gateWords(gate("unreadable"), item, "the issue tracker"),
    "The issue tracker could not be read just now. Try again in a minute.",
  );
  assert.equal(dueDateWords(), "Change it in Jira: OpenProgram never writes a due date.");
});

test("the check-in line says replied, waiting on a yes, or not yet", () => {
  const today = "2026-10-13";
  assert.deepEqual(
    checkinStateWords({
      status: { status_as_of: today, source: "confirmed" },
      today,
      waitingOnYes: 0,
    }),
    { replied: true, line: "You replied today." },
  );
  assert.deepEqual(
    checkinStateWords({
      status: { status_as_of: "2026-10-12", source: "confirmed" },
      today,
      waitingOnYes: 0,
    }),
    { replied: false, line: "Not replied yet today." },
  );
  assert.equal(
    checkinStateWords({
      status: { status_as_of: today, source: "inferred" },
      today,
      waitingOnYes: 0,
    }).replied,
    false,
  );
  assert.equal(
    checkinStateWords({ status: null, today, waitingOnYes: 1 }).line,
    "Your check-in waits for your yes or no on 1 Jira change.",
  );
});

test("who must act says the age, the owner and why, never ranking a person", () => {
  assert.equal(askAge(0), "today");
  assert.equal(askAge(46), "46 days");
  assert.equal(askAge(null), "—");
  assert.equal(askWho(ask({ who: null })), "Nobody named yet");
  assert.equal(
    askWhy(ask({ detail: "reported by Kai Thompson" })),
    "Fix needed for 3 days · reported by Kai Thompson",
  );
  assert.equal(
    askWhy(
      ask({
        waited_days: 9,
        level: 2,
        escalated_to: "Ira Novak",
        escalation_label: "scrum master",
      }),
    ),
    "Fix needed for 9 days · moved up to Ira Novak (scrum master)",
  );
  // An executive's row names no one: the level alone.
  assert.equal(
    askWhy(ask({ waited_days: 0, level: 2, escalation_label: "scrum master" })),
    "Fix needed since today · moved up to the scrum master",
  );
});

test("several projects' asks merge by the item, and a scrum master's keep only their teams", () => {
  const a = ask({ what: "a", level: 1, waited_days: 2, team: "Payments Pod" });
  const b = ask({ what: "b", level: 2, waited_days: 1, team: "Storefront Pod" });
  const c = ask({ what: "c", level: 1, waited_days: 9, team: "Payments Pod" });
  assert.deepEqual(
    mergeAsks([[a], [b, c]]).map((item) => item.what),
    ["b", "c", "a"],
  );
  assert.deepEqual(
    mergeAsks([[a], [b, c]], new Set(["Payments Pod"])).map((item) => item.what),
    ["c", "a"],
  );
  assert.deepEqual(mergeAsks([[ask({ team: null })]], new Set(["Payments Pod"])), []);
});

test("the footer says findings are about work and when they are from", () => {
  assert.equal(
    footerLine("2026-10-13"),
    "Findings attach to work items, not people. Nobody is ranked. As of Tue 13 Oct.",
  );
  assert.equal(footerLine(null), "Findings attach to work items, not people. Nobody is ranked.");
});

test("a request listed under Your asks is not said again in Who must act", () => {
  const ask = (what: string, request_id: string | null = null) =>
    ({ need: "answer", need_label: "Answer", what, level: 1, waited_days: 3, request_id }) as const;
  const merged = mergeAsks(
    [[ask("CHK-1: Noah asked for input", "req-1"), ask("CHK-2: blocked"), ask("CHK-3", "req-3")]],
    undefined,
    new Set(["req-1"]),
  );
  assert.deepEqual(
    merged.map((item) => item.what),
    ["CHK-2: blocked", "CHK-3"],
  );
});
