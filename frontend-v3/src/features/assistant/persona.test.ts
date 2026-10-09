import assert from "node:assert/strict";
import { test } from "node:test";

import { accessOf, capabilitiesFor, paletteTargets } from "../../app/access.ts";
import type { AppRole } from "../../app/roleWords.ts";
import {
  ANSWER_NOTE,
  ASK_LABEL,
  ASSISTANT_NAME,
  askError,
  askedAboutWords,
  assistantGreeting,
  placeOf,
  sourceLink,
  sourceWords,
  suggestionsFor,
  type AskPlace,
} from "./persona.ts";

const targetsOf = (role: AppRole) =>
  paletteTargets(accessOf({ lens: [role], chatEnabled: false }), capabilitiesFor([role]));
const at = (place: Partial<AskPlace>): AskPlace => ({
  page: "today",
  view: null,
  subject: null,
  ...place,
});

test("the assistant's name is said in one place, and every label is built from it", () => {
  assert.equal(ASK_LABEL, `Ask ${ASSISTANT_NAME}`);
  assert.match(ANSWER_NOTE, /can be wrong/);
});

test("the greeting names the person and the time of day, and no one when nobody is named", () => {
  const morning = new Date(2026, 9, 9, 8, 30);
  const evening = new Date(2026, 9, 9, 19, 0);
  assert.equal(
    assistantGreeting("Ira", morning),
    "Good morning, Ira — what would you like to know?",
  );
  assert.equal(
    assistantGreeting("Ira", new Date(2026, 9, 9, 14, 0)),
    "Good afternoon, Ira — what would you like to know?",
  );
  assert.equal(assistantGreeting(null, evening), "Good evening — what would you like to know?");
  assert.equal(assistantGreeting("", evening), "Good evening — what would you like to know?");
});

test("the page is read from the path, with its view", () => {
  assert.deepEqual(placeOf("/today", "", "flow"), { page: "today", view: null });
  assert.deepEqual(placeOf("/reports/p-1/daily", "", "flow"), { page: "reports", view: "daily" });
  assert.deepEqual(placeOf("/reports", "", "flow"), { page: "reports", view: null });
  assert.deepEqual(placeOf("/signals", "?view=risks", "flow"), { page: "signals", view: "risks" });
  assert.deepEqual(placeOf("/signals", "", "risks"), { page: "signals", view: "risks" });
  assert.deepEqual(placeOf("/delivery/pod/x", "", "flow"), { page: "delivery", view: null });
  assert.deepEqual(placeOf("/somewhere", "", "flow"), { page: "today", view: null });
});

test("a scrum master's pod gets questions about that pod, worded for the day shown", () => {
  const pod = at({ subject: { kind: "pod", name: "Payments Pod" } });
  assert.deepEqual(suggestionsFor(pod, { podDetail: true }), [
    "Will Payments Pod make its date?",
    "What is blocking Payments Pod, and for how long?",
    "Who in Payments Pod has not replied today?",
  ]);
  assert.equal(
    suggestionsFor(pod, { podDetail: true, day: "on Mon 5 Oct" })[2],
    "Who in Payments Pod has not replied on Mon 5 Oct?",
  );
  // A role that reads no pod's check-ins is not offered questions about them.
  assert.ok(!suggestionsFor(pod, { podDetail: false }).some((q) => q.includes("replied")));
});

test("a project gets questions about the project; the portfolio about every project", () => {
  const project = at({ subject: { kind: "project", name: "Checkout Revamp" } });
  assert.equal(
    suggestionsFor(project, { podDetail: false })[0],
    "Is Checkout Revamp on track for its date?",
  );
  assert.ok(
    suggestionsFor(project, { podDetail: false }).some((q) => q.includes("no ETA or due date")),
  );
  assert.equal(
    suggestionsFor(at({ page: "reports", subject: project.subject }), { podDetail: false })[2],
    "What changed in Checkout Revamp in the last 7 days?",
  );
  assert.equal(
    suggestionsFor(at({}), { podDetail: false })[0],
    "Which projects are most at risk, and why?",
  );
  assert.equal(
    suggestionsFor(at({}), { podDetail: false })[2],
    "Which risks have been open longest?",
    "an executive reads no blockers",
  );
});

test("every page has two to four questions, and Signals' follow its view", () => {
  for (const page of [
    "today",
    "delivery",
    "signals",
    "coordination",
    "reports",
    "chat",
    "admin",
  ] as const) {
    for (const podDetail of [true, false]) {
      const chips = suggestionsFor(at({ page }), { podDetail });
      assert.ok(chips.length >= 2 && chips.length <= 4, `${page}: ${chips.length}`);
      assert.equal(new Set(chips).size, chips.length, `${page}: no question twice`);
    }
  }
  assert.ok(
    suggestionsFor(at({ page: "signals", view: "risks" }), { podDetail: false })[0].includes(
      "risks",
    ),
  );
  assert.ok(
    suggestionsFor(at({ page: "signals", view: "flow" }), { podDetail: false })[0].includes(
      "review",
    ),
  );
});

test("on a past day the panel says which day is asked about", () => {
  assert.equal(askedAboutWords("Mon 5 Oct"), "Asking about Mon 5 Oct, the day shown.");
  assert.equal(askedAboutWords(null), null);
});

test("a source links where the role has its page, and is plain words elsewhere", () => {
  const project = { id: "project-checkout", kind: "project" as const, label: "Checkout Revamp" };
  const pod = { id: "pod payments", kind: "pod" as const, label: "Payments Pod" };
  const task = { id: "CHK-101", kind: "task" as const, label: "Card form" };
  assert.equal(sourceLink(project, targetsOf("mgr")), "/delivery/project/project-checkout");
  assert.equal(sourceLink(pod, targetsOf("sm")), "/today?pod=pod%20payments");
  assert.equal(sourceLink(project, targetsOf("po")), "/today?project=project-checkout");
  assert.equal(sourceLink(pod, targetsOf("exec")), null, "an executive's Delivery lists no pods");
  assert.equal(sourceLink(task, targetsOf("mgr")), null, "a task has no page of its own");
  assert.deepEqual(sourceWords(task), { label: "Card form", kind: "task" });
  assert.deepEqual(sourceWords({ id: "U1007", kind: null, label: null }), {
    label: "U1007",
    kind: null,
  });
});

test("a refused question says so with the server's reason; anything else in plain words", () => {
  assert.equal(
    askError({ status: 403, message: "U1007 is not authorized for aggregate read" }),
    "Not available to you. The server said: U1007 is not authorized for aggregate read",
  );
  assert.equal(askError({ status: 403 }), "Not available to you.");
  assert.ok(askError(new Error("boom")).length > 0);
});
