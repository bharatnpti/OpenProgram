import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { signalAge, signalHref, tileKey, tileReasons, tooltipText } from "./heatReasons.ts";

type Cell = NonNullable<Parameters<typeof tileReasons>[0]>[number];

function cell(overrides: Partial<Cell> & { kind: string; id: string }): Cell {
  const { kind, id, ...rest } = overrides;
  return {
    rag: "amber",
    reason: "3 of 4 unanswered today",
    reasons: ["3 of 4 haven't answered today's check-in: Ada Lind, Ben Okafor and Cleo Morales."],
    entity_ref: { kind, id },
    ...rest,
  };
}

describe("tileReasons", () => {
  test("each cell's reason and tooltip, by node kind and id", () => {
    const reasons = tileReasons([
      cell({ kind: "pod", id: "pod-web" }),
      cell({
        kind: "workstream",
        id: "ws-cart",
        rag: "unknown",
        reason: "No tasks linked",
        reasons: [
          "No tasks are linked to this workstream, so nothing reports a status for it.",
          "Assigned to Web Pod, but none of its tickets is linked to it.",
        ],
      }),
    ]);

    assert.deepEqual(reasons.get(tileKey("pod", "pod-web")), {
      reason: "3 of 4 unanswered today",
      tooltip:
        "Amber\n• 3 of 4 haven't answered today's check-in: Ada Lind, Ben Okafor and Cleo Morales.",
    });
    assert.equal(
      reasons.get(tileKey("workstream", "ws-cart"))?.tooltip,
      "No status\n• No tasks are linked to this workstream, so nothing reports a status for it.\n" +
        "• Assigned to Web Pod, but none of its tickets is linked to it.",
    );
  });

  test("a cell from an older backend, with no reason, gives its tile none", () => {
    const reasons = tileReasons([cell({ kind: "pod", id: "pod-web", reason: null })]);

    assert.equal(reasons.size, 0);
    assert.equal(tileReasons(undefined).size, 0);
  });

  test("a tooltip with no reasons listed says the reason itself", () => {
    assert.equal(tooltipText("red", "2 blockers (Ada, Ben)", []), "Red\n• 2 blockers (Ada, Ben)");
  });
});

describe("signal links and ages", () => {
  test("a signal opens its node's delivery page, else the Signals list", () => {
    assert.equal(signalHref({ kind: "pod", id: "pod-web" }), "/delivery/pod/pod-web");
    assert.equal(signalHref({ kind: "program", id: "prog/1" }), "/delivery/program/prog%2F1");
    assert.equal(signalHref({ kind: "signals", id: null }), "/signals");
    assert.equal(signalHref({ kind: "project", id: null }), "/signals");
  });

  test("an age reads as days open, or new on the day it started", () => {
    assert.equal(signalAge(3), "open 3d");
    assert.equal(signalAge(0), "new");
  });
});
