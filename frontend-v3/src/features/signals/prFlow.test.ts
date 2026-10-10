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
  countsFor,
  dotX,
  formatHours,
  formatShare,
  investment,
  noneWords,
  leadHours,
  noStageTimeWords,
  parseScope,
  planDots,
  requestCountWords,
  requestReference,
  sampleWords,
  scopeWords,
  stagesFor,
  standingWords,
  thicknessAt,
  typeColorVar,
  typeSourceWords,
  TYPES,
  worstJam,
} from "./prFlow.ts";

const stage = (
  key: PullRequestFlowStageDto["stage"],
  label: string,
  p50: number | null,
  p75: number | null,
  count = p50 === null ? 0 : 10,
): PullRequestFlowStageDto => ({
  stage: key,
  label,
  p50_hours: p50,
  p75_hours: p75,
  measured_count: count,
  open_count: 1,
});

const stages = [
  stage("coding", "Coding", 3.2, 6),
  stage("awaiting_review", "Awaiting review", 16, 30),
  stage("in_review", "In review", 2, 4),
  stage("awaiting_merge", "Awaiting merge", 0.75, 1.5),
];

// What the server sends for a type: its own four stages and its own worst jam.
// Feature has two merged requests, one of them unreviewed (coding only counts it).
const featureStages = [
  stage("coding", "Coding", 2.5, 3, 2),
  stage("awaiting_review", "Awaiting review", 40, 48, 1),
  stage("in_review", "In review", 1.5, 1.5, 1),
  stage("awaiting_merge", "Awaiting merge", null, null),
];
// One merged request with coding alone known: a time, but no jam.
const bugFixStages = [
  stage("coding", "Coding", 1, 1, 1),
  stage("awaiting_review", "Awaiting review", null, null),
  stage("in_review", "In review", null, null),
  stage("awaiting_merge", "Awaiting merge", null, null),
];
// A type with no request: four stages, no time, no count.
const refactorStages = [
  stage("coding", "Coding", null, null),
  stage("awaiting_review", "Awaiting review", null, null),
  stage("in_review", "In review", null, null),
  stage("awaiting_merge", "Awaiting merge", null, null),
];
const noJam = { p50: null, p75: null };

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
    stages_by_type: {
      feature: {
        stages: featureStages,
        worst_jam: { p50: "awaiting_review", p75: "awaiting_review" },
      },
      bug_fix: { stages: bugFixStages, worst_jam: noJam },
      refactor: { stages: refactorStages, worst_jam: noJam },
    },
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

test("a screen reader hears every stage, how many requests it rests on, and which one jams", () => {
  assert.equal(
    bandSummary(flow(), "p75"),
    "Time in each stage at p75, from 3 timed merged requests: Coding 6 h · 10 requests, Awaiting review 30 h · 10 requests (the worst jam), In review 4 h · 10 requests, Awaiting merge 1.5 h · 10 requests.",
  );
  assert.equal(
    bandSummary(flow(), "p50", "feature"),
    "Time in each stage of Feature requests at p50: Coding 2.5 h · 2 requests, Awaiting review 40 h · 1 request (the worst jam), In review 1.5 h · 1 request, Awaiting merge —.",
  );
});

test("a picked type shows its own stage times, every request's with none picked", () => {
  const all = flow();
  assert.equal(stagesFor(all, null), all.stages);
  assert.equal(stagesFor(all, "feature"), featureStages);
  // The server sends every type; one it did not send has no time in any stage.
  const missing = stagesFor(all, "documentation");
  assert.deepEqual(
    missing.map((s) => [s.stage, s.p50_hours, s.p75_hours, s.measured_count, s.open_count]),
    [
      ["coding", null, null, 0, 0],
      ["awaiting_review", null, null, 0, 0],
      ["in_review", null, null, 0, 0],
      ["awaiting_merge", null, null, 0, 0],
    ],
  );
  // The percentile picks the type's p50 or p75, as it picks the tenant's.
  const p50 = bandProfile(stagesFor(all, "feature"), "p50").map((s) => s.hours);
  const p75 = bandProfile(stagesFor(all, "feature"), "p75").map((s) => s.hours);
  assert.deepEqual(p50, [2.5, 40, 1.5, null]);
  assert.deepEqual(p75, [3, 48, 1.5, null]);
});

