import assert from "node:assert/strict";
import { test } from "node:test";

import type { DriftFindingResponse, RiskFindingResponse } from "../../api/schema";
import {
  ageWords,
  findingsOf,
  groupByProject,
  ownerLine,
  riskCountLine,
  type Finding,
} from "./signalWords.ts";

const ref = (kind: string, id: string) => ({ tenant_id: "demo", kind, id }) as never;

const risk = (over: Partial<RiskFindingResponse> = {}): RiskFindingResponse => ({
  rule_id: "pr_age",
  severity: "red",
  entity_ref: ref("developer", "U1008"),
  workstream_id: null,
  reason: "A merge request for Noah Weber has been open 6 days",
  evidence: { identifier: "checkout-web!12", url: null, url_is_user_supplied: false },
  age_days: 6,
  detected_at: "2026-10-02T09:00:00Z",
  status: "open",
  owner_id: "U1008",
  owner_status_summary: "CHK-6 MR will be ready for review tomorrow",
  owner_status_source: "confirmed",
  owner_status_as_of: "2026-10-06",
  owner_status_has_blockers: false,
  is_watermelon: true,
  person_name: "Noah Weber",
  ...over,
});

const drift = (over: Partial<DriftFindingResponse> = {}): DriftFindingResponse => ({
  kind: "said_done_no_merge",
  severity: "amber",
  entity_ref: ref("task", "CHK-4"),
  workstream_id: null,
  reason: "CHK-4 was said done, but no merge request has merged",
  detected_at: "2026-10-05T09:00:00Z",
  owner_id: "U1007",
  stated_source: "confirmed",
  evidence: null,
  child_entity_ref: null,
  ...over,
});

const none = { risks: [], drift: [] };

test("the header counts open risks, and drift only when there is some", () => {
  assert.equal(riskCountLine(7, 0), "7 open risks");
  assert.equal(riskCountLine(1, 2), "1 open risk · 2 drift");
  assert.equal(riskCountLine(0, 0), "0 open risks");
});

test("rows run worst first, then oldest; each finding keeps its own evidence", () => {
  const rows = findingsOf({
    risks: [
      risk({ severity: "amber", age_days: 9, evidence: { ...risk().evidence, identifier: "a!1" } }),
      risk({ age_days: 2, evidence: { ...risk().evidence, identifier: "a!2" } }),
      risk({ age_days: 6, evidence: { ...risk().evidence, identifier: "a!3" } }),
    ],
    drift: [drift()],
  });
  assert.deepEqual(
    rows.map((row) => [row.type, row.severity, row.type === "risk" ? row.finding.age_days : null]),
    [
      ["risk", "red", 6],
      ["risk", "red", 2],
      ["risk", "amber", 9],
      ["drift", "amber", null],
    ],
  );
  assert.equal(new Set(rows.map((row) => row.key)).size, 4);
});

test("risks are grouped by project, the worst project first, nothing dropped", () => {
  const projects = [
    { id: "p-insights", name: "Customer Insights" },
    { id: "p-checkout", name: "Checkout Revamp" },
    { id: "p-identity", name: "Identity Platform" },
  ];
  const noah = risk();
  const reads: Record<string, { risks: RiskFindingResponse[]; drift: DriftFindingResponse[] }> = {
    "p-insights": {
      risks: [risk({ severity: "amber", entity_ref: ref("task", "INS-2") })],
      drift: [],
    },
    "p-checkout": { risks: [noah], drift: [drift()] },
    "p-identity": none,
  };
  const orphan = risk({
    entity_ref: ref("repository", "infra"),
    evidence: { ...noah.evidence, identifier: "infra!3" },
  });
  const groups = groupByProject(projects, (id) => reads[id], {
    risks: [noah, ...reads["p-insights"].risks, orphan],
    drift: [drift()],
  });
  assert.deepEqual(
    groups.map((group) => [group.name, group.worst, group.findings.length]),
    [
      ["Checkout Revamp", "red", 2],
      ["Customer Insights", "amber", 1],
      ["Not tied to a project", "red", 1],
    ],
    "a project with none is left out; the portfolio's own come last",
  );
  // A project's read still loading leaves its findings to the leftover group, not to nowhere.
  const loading = groupByProject(projects, (id) => (id === "p-checkout" ? undefined : reads[id]), {
    risks: [noah],
    drift: [],
  });
  assert.deepEqual(
    loading.map((group) => group.name),
    ["Customer Insights", "Not tied to a project"],
  );
});

test("the owner's line says who says what, and how they said it", () => {
  const row = (finding: RiskFindingResponse): Finding =>
    findingsOf({ risks: [finding], drift: [] })[0];
  assert.equal(
    ownerLine(row(risk()), "Noah Weber"),
    "Noah Weber says: CHK-6 MR will be ready for review tomorrow (replied)",
  );
  assert.equal(
    ownerLine(
      row(risk({ owner_status_summary: null, owner_status_source: "inferred" })),
      "Noah Weber",
    ),
    "Noah Weber has reported nothing (inferred)",
  );
  assert.equal(
    ownerLine(
      row(
        risk({
          owner_status_summary: "No confirmed check-in after a nudge.",
          owner_status_source: "inferred",
        }),
      ),
      "Noah Weber",
    ),
    "Noah Weber: No confirmed check-in after a nudge. (inferred)",
    "words the system wrote are not what the owner says",
  );
  assert.equal(
    ownerLine(row(risk({ owner_id: null, person_name: null })), null),
    "Nobody owns this work item, so nobody has said anything about it.",
  );
  const driftRow = findingsOf({ risks: [], drift: [drift()] })[0];
  assert.equal(ownerLine(driftRow, "Kai Thompson"), "Kai Thompson: stated in a reply");
  const unowned = findingsOf({ risks: [], drift: [drift({ owner_id: null })] })[0];
  assert.equal(ownerLine(unowned, null), "Nobody is named as the owner: stated in a reply");
});

test("a risk's age is its days open; a drift finding's is the day it was found", () => {
  const day = (iso: string) => iso.slice(0, 10);
  assert.equal(ageWords(findingsOf({ risks: [risk()], drift: [] })[0], day), "6d");
  assert.equal(
    ageWords(findingsOf({ risks: [risk({ age_days: 0 })], drift: [] })[0], day),
    "today",
  );
  assert.equal(ageWords(findingsOf({ risks: [], drift: [drift()] })[0], day), "since 2026-10-05");
});
