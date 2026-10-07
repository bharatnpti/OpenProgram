import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  PullRequestFlowItemDto,
  PullRequestFlowResponse,
  PullRequestFlowStageDto,
} from "../../api/schema";
import {
  bandProfile,
  bandSummary,
  dotX,
  formatHours,
  formatShare,
  investment,
  noneWords,
  leadHours,
  parseScope,
  planDots,
  requestReference,
  scopeWords,
  standingWords,
  thicknessAt,
  typeColorVar,
  typeSourceWords,
  TYPES,
} from "./prFlow.ts";

const stage = (
  key: PullRequestFlowStageDto["stage"],
  label: string,
  p50: number | null,
  p75: number | null,
): PullRequestFlowStageDto => ({
  stage: key,
  label,
  p50_hours: p50,
  p75_hours: p75,
  measured_count: p50 === null ? 0 : 10,
  open_count: 1,
});

const stages = [
  stage("coding", "Coding", 3.2, 6),
  stage("awaiting_review", "Awaiting review", 16, 30),
  stage("in_review", "In review", 2, 4),
  stage("awaiting_merge", "Awaiting merge", 0.75, 1.5),
];

const hours = (c: number | null, a: number | null, r: number | null, m: number | null) => ({
  coding: c,
  awaiting_review: a,
  in_review: r,
  awaiting_merge: m,
});

function item(overrides: Partial<PullRequestFlowItemDto> = {}): PullRequestFlowItemDto {
  return {
    repo: "acme/storefront-web",
    number: "12",
    title: "Promo codes",
    web_url: "https://git.example/acme/storefront-web/-/merge_requests/12",
    author_name: "Zoe Almeida",
    request_type: "feature",
    type_source: "issue",
    type_evidence: "Story CHK-12",
    state: "merged",
    draft: false,
    stage: null,
    stage_since: null,
    stage_age_hours: null,
    stage_hours: hours(2, 16, 2, 1),
    opened_at: "2026-10-01T09:00:00Z",
    merged_at: "2026-10-02T06:00:00Z",
    reviewer_count: 1,
    timed: true,
    ...overrides,
  };
}

function flow(overrides: Partial<PullRequestFlowResponse> = {}): PullRequestFlowResponse {
  return {
    as_of: "2026-10-06",
    window_days: 30,
    window_start: "2026-09-06T12:00:00Z",
    window_end: "2026-10-06T12:00:00Z",
    scope: { kind: "tenant", id: null, name: null, repos: null, member_count: null },
    merged_count: 3,
    open_count: 1,
    timed_merged_count: 3,
    unreviewed_merged_count: 0,
    untimed_count: 0,
    stages,
    worst_jam: { p50: "awaiting_review", p75: "awaiting_review" },
    type_counts: [
      { request_type: "feature", label: "Feature", merged_count: 2, open_count: 1 },
      { request_type: "bug_fix", label: "Bug fix", merged_count: 1, open_count: 0 },
    ],
    items: [item()],
    items_truncated: false,
    notes: [],
    ...overrides,
  };
}

test("stage times read the way the screenshot does", () => {
  assert.equal(formatHours(0.75), "45 m");
  assert.equal(formatHours(3.2), "3.2 h");
  assert.equal(formatHours(3.04), "3 h");
  assert.equal(formatHours(16.4), "16 h");
  assert.equal(formatHours(57.6), "2.4 d");
  assert.equal(formatHours(400), "17 d");
  assert.equal(formatHours(0.001), "under 1 m");
  assert.equal(formatHours(null), "—");
});

test("the band narrows most where work waits longest, log-scaled", () => {
  const profile = bandProfile(stages, "p50");
  const byStage = Object.fromEntries(profile.map((s) => [s.stage, s]));
  assert.equal(byStage.awaiting_review.jam, 1);
  assert.ok(byStage.awaiting_review.thickness < byStage.coding.thickness);
  assert.ok(byStage.coding.thickness < byStage.in_review.thickness);
  assert.ok(byStage.in_review.thickness < byStage.awaiting_merge.thickness);
  // Even the worst jam keeps room for the dots.
  assert.ok(byStage.awaiting_review.thickness >= 0.38);
});

test("a stage nobody was timed in is open and neutral, not a jam", () => {
  const profile = bandProfile(
    stages.map((s) => ({ ...s, p50_hours: null, p75_hours: null })),
    "p75",
  );
  assert.deepEqual(
    profile.map((s) => [s.thickness, s.jam]),
    [
      [1, 0],
      [1, 0],
      [1, 0],
      [1, 0],
    ],
  );
});

test("the thickness eases between stage centres and holds at the ends", () => {
  const profile = [{ thickness: 1 }, { thickness: 0.4 }, { thickness: 1 }, { thickness: 1 }];
  assert.equal(thicknessAt(profile, 0), 1);
  assert.equal(thicknessAt(profile, 0.375), 0.4);
  const between = thicknessAt(profile, 0.25);
  assert.ok(between > 0.4 && between < 1);
  assert.equal(thicknessAt(profile, 1), 1);
});

test("a merged request lingers in each stage as long as it waited there", () => {
  const [dot] = planDots(flow(), "p75");
  assert.equal(dot.open, false);
  const [coding, awaiting, review, merge] = dot.shares;
  assert.ok(awaiting > coding && awaiting > review && review > merge);
  assert.ok(Math.abs(dot.shares.reduce((a, b) => a + b, 0) - 1) < 1e-9);
  // It always moves left to right within one trip.
  const start = (1 - dot.phase) * dot.period;
  let last = -1;
  for (let k = 0; k < 20; k++) {
    const x = dotX(dot, start + (k / 20) * dot.period * 0.99);
    assert.ok(x >= last, `step ${k}`);
    last = x;
  }
});

