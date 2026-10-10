import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryStage, GateState } from "../../api/schema";
import { failedCells, heatCell, heatRows } from "./heat.ts";

const BA = { template_id: "ba", name: "Business acceptance" };
const ED = { template_id: "ed", name: "Engineering delivery" };
const templates = [BA, ED].map((t) => ({
  ...t,
  guards_stage: "production" as const,
  kinds: [],
  enabled: true,
}));

function evaluation(
  template_id: string,
  state: GateState,
  over: { met?: number; total?: number; suggested?: number } = {},
) {
  return {
    template_id,
    state,
    met: over.met ?? 0,
    total: over.total ?? 0,
    suggested: over.suggested ?? 0,
    missing_kinds: [],
  };
}

function issue(
  key: string,
  stage: DeliveryStage,
  evaluations: ReturnType<typeof evaluation>[],
  without: string[] = [],
  suggested = 0,
) {
  return {
    key,
    title: `${key} work`,
    stage,
    status: null,
    evaluations,
    passed_without: without,
    items: Array.from({ length: suggested }, (_, i) => ({
      item_id: `${key}-${i}`,
      issue_key: key,
      template_id: "ba",
      kind: "acceptance",
      text: "x",
      status: "suggested" as const,
      source: "description" as const,
      source_ref: "",
      created_by: "scan",
      signed_by: null,
      signed_at: null,
      evidence_url: null,
      note: "",
    })),
  };
}

test("a gate gone around reads Bypassed unless it passed; a failed item wins", () => {
  const moved = ["Business acceptance", "Engineering delivery"];
  assert.deepEqual(
    heatCell(
      issue("CHK-8", "production", [evaluation("ba", "missing", { suggested: 2 })], moved),
      BA,
    ),
    { state: "bypassed", label: "Bypassed", note: "2 from Jira" },
  );
  assert.deepEqual(
    heatCell(
      issue("CHK-12", "production", [evaluation("ba", "passed", { met: 2, total: 2 })], moved),
      BA,
    ),
    { state: "passed", label: "Passed", note: null },
  );
  assert.equal(
    heatCell(
      issue("CHK-9", "production", [evaluation("ba", "failed", { met: 1, total: 2 })], moved),
      BA,
    ).state,
    "failed",
  );
  assert.deepEqual(
    heatCell(
      issue("CHK-4", "in_development", [evaluation("ba", "open", { met: 1, total: 3 })]),
      BA,
    ),
    {
      state: "open",
      label: "Open",
      note: "1 of 3 met",
    },
  );
  assert.deepEqual(heatCell(issue("CHK-1", "raised", [evaluation("ba", "missing")]), BA), {
    state: "pending",
    label: "Not started",
    note: null,
  });
  assert.equal(heatCell(issue("CHK-1", "raised", []), ED).label, "Does not apply");
});

test("rows run furthest stage first in key order, and the quiet ones fold", () => {
  const issues = [
    issue("CHK-1", "raised", [evaluation("ba", "missing"), evaluation("ed", "missing")]),
    issue(
      "CHK-12",
      "business_testing",
      [evaluation("ba", "passed", { met: 2, total: 2 }), evaluation("ed", "missing")],
      ["Engineering delivery"],
    ),
    issue(
      "CHK-3",
      "production",
      [evaluation("ba", "missing"), evaluation("ed", "missing")],
      ["Business acceptance", "Engineering delivery"],
    ),
    issue("CHK-10", "in_development", [evaluation("ba", "missing"), evaluation("ed", "missing")]),
    // Nothing started, but Jira suggested something to keep: it needs someone.
    issue(
      "CHK-2",
      "raised",
      [evaluation("ba", "missing", { suggested: 1 }), evaluation("ed", "missing")],
      [],
      1,
    ),
  ];
  const { shown, folded } = heatRows(issues, templates);
  assert.deepEqual(
    shown.map((row) => row.issue.key),
    ["CHK-3", "CHK-12", "CHK-2"],
  );
  assert.deepEqual(
    folded.map((row) => row.issue.key),
    ["CHK-10", "CHK-1"],
  );
  assert.equal(failedCells([...shown, ...folded]), 0);
});
