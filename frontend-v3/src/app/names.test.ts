import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  fromDirectory,
  fromHeatmapCells,
  fromNodes,
  fromRoster,
  indexNames,
  nameLookup,
} from "./names.ts";

describe("where names come from", () => {
  test("members from config or graph nodes, by node id and by chat id", () => {
    const entries = fromNodes([
      {
        id: "U1001",
        kind: "developer",
        name: "Asha Rao",
        metadata: { chat_external_id: "C-ASHA" },
      },
      { id: "pod-payments", kind: "pod", name: "Payments Pod", metadata: {} },
      { id: "U1002", kind: "developer", name: "Liam Chen", metadata: null },
      { id: "U1003", kind: "developer", name: "  ", metadata: {} },
    ]);
    assert.deepEqual(entries, [
      { id: "U1001", name: "Asha Rao", aliases: ["C-ASHA"] },
      { id: "U1002", name: "Liam Chen", aliases: [] },
    ]);
  });

  test("the heat map names every member in a team and each person in no team", () => {
    assert.deepEqual(
      fromHeatmapCells([
        { entity_ref: { kind: "developer", id: "U1007" }, name: "Kai Thompson" },
        { entity_ref: { kind: "developer", id: "U1011" }, name: "Elena Fischer" },
        { entity_ref: { kind: "pod", id: "pod-payments" }, name: "Payments Pod" },
        { entity_ref: { kind: "developer", id: "U9" }, name: null },
      ]),
      [
        { id: "U1007", name: "Kai Thompson" },
        { id: "U1011", name: "Elena Fischer" },
      ],
    );
    assert.deepEqual(fromHeatmapCells(undefined), []);
  });

  test("the directory names the people on pods, projects and workstreams, by either id", () => {
    const item = (
      people: { key: string; id: string; member_id: string | null; name: string | null }[],
    ) =>
      ({ people }) as Parameters<typeof fromDirectory>[0] extends (infer T)[] | undefined
        ? T
        : never;
    assert.deepEqual(
      fromDirectory([
        item([
          { key: "owner_id", id: "C-MINA", member_id: "U1003", name: "Mina Patel" },
          { key: "sm_id", id: "U1006", member_id: "U1006", name: "Ira Novak" },
          { key: "tpm_id", id: "C-X", member_id: null, name: null },
        ]),
      ]),
      [
        { id: "C-MINA", name: "Mina Patel", aliases: ["U1003"] },
        { id: "U1006", name: "Ira Novak", aliases: ["U1006"] },
      ],
    );
  });

  test("the local roster", () => {
    assert.deepEqual(fromRoster([{ id: "U1", name: "A" }]), [{ id: "U1", name: "A" }]);
    assert.deepEqual(fromRoster(undefined), []);
  });
});

describe("the index and the lookup", () => {
  test("the first source to name an id keeps it", () => {
    const index = indexNames(
      [{ id: "U1", name: "Asha Rao" }],
      [{ id: "U1", name: "Someone Else" }],
      [{ id: "U2", name: "Liam Chen", aliases: ["C-LIAM"] }],
    );
    assert.equal(index.get("U1"), "Asha Rao");
    assert.equal(index.get("U2"), "Liam Chen");
    assert.equal(index.get("C-LIAM"), "Liam Chen");
  });

  test("an id nobody names stays an id, and says it is unknown", () => {
    const names = nameLookup(indexNames([{ id: "U1", name: "Asha Rao" }]));
    assert.equal(names("U1"), "Asha Rao");
    assert.equal(names("U0TESTUNK1"), "U0TESTUNK1");
    assert.equal(names(null), "—");
    assert.equal(names(undefined), "—");
    assert.equal(names.known("U1"), true);
    assert.equal(names.known("U0TESTUNK1"), false);
    assert.equal(names.known(null), false);
    // Nobody names it: the id stays on screen, after a plain word, never a guessed name.
    assert.equal(names.or("U1", "someone"), "Asha Rao");
    assert.equal(names.or("U0TESTUNK1", "someone"), "someone (U0TESTUNK1)");
    assert.equal(names.or(null, "someone"), "someone");
  });
});
