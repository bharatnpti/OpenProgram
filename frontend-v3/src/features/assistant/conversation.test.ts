import assert from "node:assert/strict";
import { test } from "node:test";

import type { AskMessage } from "./assistantContext";
import {
  answerParts,
  conversationFor,
  freshMemory,
  nextQuestions,
  remember,
  turnsOf,
  uniqueSources,
} from "./conversation.ts";

let id = 0;
const you = (text: string): AskMessage => ({ id: ++id, from: "you", text });
const ora = (
  text: string,
  status: "pending" | "answered" | "failed" = "answered",
  followUps: string[] = [],
): AskMessage => ({
  id: ++id,
  from: "assistant",
  status,
  text,
  sources: [],
  dayLabel: null,
  question: "",
  mode: "quick",
  steps: [],
  followUps,
});

test("answered exchanges become turns; a failed one and the one on its way do not", () => {
  const messages = [
    you("Is Checkout red?"),
    ora("Yes."),
    you("Why?"),
    ora("Not available to you.", "failed"),
    you("And Identity?"),
    ora("", "pending"),
  ];
  assert.deepEqual(turnsOf(messages), [
    { role: "user", content: "Is Checkout red?" },
    { role: "assistant", content: "Yes." },
  ]);
});

test("a first question sends no conversation; a later one the turns since the summary", () => {
  assert.equal(conversationFor([], freshMemory()), null);
  const messages = [you("A?"), ora("a."), you("B?"), ora("b.")];
  assert.deepEqual(conversationFor(messages, freshMemory()), {
    summary: null,
    turns: turnsOf(messages),
  });
  assert.deepEqual(conversationFor(messages, { summary: "A was a.", covered: 2 }), {
    summary: "A was a.",
    turns: [
      { role: "user", content: "B?" },
      { role: "assistant", content: "b." },
    ],
  });
});

test("when the server folds turns away, the memory keeps its summary in their place", () => {
  const folded = remember(freshMemory(), { summary: "So far: red.", summarized_turns: 6 });
  assert.deepEqual(folded, { summary: "So far: red.", covered: 6 });
  assert.deepEqual(remember(folded, { summary: null, summarized_turns: 0 }), folded);
});

test("an answer reads as its verdict, bullets, other lines and what is not known", () => {
  assert.deepEqual(
    answerParts(
      "Checkout is red because:\n• 3 blockers\n- No reply: Kai\nIdentity is fine.\nNot known: the date",
    ),
    {
      verdict: "Checkout is red because:",
      bullets: ["3 blockers", "No reply: Kai"],
      rest: ["Identity is fine."],
      notKnown: "the date",
    },
  );
  assert.deepEqual(answerParts("Not known: anything"), {
    verdict: null,
    bullets: [],
    rest: [],
    notKnown: "anything",
  });
});

test("next questions: none while answering, the answer's own after it, the page's before any", () => {
  const page = ["Which projects are most at risk?", "What changed?"];
  assert.deepEqual(nextQuestions([], page, false), page);
  const answered = [you("What changed?"), ora("Two MRs.", "answered", ["Who merged them?"])];
  assert.deepEqual(nextQuestions(answered, page, false), ["Who merged them?"]);
  assert.deepEqual(nextQuestions(answered, page, true), []);
  // An answer that suggests nothing falls back to the page's, minus what was asked.
  const plain = [you("What changed?"), ora("Two MRs.")];
  assert.deepEqual(nextQuestions(plain, page, false), ["Which projects are most at risk?"]);
});

test("a source is listed once by the words it reads as", () => {
  assert.deepEqual(
    uniqueSources([
      { id: "CHK-102", kind: "work_item", label: "Refund edge cases" },
      { id: "task-chk-102", kind: "task", label: "Refund edge cases" },
      { id: "U1004", kind: "developer", label: "Noah Weber" },
      { id: "x-1", kind: null, label: null },
    ]).map((s) => s.id),
    ["CHK-102", "U1004", "x-1"],
  );
});
