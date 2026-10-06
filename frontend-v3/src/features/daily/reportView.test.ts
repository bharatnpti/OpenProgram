import assert from "node:assert/strict";
import { test } from "node:test";

import { askParts, outcomeLine, sendConfirmation, todaysNote } from "./reportView.ts";

test("an ask line splits into its kind and its text", () => {
  assert.deepEqual(askParts("Fix: Sandbox credentials for 3-D Secure"), {
    kind: "Fix",
    text: "Sandbox credentials for 3-D Secure",
  });
  assert.deepEqual(askParts("Waited 9 days: escalated"), {
    kind: null,
    text: "Waited 9 days: escalated",
  });
});

test("the send confirmation names how many destinations receive it and where", () => {
  const { title, description } = sendConfirmation({
    name: "Checkout: end of day",
    destination_count: 3,
    audience_summary: "1 chat channel and 2 people by direct message",
  });
  assert.equal(title, "Send Checkout: end of day now?");
  assert.match(description, /^3 destinations receive it right away: 1 chat channel/);
});

test("a run says how many destinations it reached", () => {
  assert.equal(
    outcomeLine({
      outcomes: [
        { kind: "email", target: "a", label: "a", ok: true, detail: "" },
        { kind: "teams", target: "b", label: "b", ok: false, detail: "" },
      ],
    }),
    "1 of 2 delivered",
  );
  assert.equal(outcomeLine({ outcomes: [] }), "no destinations");
});

test("yesterday's note does not prefill today's", () => {
  const note = {
    report_id: "r",
    report_date: "2026-10-05",
    text: "Old",
    author: "u",
    updated_at: "2026-10-05T17:00:00Z",
  };
  assert.equal(todaysNote({ note }, "2026-10-06"), "");
  assert.equal(todaysNote({ note }, "2026-10-05"), "Old");
  assert.equal(todaysNote({ note: null }, "2026-10-06"), "");
});
