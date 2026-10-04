import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { noPodTiles } from "./noPodRow.ts";

type Cell = NonNullable<Parameters<typeof noPodTiles>[0]>[number];

function cell(overrides: Partial<Cell> & { id: string }): Cell {
  const { id, ...rest } = overrides;
  return {
    row: "no pod",
    rag: "green",
    source: "confirmed",
    why: "No pod, outside team colours: Confirmed status has no blockers.",
    name: null,
    entity_ref: { id },
    ...rest,
  };
}

describe("noPodTiles", () => {
  test("keeps only the no-pod row, named, with each person's check-in state", () => {
    const { tiles, total } = noPodTiles(
      [
        cell({ id: "u-elena", name: "Elena Fischer" }),
        cell({ id: "u-omar", name: "Omar Haddad", row: "developer", rag: "amber" }),
        cell({ id: "pod-1", row: "pod", rag: "red" }),
      ],
      4,
    );

    assert.equal(total, 1);
    assert.deepEqual(tiles, [
      {
        id: "u-elena",
        name: "Elena Fischer",
        rag: "green",
        state: "confirmed",
        why: "No pod, outside team colours: Confirmed status has no blockers.",
      },
    ]);
  });

  test("worst first, silence as no status, and never an id for a name", () => {
    const { tiles, total } = noPodTiles(
      [
        cell({ id: "u-1", name: "Ada" }),
        cell({ id: "u-2", rag: "unknown", source: "unknown" }),
        cell({ id: "u-3", name: "Ben", rag: "amber", source: "partial" }),
      ],
      2,
    );

    assert.equal(total, 3);
    assert.deepEqual(
      tiles.map((tile) => [tile.name, tile.rag, tile.state]),
      [
        ["Ben", "amber", "partial"],
        ["A team member", "unknown", "no status"],
      ],
    );
  });

  test("no heat map, or nobody outside the teams, is an empty row", () => {
    assert.deepEqual(noPodTiles(undefined, 4), { tiles: [], total: 0 });
    assert.deepEqual(noPodTiles([cell({ id: "pod-1", row: "pod" })], 4), {
      tiles: [],
      total: 0,
    });
  });
});
