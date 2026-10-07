import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  heatTiles,
  momentum,
  momentumNote,
  noPodTiles,
  programCell,
  signalHref,
  tileColours,
  tileKey,
  tileReasons,
  tileWeights,
  todayVerdict,
  tooltipText,
} from "./heat.ts";

type ReasonCell = NonNullable<Parameters<typeof tileReasons>[0]>[number];

function reasonCell(overrides: Partial<ReasonCell> & { kind: string; id: string }): ReasonCell {
  const { kind, id, ...rest } = overrides;
  return {
    rag: "amber",
    reason: "3 of 4 unanswered today",
    reasons: ["3 of 4 haven't answered today's check-in: Ada Lind, Ben Okafor and Cleo Morales."],
    entity_ref: { kind, id },
    ...rest,
  };
}

describe("tile reasons", () => {
  test("each cell's reason and tooltip, by node kind and id", () => {
    const reasons = tileReasons([
      reasonCell({ kind: "pod", id: "pod-web" }),
      reasonCell({
        kind: "workstream",
        id: "ws-cart",
        rag: "unknown",
        reason: "No tasks linked",
        reasons: ["No tasks are linked to this workstream, so nothing reports a status for it."],
      }),
    ]);

    assert.deepEqual(reasons.get(tileKey("pod", "pod-web")), {
      reason: "3 of 4 unanswered today",
      tooltip:
        "Amber\n• 3 of 4 haven't answered today's check-in: Ada Lind, Ben Okafor and Cleo Morales.",
    });
    assert.equal(
      reasons.get(tileKey("workstream", "ws-cart"))?.tooltip,
      "No status\n• No tasks are linked to this workstream, so nothing reports a status for it.",
    );
  });

  test("a cell from an older backend, with no reason, gives its tile none", () => {
    assert.equal(tileReasons([reasonCell({ kind: "pod", id: "p", reason: null })]).size, 0);
    assert.equal(tileReasons(undefined).size, 0);
  });

  test("a tile is coloured by its heat map cell, reason or not", () => {
    const colours = tileColours([
      reasonCell({ kind: "pod", id: "pod-web", rag: "red" }),
      reasonCell({ kind: "project", id: "p", rag: "green", reason: null }),
    ]);
    assert.equal(colours.get(tileKey("pod", "pod-web")), "red");
    assert.equal(colours.get(tileKey("project", "p")), "green");
    assert.equal(tileColours(undefined).size, 0);
  });

  test("a tooltip with no reasons listed says the reason itself", () => {
    assert.equal(tooltipText("red", "2 blockers (Ada, Ben)", []), "Red\n• 2 blockers (Ada, Ben)");
  });
});

type NoPodCell = NonNullable<Parameters<typeof noPodTiles>[0]>[number];

