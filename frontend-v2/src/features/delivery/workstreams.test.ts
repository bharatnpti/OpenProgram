import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { hierarchyLabel, isEmptyWorkstream, rollupCountsLine } from "./workstreams.ts";

describe("hierarchyLabel", () => {
  test("names the workstream level only when one is in use", () => {
    assert.equal(hierarchyLabel([{ id: "ws-pay" }]), "Hierarchy · program → project → workstream");
    // The QA org: five workstreams, none holding a task, so none listed.
    assert.equal(hierarchyLabel([]), "Hierarchy · program → project");
  });
});

describe("isEmptyWorkstream", () => {
  test("only a workstream the backend marks not in use", () => {
    assert.equal(isEmptyWorkstream("workstream", { in_use: false }), true);
    assert.equal(isEmptyWorkstream("workstream", { in_use: true }), false);
    assert.equal(isEmptyWorkstream("project", { in_use: false }), false);
    assert.equal(isEmptyWorkstream("workstream", undefined), false);
  });

  test("an older backend that sends no in_use shows its workstreams as before", () => {
    const older = {} as { in_use: boolean };
    assert.equal(isEmptyWorkstream("workstream", older), false);
  });
});

describe("rollupCountsLine", () => {
  test("leaves workstreams out when none is in use", () => {
    assert.equal(
      rollupCountsLine({ projects: 2, workstreams: 0, pods: 3 }),
      "2 projects · 3 pods roll up into this program.",
    );
  });

  test("counts them when there are some", () => {
    assert.equal(
      rollupCountsLine({ projects: 1, workstreams: 1, pods: 4 }),
      "1 project · 1 workstream · 4 pods roll up into this program.",
    );
  });
});
