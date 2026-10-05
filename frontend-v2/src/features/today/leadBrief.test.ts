import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { briefDateLabel, briefParts } from "./leadBrief.ts";

describe("briefParts", () => {
  test("a structured brief is its verdict and its bullets", () => {
    assert.deepEqual(
      briefParts({
        body: "Amber: one update is partial.\n- Ben confirms.\n- Cleo reviews web !4 (SHOP-11).",
        verdict: "Amber: one update is partial.",
        bullets: ["Ben confirms.", "Cleo reviews web !4 (SHOP-11)."],
      }),
      {
        verdict: "Amber: one update is partial.",
        bullets: ["Ben confirms.", "Cleo reviews web !4 (SHOP-11)."],
      },
    );
  });

  test("an older free-text brief is split into sentence bullets, with no verdict", () => {
    assert.deepEqual(
      briefParts({
        body:
          "Since 2 Mar 14:16 UTC, 3 staff checked in. SHOP-8 is done in the tracker. " +
          "Ada Lind's review request needs a PM.",
        verdict: null,
        bullets: [],
      }),
      {
        verdict: null,
        bullets: [
          "Since 2 Mar 14:16 UTC, 3 staff checked in.",
          "SHOP-8 is done in the tracker.",
          "Ada Lind's review request needs a PM.",
        ],
      },
    );
  });

  test("a body from an older backend without the fields still reads", () => {
    assert.deepEqual(briefParts({ body: "Verdict.\n- One.\n- Two." }), {
      verdict: null,
      bullets: ["Verdict.", "One.", "Two."],
    });
  });
});

describe("briefDateLabel", () => {
  const now = new Date("2026-03-09T12:00:00Z");

  test("says the day in words, on the reader's clock", () => {
    assert.equal(
      briefDateLabel("2026-03-08T14:16:44Z", { now, timeZone: "Asia/Kolkata" }),
      "Sunday 8 March, 19:46",
    );
    assert.equal(
      briefDateLabel("2026-03-08T14:16:44Z", { now, timeZone: "UTC" }),
      "Sunday 8 March, 14:16",
    );
  });

  test("says the year only when it is not this year", () => {
    assert.equal(
      briefDateLabel("2025-12-31T09:00:00Z", { now, timeZone: "UTC" }),
      "Wednesday 31 December 2025, 09:00",
    );
  });

  test("an unreadable date says nothing", () => {
    assert.equal(briefDateLabel("not a date", { now }), "");
  });
});