function noPodCell(overrides: Partial<NoPodCell> & { id: string }): NoPodCell {
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

describe("the no pod row", () => {
  test("keeps only people in no team, named, with their check-in state", () => {
    const { tiles, total } = noPodTiles(
      [
        noPodCell({ id: "u-elena", name: "Elena Fischer" }),
        noPodCell({ id: "u-omar", name: "Omar Haddad", row: "developer", rag: "amber" }),
        noPodCell({ id: "pod-1", row: "pod", rag: "red" }),
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
        reason: null,
        reasons: ["No pod, outside team colours: Confirmed status has no blockers."],
      },
    ]);
  });

  test("carries the backend's reason and every reason for the tooltip", () => {
    const { tiles } = noPodTiles(
      [
        noPodCell({
          id: "u-fay",
          name: "Fay Moreau",
          rag: "amber",
          source: "inferred",
          reason: "Check-in unanswered today",
          reasons: ["Hasn't answered today's check-in.", "In no pod."],
        }),
      ],
      4,
    );
    assert.deepEqual(
      tiles.map((tile) => [tile.state, tile.reason, tile.reasons]),
      [
        [
          "inferred",
          "Check-in unanswered today",
          ["Hasn't answered today's check-in.", "In no pod."],
        ],
      ],
    );
  });

  test("worst first, silence as no status, and never an id for a name", () => {
    const { tiles, total } = noPodTiles(
      [
        noPodCell({ id: "u-1", name: "Ada" }),
        noPodCell({ id: "u-2", rag: "unknown", source: "unknown" }),
        noPodCell({ id: "u-3", name: "Ben", rag: "amber", source: "partial" }),
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

  test("no heat map, or nobody outside the teams, is no row", () => {
    assert.deepEqual(noPodTiles(undefined, 4), { tiles: [], total: 0 });
    assert.deepEqual(noPodTiles([noPodCell({ id: "pod-1", row: "pod" })], 4), {
      tiles: [],
      total: 0,
    });
  });
});

describe("signals and momentum", () => {
  test("a signal opens its node's delivery page, else the Signals list", () => {
    assert.equal(signalHref({ kind: "pod", id: "pod-web" }), "/delivery/pod/pod-web");
    assert.equal(signalHref({ kind: "program", id: "prog/1" }), "/delivery/program/prog%2F1");
    assert.equal(signalHref({ kind: "signals", id: null }), "/signals");
    assert.equal(signalHref({ kind: "project", id: null }), "/signals");
  });

  const point = (rag: "red" | "amber" | "green" | "unknown") => ({
    as_of: "2026-10-01",
    rag,
    source: "confirmed" as const,
    score: { unknown: 0, red: 1, amber: 2, green: 3 }[rag],
  });

  test("momentum is read over reported days only, and says where it stands now", () => {
    assert.equal(momentum([point("red"), point("red"), point("red")]).label, "steady, now red");
    assert.equal(
      momentum([point("unknown"), point("red"), point("amber"), point("green")]).label,
      "improving, now green",
    );
    assert.equal(momentum([point("green"), point("amber")]).label, "sliding, now amber");
    // Opening unknown then going red must not draw as a climb.
    assert.equal(
      momentum([point("unknown"), point("unknown"), point("red")]).label,
      "not enough reported days",
    );
    assert.equal(momentum(undefined).label, "not enough reported days");
  });

  test("the note says how many of the days the direction rests on", () => {
    const days = [
      ...Array.from({ length: 25 }, () => point("unknown")),
      point("amber"),
      point("amber"),
      point("amber"),
      point("amber"),
      point("amber"),
    ];
    assert.equal(momentumNote(momentum(days)), "30 days · 5 days reported · steady, now amber");
    const full = Array.from({ length: 30 }, () => point("green"));
    assert.equal(momentumNote(momentum(full)), "30 days · steady, now green");
    assert.equal(momentumNote(momentum(undefined)), "30 days · not enough reported days");
    // The history keeps working days only, so it can hold fewer entries than the window has days.
    assert.equal(
      momentumNote(momentum([point("unknown"), point("red")]), 14),
      "14 days · 1 day reported · not enough reported days",
    );
  });

  test("unreported days leave a gap in the line, not a zero", () => {
    assert.deepEqual(momentum([point("unknown"), point("red"), point("green")]).values, [
      null,
      0,
      1,
    ]);
  });
});

describe("a heat row", () => {
  type Node = { id: string; rag: "red" | "amber" | "green" | "unknown" };
  const rank = (...rags: Node["rag"][]): Node[] => rags.map((rag, i) => ({ id: `n${i}`, rag }));
  const colour = (node: Node) => node.rag;

  test("shows the worst four, and says how many it left out", () => {
    const row = heatTiles(rank("red", "red", "amber", "green", "green", "green"), colour, 4);
    assert.equal(row.shown.length, 4);
    assert.equal(row.hidden, 2);
    assert.equal(row.hiddenAsBad, false);
  });

  test("a tie at the edge is shown whole: five amber pods are five tiles, not four", () => {
    const row = heatTiles(rank("amber", "amber", "amber", "amber", "amber"), colour, 4);
    assert.equal(row.shown.length, 5);
    assert.equal(row.hidden, 0);
  });

  test("a tie stops at the cap, and the row says what it left out is as bad", () => {
    const row = heatTiles(rank(...Array<Node["rag"]>(12).fill("amber")), colour, 4, 8);
    assert.equal(row.shown.length, 8);
    assert.equal(row.hidden, 4);
    assert.equal(row.hiddenAsBad, true);
  });

  test("only ties with the last tile are added: a worse tile above the edge never grows the row", () => {
    const row = heatTiles(rank("red", "red", "red", "amber", "amber", "green"), colour, 4);
    assert.deepEqual(
      row.shown.map((node) => node.rag),
      ["red", "red", "red", "amber", "amber"],
    );
    assert.equal(row.hidden, 1);
  });

  test("a green tie is not worth the room", () => {
    const row = heatTiles(rank("green", "green", "green", "green", "green"), colour, 4);
    assert.equal(row.shown.length, 4);
    assert.equal(row.hidden, 1);
  });

  test("unknown is not green: ties with no status are shown whole too", () => {
    assert.equal(
      heatTiles(rank("unknown", "unknown", "unknown", "unknown", "unknown"), colour, 4).shown
        .length,
      5,
    );
  });

  test("a short row and an empty one are as they are", () => {
    assert.equal(heatTiles(rank("amber", "red"), colour, 4).shown.length, 2);
    assert.deepEqual(heatTiles([], colour, 4), { shown: [], hidden: 0, hiddenAsBad: false });
  });

  test("tiles with more reasons rank first among equals", () => {
    const weights = tileWeights([
      reasonCell({ kind: "pod", id: "a", reasons: ["one"] }),
      reasonCell({ kind: "pod", id: "b", reasons: ["one", "two"] }),
      reasonCell({ kind: "pod", id: "c", reasons: undefined, reason: null }),
    ]);
    assert.equal(weights.get(tileKey("pod", "a")), 1);
    assert.equal(weights.get(tileKey("pod", "b")), 2);
    assert.equal(weights.get(tileKey("pod", "c")), 0);
    assert.equal(tileWeights(undefined).size, 0);
  });
});

describe("the verdict Today opens with", () => {
  // The round that found it: every tile amber, the program red on two different blockers.
  const tiles = {
    rag: "amber" as const,
    headline: "Amber: signals disagree on 4 issues.",
    detail: "Also: 2 open blockers, including Omar Haddad on IDP-6.",
  };

  test("the program's own cell is found by kind and id", () => {
    const cells = [
      reasonCell({ kind: "project", id: "program-platform", rag: "green" }),
      reasonCell({
        kind: "program",
        id: "program-platform",
        rag: "red",
        reason: " 2 blockers (Omar, Raj) ",
      }),
    ];
    assert.deepEqual(programCell(cells, "program-platform"), {
      rag: "red",
      reason: "2 blockers (Omar, Raj)",
    });
    assert.equal(programCell(cells, "another-program"), null);
    assert.equal(programCell(undefined, "program-platform"), null);
  });

  test("a red program leads over amber teams, with its own first reason", () => {
    const verdict = todayVerdict(tiles, { rag: "red", reason: "2 blockers (Omar, Raj)" });
    assert.equal(verdict.rag, "red");
    assert.equal(verdict.headline, "Red: 2 blockers (Omar, Raj).");
    assert.equal(
      verdict.detail,
      "Teams read Amber: signals disagree on 4 issues. Also: 2 open blockers, including Omar Haddad on IDP-6.",
    );
    assert.equal(verdict.fromProgram, true);
    // Without a reason the heat map gives it, the colour still leads and Delivery says why.
    assert.equal(
      todayVerdict(tiles, { rag: "red", reason: null }).headline,
      "Red: the program's own status is red. Delivery says why.",
    );
  });

  test("the server's verdict stands when the program is no worse than its teams", () => {
    for (const program of [
      { rag: "amber" as const, reason: "Signals disagree on 3 issues" },
      { rag: "green" as const, reason: "All 6 confirmed" },
      { rag: "unknown" as const, reason: "No status reported yet" },
      null,
    ]) {
      assert.deepEqual(todayVerdict(tiles, program), {
        rag: "amber",
        headline: tiles.headline,
        detail: tiles.detail,
        fromProgram: false,
      });
    }
  });

  test("an amber program over green teams leads too, with no second line when the teams said none", () => {
    const verdict = todayVerdict(
      { rag: "green", headline: "Green: everyone confirmed with no open blockers.", detail: null },
      { rag: "amber", reason: "Target date near" },
    );
    assert.equal(verdict.rag, "amber");
    assert.equal(verdict.headline, "Amber: Target date near.");
    assert.equal(verdict.detail, "Teams read Green: everyone confirmed with no open blockers.");
  });
});
