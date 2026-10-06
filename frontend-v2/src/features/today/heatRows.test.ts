import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { shownHeatRows } from "./heatRows.ts";

describe("shownHeatRows", () => {
  test("no workstreams row when no workstream is in use", () => {
    const rows = [
      { kind: "project", total: 2 },
      { kind: "workstream", total: 0 },
      { kind: "pod", total: 4 },
    ];

    assert.deepEqual(
      shownHeatRows(rows).map((row) => row.kind),
      ["project", "pod"],
    );
  });

  test("the workstreams row once one is in use", () => {
    const rows = [
      { kind: "project", total: 2 },
      { kind: "workstream", total: 1 },
      { kind: "pod", total: 4 },
    ];

    assert.deepEqual(
      shownHeatRows(rows).map((row) => row.kind),
      ["project", "workstream", "pod"],
    );
  });

  test("projects and pods keep their rows even when empty", () => {
    const rows = [
      { kind: "project", total: 0 },
      { kind: "pod", total: 0 },
    ];

    assert.deepEqual(shownHeatRows(rows), rows);
  });
});