test("the band's thickness and the worst jam follow the picked type", () => {
  const all = flow();
  const tenant = bandProfile(stagesFor(all, null), "p75");
  const feature = bandProfile(stagesFor(all, "feature"), "p75");
  const refactor = bandProfile(stagesFor(all, "refactor"), "p75");
  // Awaiting review jams for both, and beside it coding is shorter for feature.
  assert.equal(tenant[1].jam, 1);
  assert.equal(feature[1].jam, 1);
  assert.ok(feature[0].thickness > tenant[0].thickness);
  // A stage the type has no time in is open and neutral, like a type with none at all.
  assert.deepEqual([feature[3].thickness, feature[3].jam, feature[3].count], [1, 0, 0]);
  assert.deepEqual(
    refactor.map((s) => [s.thickness, s.jam, s.count]),
    [
      [1, 0, 0],
      [1, 0, 0],
      [1, 0, 0],
      [1, 0, 0],
    ],
  );
  assert.deepEqual(
    feature.map((s) => s.count),
    [2, 1, 1, 0],
  );
  assert.equal(worstJam(all, "p75"), "awaiting_review");
  assert.equal(worstJam(all, "p75", null), "awaiting_review");
  assert.equal(worstJam(all, "p50", "feature"), "awaiting_review");
  // Coding alone known: no jam, as the server says.
  assert.equal(worstJam(all, "p75", "bug_fix"), null);
  assert.equal(worstJam(all, "p75", "documentation"), null);
});

test("the merged and open counts follow the picked type", () => {
  const all = flow();
  assert.deepEqual(countsFor(all, null), { merged: 3, open: 1 });
  assert.deepEqual(countsFor(all, "feature"), { merged: 2, open: 1 });
  assert.deepEqual(countsFor(all, "bug_fix"), { merged: 1, open: 0 });
  assert.deepEqual(countsFor(all, "refactor"), { merged: 0, open: 0 });
});

test("a time says how many requests it rests on: 1 request, 24 requests, nothing for a dash", () => {
  assert.equal(requestCountWords(1), "1 request");
  assert.equal(requestCountWords(24), "24 requests");
  assert.equal(`${formatHours(24)} · ${sampleWords(24, 1)}`, "24 h · 1 request");
  assert.equal(`${formatHours(24)} · ${sampleWords(24, 24)}`, "24 h · 24 requests");
  assert.equal(sampleWords(null, 0), null);
  // A dash carries no number, whatever count came with it.
  assert.equal(sampleWords(null, 3), null);
  assert.equal(sampleWords(2, 0), null);
});

test("a picked type with no stage time says why, and one with some says nothing", () => {
  const all = flow();
  assert.equal(
    noStageTimeWords("Refactor", 0, 30, stagesFor(all, "refactor")),
    "No request of type Refactor was merged in these 30 days, so there is no stage time to show.",
  );
  assert.equal(
    noStageTimeWords("Refactor", 3, 90, stagesFor(all, "refactor")),
    "No stage time is known for the 3 merged requests of type Refactor.",
  );
  assert.equal(
    noStageTimeWords("Refactor", 1, 90, stagesFor(all, "refactor")),
    "No stage time is known for the 1 merged request of type Refactor.",
  );
  assert.equal(noStageTimeWords("Feature", 2, 30, stagesFor(all, "feature")), null);
  // Coding alone is still a time.
  assert.equal(noStageTimeWords("Bug fix", 1, 30, stagesFor(all, "bug_fix")), null);
});
