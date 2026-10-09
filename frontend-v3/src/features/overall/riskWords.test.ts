import assert from "node:assert/strict";
import { test } from "node:test";

import { riskLines } from "./riskWords.ts";

const risk = (
  id: string,
  severity: "red" | "amber",
  age: number,
  person: string | null,
  says: string | null,
) => ({
  rule_id: "stale_pr",
  severity,
  entity_ref: { tenant_id: "demo", kind: "developer" as const, id },
  workstream_id: null,
  reason: `Pull request of ${id} open ${age} days`,
  evidence: { identifier: `acme/checkout-api#${age}`, url: null, url_is_user_supplied: false },
  age_days: age,
  detected_at: "2026-10-09T06:00:00Z",
  status: "open",
  owner_id: id,
  owner_status_summary: says,
  owner_status_source: null,
  owner_status_as_of: null,
  owner_status_has_blockers: false,
  is_watermelon: false,
  person_name: person,
});

test("risks read worst first, then oldest, with what their owner says folded", () => {
  const names = (id: string | null | undefined) => (id === "U1005" ? "Zoe Almeida" : (id ?? ""));
  const known = (id: string | null | undefined) => id === "U1005";
  const lines = riskLines(
    {
      risks: [
        risk("U1004", "amber", 9, "Noah Weber", null),
        risk("U1005", "red", 5, null, "Waiting on review."),
        risk("U1009", "red", 7, "Sofia Bergmann", "In UAT."),
        risk("U1099", "red", 2, null, "Basically done."),
      ],
      drift: [],
    },
    names,
    known,
    "2026-10-09",
  );
  assert.deepEqual(
    lines.map((line) => [line.owner, line.age]),
    [
      ["Sofia Bergmann", 7],
      ["Zoe Almeida", 5],
      ["U1099", 2],
      ["Noah Weber", 9],
    ],
  );
  assert.equal(lines[0].saysTitle, "What Sofia says");
  assert.equal(lines[0].meta, "acme/checkout-api#7 · risk");
  // Someone nobody names keeps their id, and their words are the owner's.
  assert.equal(lines[2].saysTitle, "What the owner says");
  // Nothing said, nothing to fold.
  assert.equal(lines[3].saysTitle, null);
});
