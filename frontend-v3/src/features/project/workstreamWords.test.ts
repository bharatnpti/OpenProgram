import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { byTarget, workstreamLine, workstreamsMeta } from "./workstreamWords.ts";

const day = (iso: string) => `day ${iso}`;

describe("a workstream's row", () => {
  test("type, phase, target, the people named, and where its code lives", () => {
    assert.equal(
      workstreamLine(
        {
          metadata: {
            type: "Build",
            phase: "Delivery",
            target_date: "2026-11-30",
            github_repos: "acme/checkout-api, acme/storefront-web",
          },
          people: [
            { key: "owner_id", id: "U1", member_id: "U1", name: "Mina Patel" },
            { key: "sm_id", id: "U9", member_id: null, name: null },
          ],
        },
        true,
        day,
      ),
      "Build · phase Delivery · target day 2026-11-30 · Owner Mina Patel · code in acme/checkout-api, acme/storefront-web",
    );
  });

  test("a reader who sees no names gets none, and empty facts are left out", () => {
    assert.equal(
      workstreamLine(
        {
          metadata: { type: " " },
          people: [{ key: "owner_id", id: "U1", member_id: "U1", name: "Mina Patel" }],
        },
        false,
        day,
      ),
      "",
    );
  });

  test("in order of target date, none last, then by name", () => {
    const items: { name: string; metadata: Record<string, string> }[] = [
      { name: "Zed", metadata: {} },
      { name: "Beta", metadata: { target_date: "2026-12-01" } },
      { name: "Alpha", metadata: {} },
      { name: "Gamma", metadata: { target_date: "2026-11-01" } },
    ];
    assert.deepEqual(
      byTarget(items).map((item) => item.name),
      ["Gamma", "Beta", "Alpha", "Zed"],
    );
  });
});

test("the section's line says what a fold holds for the reader", () => {
  assert.equal(workstreamsMeta(2, true), "2 workstreams · open one for its tasks and reasons");
  assert.equal(workstreamsMeta(1, false), "1 workstream · open one for its facts and pods");
});
