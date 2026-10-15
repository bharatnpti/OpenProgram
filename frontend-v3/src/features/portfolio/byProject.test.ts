import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { WarningGroupDto, WarningItemDto } from "../../api/schema";
import { byProject, countedApart, isDrift, projectCount } from "./byProject.ts";

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
  project_id: "p-1",
  project_name: "Checkout",
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

describe("warnings by project", () => {
  test("the worst project first, and in each the worst then the oldest", () => {
    const projects = byProject({
      groups: [
        group({
          kind: "blockers",
          items: [
            item({ id: "a", age_days: 2 }),
            item({ id: "b", project_id: "p-2", project_name: "Identity", severity: "red" }),
          ],
        }),
        group({
          kind: "review_stuck",
          items: [item({ id: "c", age_days: 9 }), item({ id: "d", severity: "red", age_days: 1 })],
        }),
      ],
    });
    assert.deepEqual(
      projects.map((project) => [project.name, project.worst, project.items.map((row) => row.id)]),
      [
        ["Checkout", "red", ["d", "c", "a"]],
        ["Identity", "red", ["b"]],
      ],
    );
    assert.equal(projects[0].items[0].group, "review_stuck");
    assert.equal(projectCount(projects[1]), "1 warning");
  });

  test("a group read only as a count lists nothing and is said apart", () => {
    const view = {
      groups: [
        group({ kind: "blockers", count: 7, counts_only: true }),
        group({ kind: "no_movement", title: "No movement", count: 0 }),
      ],
    };
    assert.deepEqual(byProject(view), []);
    assert.equal(
      countedApart(view),
      "Also 7 blockers people reported, counted across the projects rather than listed.",
    );
    assert.equal(countedApart({ groups: [group({ count: 2 })] }), null);
  });

  test("drift is what the tools say against the reports or each other", () => {
    assert.equal(isDrift("tools_disagree"), true);
    assert.equal(isDrift("done_in_code"), true);
    assert.equal(isDrift("blockers"), false);
  });
});

describe("what the owner says, by project", () => {
  test("each row keeps the owner's words and the pods the read sent, for the shared fold", () => {
    const says = { text: "Waiting on finance", source: "confirmed" as const, on: "2026-10-14" };
    const [project] = byProject({
      groups: [
        group({
          kind: "review_stuck",
          items: [
            item({
              id: "a",
              owner: "Noah Weber",
              owner_says: says,
              pods: [{ id: "pod-pay", name: "Payments Pod" }],
            }),
            item({ id: "b", owner: null, owner_team: "Payments Pod" }),
          ],
        }),
      ],
    });
    assert.deepEqual(project.items.find((row) => row.id === "a")?.owner_says, says);
    assert.deepEqual(project.items.find((row) => row.id === "a")?.pods, [
      { id: "pod-pay", name: "Payments Pod" },
    ]);
    assert.equal(project.items.find((row) => row.id === "b")?.owner_says, null);
  });
});
