import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { taskChips } from "./taskChips.ts";

describe("taskChips", () => {
  test("a tracker task shows its tracker status before its colour", () => {
    // N17: CHK-4 moved to In Progress in Jira, but its row read only "unknown".
    assert.deepEqual(taskChips({ rag: "unknown", tracker_status: "In Progress" }), [
      {
        kind: "tracker",
        label: "In Progress",
        title: "Status in the issue tracker: In Progress",
      },
      { kind: "rag", label: "unknown", rag: "unknown" },
    ]);
  });

  test("the tracker's own wording is kept, trimmed", () => {
    const [tracker] = taskChips({ rag: "green", tracker_status: "  Done " });
    assert.deepEqual(tracker, {
      kind: "tracker",
      label: "Done",
      title: "Status in the issue tracker: Done",
    });
  });

  test("a task with no tracker status shows its colour only", () => {
    const colourOnly = [{ kind: "rag", label: "amber", rag: "amber" }];
    // An older server sends no field; a seeded task sends null; blank is nothing.
    assert.deepEqual(taskChips({ rag: "amber" }), colourOnly);
    assert.deepEqual(taskChips({ rag: "amber", tracker_status: null }), colourOnly);
    assert.deepEqual(taskChips({ rag: "amber", tracker_status: "   " }), colourOnly);
  });
});
