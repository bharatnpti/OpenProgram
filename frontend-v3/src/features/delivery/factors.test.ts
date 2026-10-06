import assert from "node:assert/strict";
import { test } from "node:test";

import { reasonLine, worstFirst } from "./factors.ts";

const factor = (
  description: string,
  contributes: "red" | "amber" | "green" | "unknown",
  id = "U1",
) => ({
  description,
  contributes,
  source_ref: { tenant_id: "t", kind: "developer" as const, id },
  kind: "blocker",
  blocker_id: null,
  work_item_ref: null,
  unattributed: false,
  applies_to_pod_ids: [],
});

test("the reason a reader sees first is the one that sets the colour", () => {
  const sorted = worstFirst([
    factor("ok", "green"),
    factor("silent", "unknown"),
    factor("blocked", "red"),
  ]);
  assert.deepEqual(
    sorted.map((f) => f.description),
    ["blocked", "silent", "ok"],
  );
});

test("the reason names whom it comes from when the server names them", () => {
  assert.equal(
    reasonLine([factor("2 blockers past 7 days", "red", "U7")], { U7: "Kai Thompson" }),
    "2 blockers past 7 days (from Kai Thompson)",
  );
  assert.equal(reasonLine([factor("stale", "amber", "U9")], {}), "stale");
  assert.equal(reasonLine([], {}), null);
});
