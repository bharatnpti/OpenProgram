import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type {
  FocusTaskDto,
  ScopeProjectDto,
  WarningGroupDto,
  WarningItemDto,
} from "../../api/schema";
import type { TaskRow } from "./taskUpdate";
import {
  nextTaskWhy,
  nextTasks,
  oldestWarnings,
  podLineWords,
  portfolioLine,
  scopeActions,
} from "./todayMore.ts";

describe("the portfolio line", () => {
  test("off track, at risk and no date, each with its count, 0 included", () => {
    assert.equal(
      portfolioLine({
        on_track: 3,
        at_risk: 0,
        off_track: 1,
        done: 0,
        no_date: 2,
        not_enough_data: 0,
        none: 0,
      }),
      "Portfolio: 1 off track · 0 at risk · 2 no date",
    );
  });
});

const item = (overrides: Partial<WarningItemDto>): WarningItemDto => ({
  id: "i",
  kind: "blocker",
  key: "CHK-1",
  line: "Blocked",
  age_days: 3,
  since: null,
  age_known: true,
  owner: null,
  owner_team: null,
  owner_is_you: false,
  project_id: "p",
  project_name: "P",
  severity: "amber",
  evidence_url: null,
  watermelon: false,
  ...overrides,
});

const group = (overrides: Partial<WarningGroupDto>): WarningGroupDto => ({
  kind: "blockers",
  title: "Blockers people reported",
  count: 0,
  items: [],
  counts_only: false,
  ...overrides,
});

describe("the oldest open warnings", () => {
  test("the three oldest across every listed group, each with its kind, and none twice", () => {
    const rows = oldestWarnings({
      groups: [
        group({ items: [item({ id: "a", age_days: 35 }), item({ id: "b", age_days: 2 })] }),
        group({
          kind: "no_movement",
          title: "No movement",
          items: [item({ id: "c", age_days: 24 }), item({ id: "a", age_days: 35 })],
        }),
        group({
          kind: "review_stuck",
          title: "Review stuck",
          items: [item({ id: "d", age_days: 22 })],
        }),
        group({ kind: "tools_disagree", counts_only: true, count: 4 }),
      ],
    });
    assert.deepEqual(
      rows.map((row) => [row.id, row.group]),
      [
        ["a", "blockers"],
        ["c", "no_movement"],
        ["d", "review_stuck"],
      ],
    );
  });
});

describe("a scrum master's other pod", () => {
  test("replies and blockers, each only once read", () => {
    assert.equal(
      podLineWords({ replied: 4, people: 6, blockers: 2 }),
      "4 of 6 replied · 2 open blockers",
    );
    assert.equal(podLineWords({ replied: null, people: null, blockers: 0 }), "no open blockers");
    assert.equal(
      podLineWords({ replied: 0, people: 0, blockers: 1 }),
      "nobody asked to check in · 1 open blocker",
    );
  });
});

const project = (overrides: Partial<ScopeProjectDto>): ScopeProjectDto => ({
  id: "p-1",
  name: "Checkout",
  program_id: null,
  program_name: null,
  plan: null,
  added_keys: [],
  without_label_keys: [],
  scope_day: null,
  hygiene_open: 0,
  ...overrides,
});

const plan = {
  planned_on: "2026-10-05",
  planned: 8,
  added: 2,
  without_label: 1,
  without_label_percent: 50,
  change_label: "CR",
  removed: 0,
  added_days: null,
};

describe("what a product owner's projects ask of them", () => {
  test("a baseline to take, labels missing, then the open hygiene findings, own projects only", () => {
    const actions = scopeActions(
      [
        project({ id: "p-1", name: "Checkout", plan, hygiene_open: 3 }),
        project({ id: "p-2", name: "Identity" }),
        project({ id: "p-3", name: "Insights", plan: { ...plan, without_label: 0 } }),
        project({ id: "p-4", name: "Not mine", hygiene_open: 5 }),
      ],
      new Set(["p-1", "p-2", "p-3"]),
      true,
    );
    assert.deepEqual(
      actions.map((action) => [action.project, action.anchor, action.text]),
      [
        [
          "Checkout",
          "baseline",
          "1 requirement joined after planning without the CR label: label it or take a new baseline.",
        ],
        [
          "Identity",
          "baseline",
          "No planning baseline yet: mark planning complete to count what joins after.",
        ],
        ["Checkout", "hygiene", "3 Jira hygiene findings open: fix, waive or re-check."],
      ],
    );
  });

  test("a reader who does not read baselines is asked only about hygiene", () => {
    const actions = scopeActions(
      [project({ plan: null, hygiene_open: 1 })],
      new Set(["p-1"]),
      false,
    );
    assert.deepEqual(
      actions.map((action) => action.anchor),
      ["hygiene"],
    );
  });
});

const row = (group: TaskRow["group"], overrides: Partial<FocusTaskDto> = {}): TaskRow => ({
  task: {
    id: `t-${group}`,
    name: group,
    rag: "amber",
    source: "confirmed",
    confidence: null,
    deadline: null,
    tracker_status: null,
    my_eta: null,
    my_eta_label: null,
    last_update: null,
    blocker_ids: [],
    can_move_in_tracker: false,
    observed: null,
    ...overrides,
  },
  blockers: [],
  group,
  late: group === "past_due",
  etaLate: group === "eta_late",
});

describe("a developer's next tasks", () => {
  test("the first three open ones, as the task list sorts them", () => {
    const rows = [row("blocked"), row("done"), row("past_due"), row("in_progress"), row("todo")];
    assert.deepEqual(
      nextTasks(rows).map((item) => item.group),
      ["blocked", "past_due", "in_progress"],
    );
  });

  test("why each is next", () => {
    assert.equal(nextTaskWhy(row("blocked")), "Blocked");
    assert.equal(nextTaskWhy(row("past_due")), "Past due");
    assert.equal(nextTaskWhy(row("eta_late")), "Your ETA is after its due date");
    assert.equal(nextTaskWhy(row("in_progress", { tracker_status: "In Review" })), "In Review");
    assert.equal(nextTaskWhy(row("todo")), "To do");
  });
});
