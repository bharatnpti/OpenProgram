import assert from "node:assert/strict";
import { test } from "node:test";

import type { InvestigateStepResponse } from "../../api/schema";
import { STOPPED_EARLY, answering, applyEvent, streamEnded, toolWords } from "./investigate.ts";
import { checkedWords, INVESTIGATE_NOTE, ASSISTANT_NAME, pendingWords } from "./persona.ts";

const step = (
  index: number,
  status: InvestigateStepResponse["status"],
  more: Partial<InvestigateStepResponse> = {},
): InvestigateStepResponse => ({
  index,
  question: `Step ${index}?`,
  status,
  tools_used: [],
  findings: [],
  error: null,
  ...more,
});

test("the plan lists the steps, and each finished step replaces its planned one", () => {
  let now = applyEvent(answering(), {
    type: "plan",
    steps: [step(0, "running"), step(1, "running")],
  });
  assert.equal(now.status, "pending");
  now = applyEvent(now, {
    type: "step",
    step: step(1, "done", { findings: ["CHK-103 blocked 9 days"], tools_used: ["open_risks"] }),
  });
  assert.deepEqual(
    now.steps.map((s) => s.status),
    ["running", "done"],
  );
  assert.deepEqual(now.steps[1].findings, ["CHK-103 blocked 9 days"]);
});

test("the answer settles the message with its text, sources and every step", () => {
  const done = applyEvent(answering(), {
    type: "answer",
    answer: {
      answer: "Payments Pod is late because:\n• CHK-103 blocked",
      references: ["pod-payments"],
      tools_used: ["open_risks"],
      trace_id: "t",
      sources: [{ id: "pod-payments", kind: "pod", label: "Payments Pod" }],
      follow_ups: ["What blocks it?"],
      summarized_turns: 0,
    },
    steps: [step(0, "done")],
  });
  assert.equal(done.status, "answered");
  assert.equal(done.text, "Payments Pod is late because:\n• CHK-103 blocked");
  assert.deepEqual(done.sources, [{ id: "pod-payments", kind: "pod", label: "Payments Pod" }]);
  assert.equal(done.steps.length, 1);
  assert.deepEqual(done.followUps, ["What blocks it?"]);
});

test("a failure says why, keeping the steps it got to", () => {
  const planned = applyEvent(answering(), { type: "plan", steps: [step(0, "running")] });
  const failed = applyEvent(planned, { type: "failed", message: "Took too long.", steps: [] });
  assert.equal(failed.status, "failed");
  assert.equal(failed.text, "Took too long.");
  assert.equal(failed.steps.length, 1);
});

test("a stream that ends with no answer or reason has stopped early", () => {
  const planned = applyEvent(answering(), { type: "plan", steps: [step(0, "running")] });
  assert.deepEqual(streamEnded(planned), { ...planned, status: "failed", text: STOPPED_EARLY });
  const answered = { ...planned, status: "answered" as const, text: "Done." };
  assert.equal(streamEnded(answered), answered);
});

test("the waiting words follow the investigation, and name the assistant", () => {
  assert.equal(pendingWords(false, 0), `${ASSISTANT_NAME} is looking it up…`);
  assert.equal(pendingWords(true, 0), `${ASSISTANT_NAME} is planning the steps…`);
  assert.equal(pendingWords(true, 1), `${ASSISTANT_NAME} is checking 1 step…`);
  assert.equal(pendingWords(true, 3), `${ASSISTANT_NAME} is checking 3 steps…`);
  assert.equal(checkedWords(3), `How ${ASSISTANT_NAME} checked: 3 steps`);
  assert.match(INVESTIGATE_NOTE, /with AI/);
  assert.match(INVESTIGATE_NOTE, /Check the sources before you act\.$/);
  assert.equal(toolWords(["open_risks", "status_reasons"]), "open risks · status reasons");
});
