import assert from "node:assert/strict";
import { test } from "node:test";

import {
  checkinStateWords,
  metadataFacts,
  reasonLine,
  reasonRows,
  sourcesLine,
  worstFirst,
} from "./factors.ts";

type NodeKind = "developer" | "repo" | "task" | "work_item";

const factor = (
  description: string,
  contributes: "red" | "amber" | "green" | "unknown",
  id = "U1",
  kind = "blocker",
  sourceKind: NodeKind = "developer",
) => ({
  description,
  contributes,
  source_ref: { tenant_id: "t", kind: sourceKind, id },
  kind,
  blocker_id: null,
  work_item_ref: null,
  unattributed: false,
  applies_to_pod_ids: [],
});

test("the same reason from many places is one row naming each place", () => {
  const rows = reasonRows(
    [
      factor(
        "No child status data is available.",
        "unknown",
        "acme/checkout-api",
        "aggregate",
        "repo",
      ),
      factor("Task Refund edge cases is blocked.", "red", "task-chk-102", "task", "task"),
      factor(
        "No child status data is available.",
        "unknown",
        "acme/storefront-web",
        "aggregate",
        "repo",
      ),
      factor("Developer status is unknown.", "unknown", "U1007", "status"),
      factor("Blocker: PR needs a reviewer", "amber", "CHK-101", "blocker", "work_item"),
    ],
    { "task-chk-102": "Refund edge cases", U1007: "Kai Thompson" },
  );
  assert.deepEqual(
    rows.map((row) => [row.description, row.kind, row.sources]),
    [
      ["Task Refund edge cases is blocked.", "task", ["Refund edge cases"]],
      ["Blocker: PR needs a reviewer", "blocker", ["work item CHK-101"]],
      [
        "No child status data is available.",
        "rollup",
        ["repo acme/checkout-api", "repo acme/storefront-web"],
      ],
      ["Developer status is unknown.", "status", ["Kai Thompson"]],
    ],
  );
  assert.equal(sourcesLine(["a", "b", "c", "d", "e"]), "a, b, c and 2 more");
  assert.equal(sourcesLine(["a", "b"]), "a, b");
});

test("metadata shows what the seed sets, and nothing for empty or null keys", () => {
  assert.deepEqual(
    metadataFacts({ sm_id: null, tpm_id: null, owner_id: null, github_repos: "acme/checkout-api" }),
    [["Repositories", "acme/checkout-api"]],
  );
  assert.deepEqual(
    metadataFacts({
      code: "CHK",
      github_repos: "acme/checkout-api,acme/storefront-web",
      jira_project_key: "CHK",
    }),
    [
      ["Jira project", "CHK"],
      ["Repositories", "acme/checkout-api, acme/storefront-web"],
    ],
  );
  assert.deepEqual(
    metadataFacts({ type: "feature", phase: "build", target_date: "2026-10-30", summary: "" }),
    [
      ["Type", "feature"],
      ["Phase", "build"],
      ["Target date", "30 Oct 2026"],
    ],
  );
  assert.deepEqual(metadataFacts({}), []);
});

test("a check-in that isn't from the day shown says which day it is from", () => {
  const dev = (
    state: "confirmed" | "partial" | "stale" | "missing",
    source: "confirmed" | "inferred" | "unknown",
    day: string | null,
  ) => ({
    developer_id: "U1",
    developer_name: "Kai Thompson",
    state,
    source,
    status_as_of: day,
    summary: "",
  });
  assert.equal(checkinStateWords(dev("confirmed", "confirmed", "2026-10-06")), "confirmed");
  assert.equal(checkinStateWords(dev("stale", "confirmed", "2026-09-29")), "from Tue 29 Sept");
  assert.equal(checkinStateWords(dev("stale", "inferred", "2026-10-01")), "inferred");
  assert.equal(checkinStateWords(dev("missing", "unknown", "2026-09-29")), "no reply");
  assert.equal(checkinStateWords(dev("stale", "confirmed", null)), "not confirmed");
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
