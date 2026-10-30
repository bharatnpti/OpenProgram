import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { WarningGroupDto, WarningItemDto } from "../../api/schema";
import { rollUpChips, warningsCountLine, worstVerdict } from "./programmeWords.ts";

describe("a programme's dot", () => {
  const none = {
    on_track: 0,
    at_risk: 0,
    off_track: 0,
    done: 0,
    no_date: 0,
    not_enough_data: 0,
    none: 0,
  };
  test("the worst verdict among its projects", () => {
    assert.equal(worstVerdict({ ...none, on_track: 3, at_risk: 1 }), "at_risk");
    assert.equal(worstVerdict({ ...none, at_risk: 1, no_date: 2 }), "no_date");
    assert.equal(worstVerdict({ ...none, off_track: 1, no_date: 2 }), "off_track");
    assert.equal(worstVerdict({ ...none, done: 2 }), "done");
  });
  test("none for a programme with no verdict, or no counts", () => {
    assert.equal(worstVerdict({ ...none, none: 4 }), null);
    assert.equal(worstVerdict(undefined), null);
  });
});

const nodes = (counts: Record<string, number>) =>
  Object.entries(counts).flatMap(([kind, count]) =>
    Array.from({ length: count }, () => ({ kind })),
  );

describe("what rolls up into a programme", () => {
  test("projects, pods, the people listed and the tasks; workstreams only when used", () => {
    assert.deepEqual(rollUpChips(nodes({ project: 3, pod: 4, developer: 13, task: 16 }), null), [
      "3 projects",
      "4 pods",
      "13 people",
      "16 tasks",
    ]);
    assert.deepEqual(rollUpChips(nodes({ project: 1, workstream: 2, pod: 1, task: 1 }), null), [
      "1 project",
      "2 workstreams",
      "1 pod",
      "1 task",
    ]);
  });

  test("a tree that names nobody counts the programme's people, and none under five", () => {
    assert.deepEqual(rollUpChips(nodes({ project: 2, pod: 2 }), 13), [
      "2 projects",
      "2 pods",
      "13 people",
    ]);
    assert.deepEqual(rollUpChips(nodes({ project: 1, pod: 1 }), 4), ["1 project", "1 pod"]);
    assert.deepEqual(rollUpChips([], null), ["0 projects", "0 pods"]);
  });
});

const item = (overrides: Partial<WarningItemDto> = {}): WarningItemDto => ({
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
  pods: [],
  owner_says: null,
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

describe("the warnings' count line", () => {
  test("the total, the owners who say on track, and drift, each said with 0 too", () => {
    assert.equal(
      warningsCountLine({
        total: 5,
        projects: 2,
        groups: [
          group({ kind: "blockers", count: 2, items: [item(), item({ id: "b" })] }),
          group({
            kind: "tools_disagree",
            count: 2,
            items: [item({ id: "c", watermelon: true }), item({ id: "d" })],
          }),
          group({ kind: "done_in_code", count: 1, items: [item({ id: "e" })] }),
        ],
      }),
      "5 open across 2 projects · 1 where the owner says on track · 3 drift.",
    );
    assert.equal(
      warningsCountLine({ total: 1, projects: 1, groups: [group({ count: 1 })] }),
      "1 open across 1 project · 0 where the owner says on track · 0 drift.",
    );
  });

  test("nothing open says so", () => {
    assert.equal(
      warningsCountLine({ total: 0, projects: 0, groups: [] }),
      "Nothing open: Jira and Git agree with what was reported.",
    );
  });
});