test("an open request stands in its own stage, further on the longer it waited", () => {
  const open = (age: number) =>
    planDots(
      flow({
        items: [
          item({
            state: "open",
            stage: "awaiting_review",
            stage_age_hours: age,
            stage_hours: hours(2, null, null, null),
            merged_at: null,
          }),
        ],
      }),
      "p75",
    )[0];
  const fresh = open(1);
  const stale = open(200);
  assert.ok(fresh.open && stale.open);
  for (const dot of [fresh, stale]) assert.ok(dot.rest > 0.25 && dot.rest < 0.5);
  assert.ok(stale.rest > fresh.rest);
  assert.equal(dotX(stale, 123), stale.rest);
});

test("a request merged with no review passes the review stages at once", () => {
  const [dot] = planDots(
    flow({ items: [item({ stage_hours: hours(2, null, null, null) })] }),
    "p75",
  );
  const [coding, ...review] = dot.shares;
  for (const share of review) assert.ok(share < coding / 10);
});

test("requests whose history was not read are counted but not drawn", () => {
  assert.equal(planDots(flow({ items: [item({ timed: false })] }), "p50").length, 0);
});

test("the same request always lands in the same lane", () => {
  const a = planDots(flow(), "p50")[0];
  const b = planDots(flow(), "p75")[0];
  assert.equal(a.lane, b.lane);
  assert.ok(a.lane >= -0.85 && a.lane <= 0.85);
});

test("types keep fixed colours: eight palette slots and grey", () => {
  assert.equal(TYPES.length, 9);
  assert.equal(typeColorVar("feature"), "--op-flow-type-1");
  assert.equal(typeColorVar("performance"), "--op-flow-type-8");
  assert.equal(typeColorVar("unclassified"), "--op-flow-type-none");
});

test("investment bars run largest first and the types with none wait in one line", () => {
  const { bars, none } = investment(
    flow({
      type_counts: [
        { request_type: "feature", label: "Feature", merged_count: 2, open_count: 1 },
        { request_type: "bug_fix", label: "Bug fix", merged_count: 5, open_count: 0 },
        { request_type: "chore", label: "Chore or tooling", merged_count: 2, open_count: 0 },
        { request_type: "refactor", label: "Refactor", merged_count: 0, open_count: 3 },
      ],
    }),
  );
  // A tie keeps the fixed type order: feature before chore.
  assert.deepEqual(
    bars.map((row) => [row.type, row.merged, row.open]),
    [
      ["bug_fix", 5, 0],
      ["feature", 2, 1],
      ["chore", 2, 0],
    ],
  );
  assert.equal(formatShare(bars[0].share), "56%");
  // Every type is somewhere, the ones with none in the fixed order.
  assert.equal(bars.length + none.length, TYPES.length);
  assert.deepEqual(
    none.slice(0, 3).map((row) => row.type),
    ["dependency_update", "refactor", "documentation"],
  );
  assert.equal(
    noneWords(none.slice(0, 3)),
    "None in this window: Dependency update, Refactor and Documentation.",
  );
  assert.equal(noneWords(none.slice(1, 2)), "None in this window: Refactor.");
  assert.equal(noneWords([]), null);
  // The colour stays the type's wherever the sort puts it.
  assert.equal(typeColorVar(bars[0].type), "--op-flow-type-3");
  assert.equal(formatShare(0), "0%");
  assert.equal(formatShare(0.004), "<1%");
});

test("words for where a request stands and where its type came from", () => {
  assert.equal(standingWords(item()), "Merged");
  assert.equal(
    standingWords(item({ state: "open", stage: "in_review", stage_age_hours: 5, draft: false })),
    "In review for 5 h",
  );
  assert.equal(
    standingWords(item({ state: "open", stage: "coding", stage_age_hours: 30, draft: true })),
    "Coding for 30 h (draft)",
  );
  assert.equal(standingWords(item({ state: "open", timed: false })), "Open, stage not known");
  assert.equal(typeSourceWords("issue", "Bug CHK-3"), "from the Jira issue Bug CHK-3");
  assert.equal(
    typeSourceWords("author", "renovate[bot]"),
    "written by a dependency bot (renovate[bot])",
  );
  assert.equal(typeSourceWords("none", null), "no rule named a type");
  assert.equal(requestReference(item()), "storefront-web !12");
  assert.equal(
    requestReference(item({ web_url: "https://github.com/a/b/pull/4" })),
    "storefront-web #12",
  );
  assert.equal(leadHours(item()), 21);
  assert.equal(leadHours(item({ state: "open" })), null);
});

test("the scope in words, and from the URL", () => {
  assert.equal(scopeWords(flow().scope), "Every synced repository");
  assert.equal(
    scopeWords({ kind: "pod", id: "pod-web", name: "Web Pod", repos: null, member_count: 2 }),
    "Pod Web Pod: requests by its 2 members in any repository",
  );
  assert.equal(
    scopeWords({ kind: "project", id: "p", name: "Checkout", repos: ["a"], member_count: null }),
    "Project Checkout: 1 repository",
  );
  assert.deepEqual(parseScope("project:project-checkout"), {
    kind: "project",
    id: "project-checkout",
  });
  assert.deepEqual(parseScope("nonsense"), { kind: "all", id: null });
  assert.deepEqual(parseScope(null), { kind: "all", id: null });
});

test("a screen reader hears every stage and which one jams", () => {
  assert.equal(
    bandSummary(flow(), "p75"),
    "Time in each stage at p75, from 3 timed merged requests: Coding 6 h, Awaiting review 30 h (the worst jam), In review 4 h, Awaiting merge 1.5 h.",
  );
});
