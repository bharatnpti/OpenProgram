import assert from "node:assert/strict";
import { test } from "node:test";

import {
  distinctBlockers,
  metadataFacts,
  reasonLine,
  reasonRows,
  reasonsNote,
  redFromBlockerCount,
  setBy,
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

test("the reasons count agrees with its word, and says how many places it merged", () => {
  assert.equal(reasonsNote(3, 3), "3 reasons");
  assert.equal(reasonsNote(2, 6), "2 reasons from 6 places");
  assert.equal(reasonsNote(1, 6), "1 reason from 6 places");
  assert.equal(reasonsNote(1, 2), "1 reason from 2 places");
});

test("a rollup's drift reason is called what it says, not 'drift'", () => {
  const rows = reasonRows(
    [
      factor(
        "Signals disagree: CHK-11 has a merge request open 5 days (storefront-web !2).",
        "amber",
        "U2",
        "drift",
      ),
    ],
    { U2: "Zoe Almeida" },
  );
  assert.equal(rows[0].kind, "signals disagree");
});

// The program of the real round: two blockers in two pods, each amber on its own, and an
// unrelated amber reason that happens to come first.
const blocker = (description: string, id: string, ref: string) => ({
  ...factor(description, "amber", ref, "blocker", "work_item"),
  blocker_id: id,
});
const TWO_BLOCKERS = [
  factor(
    "Signals disagree: CHK-6 has a merge request open 5 days (checkout-api !3).",
    "amber",
    "U2",
    "drift",
  ),
  blocker("Blocker: Waiting on review for sso-gateway!1", "b-idp-6", "IDP-6"),
  blocker("Blocker: Waiting for reviewer for MR !1 (INS-2).", "b-ins-2", "INS-2"),
];

test("a red that two different blockers make says so, not an unrelated amber reason", () => {
  assert.equal(redFromBlockerCount("red", TWO_BLOCKERS), true);
  assert.deepEqual(setBy("red", TWO_BLOCKERS), {
    text: "2 different blockers are open at once: Waiting on review for sso-gateway!1; Waiting for reviewer for MR !1 (INS-2).",
    factor: null,
  });
  assert.equal(
    reasonLine(TWO_BLOCKERS, {}, "red"),
    "2 different blockers are open at once: Waiting on review for sso-gateway!1; Waiting for reviewer for MR !1 (INS-2).",
  );
});

test("an amber node, or a red with a red reason of its own, names its worst reason", () => {
  assert.equal(redFromBlockerCount("amber", TWO_BLOCKERS), false);
  assert.equal(setBy("amber", TWO_BLOCKERS)?.factor?.kind, "drift");
  const critical = [...TWO_BLOCKERS, factor("A critical blocker.", "red", "U7", "blocker")];
  assert.equal(redFromBlockerCount("red", critical), false);
  assert.equal(setBy("red", critical)?.text, "A critical blocker.");
  // One blocker twice (the same id from two pods) is one blocker, as the backend counts it.
  const once = [
    blocker("Blocker: Waiting on review", "b-1", "IDP-6"),
    blocker("Blocker: Waiting on review", "b-1", "IDP-6"),
  ];
  assert.equal(distinctBlockers(once).length, 1);
  assert.equal(redFromBlockerCount("red", once), false);
  // Without a colour to explain nothing changes: the worst reason, as before.
  assert.equal(
    reasonLine(TWO_BLOCKERS, { U2: "Noah Weber" }),
    TWO_BLOCKERS[0].description + " (from Noah Weber)",
  );
});

test("more than three blockers are named three, then counted", () => {
  const many = ["a", "b", "c", "d", "e"].map((id) => blocker(`Blocker: ${id} is stuck`, id, id));
  assert.equal(
    setBy("red", many)?.text,
    "5 different blockers are open at once: a is stuck; b is stuck; c is stuck and 2 more.",
  );
});
