import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryImpactResponse } from "../../api/schema";

import { OVERALL_ORDER, overallSections } from "./sections.ts";
import {
  causeBars,
  committedLine,
  daysWords,
  driverLabel,
  endsLine,
  measureWords,
  signed,
  teamLine,
  type Driver,
} from "./waterfall.ts";

const driver = (kind: string, p85: number, p50: number, extra: Partial<Driver> = {}): Driver => ({
  kind,
  group: null,
  working_days: p85,
  p50,
  p85,
  items: [],
  more: 0,
  note: "",
  ...extra,
});

const DRIVERS = [
  driver("team_speed", -78, -40, { note: "1 requirement finished" }),
  driver("scope_added", 24, 12, { note: "1 change request, 1 without the CR label" }),
];

test("the causes are bars from zero, latest-pushing first, sized by the largest", () => {
  const bars = causeBars(DRIVERS, "p85");

  assert.deepEqual(
    bars.map((bar) => [bar.label, bar.value, bar.later, Math.round(bar.size * 100) / 100]),
    [
      ["Scope added", 24, true, 0.31],
      ["Team speed", -78, false, 1],
    ],
  );
  assert.equal(bars[0].note, "1 change request, 1 without the CR label");
  // The likely (50%) date reads the same causes by their own working days.
  assert.deepEqual(
    causeBars(DRIVERS, "p50").map((bar) => bar.value),
    [12, -40],
  );
});

test("a cause of no working days stays only when it lists requirements", () => {
  const bars = causeBars(
    [
      driver("scope_removed", 0, 0),
      driver("scope_added", 0, 0, {
        items: [
          {
            key: "CHK-9",
            title: "Card art",
            type: "Story",
            working_days: 0,
            day: null,
            group: "no_label",
            change_label: false,
            added_by: null,
          },
        ],
      }),
    ],
    "p85",
  );
  assert.deepEqual(
    bars.map((bar) => bar.label),
    ["Scope added"],
  );
});

test("each cause, date and day count is said in a leader's words", () => {
  assert.equal(driverLabel({ kind: "scope_added" }), "Scope added");
  assert.equal(driverLabel({ kind: "scope_removed" }), "Scope removed");
  assert.equal(driverLabel({ kind: "team_speed" }), "Team speed");
  assert.equal(driverLabel({ kind: "something_new" }), "Other");
  assert.equal(measureWords("p85"), "At the latest likely");
  assert.equal(measureWords("p50"), "Likely");
  assert.equal(daysWords(24), "+24 days");
  assert.equal(daysWords(-1), "−1 day");
  assert.equal(signed(0), "0");
  assert.equal(signed(-2.4), "−2.4");
});

const impact = {
  start: {
    day: "2026-10-01",
    p50: "2026-11-10",
    p85: "2026-11-18",
    committed: null,
    verdict: null,
  },
  end: { day: "2026-10-12", p50: "2026-11-20", p85: "2026-12-02", committed: null, verdict: null },
} as Pick<DeliveryImpactResponse, "start" | "end"> as DeliveryImpactResponse;

test("the likely date at both ends, the committed date and the team's date are short lines", () => {
  assert.equal(endsLine(impact, "p85"), "18 Nov then, 2 Dec now");
  assert.equal(endsLine(impact, "p50"), "10 Nov then, 20 Nov now");
  assert.equal(
    committedLine({
      from_date: "2026-11-04",
      to_date: "2026-11-18",
      on: "2026-10-09",
      by: "Mina Patel",
      note: "two changes approved",
      after_added: ["CHK-20", "CHK-21"],
    }),
    "4 Nov → 18 Nov, by Mina Patel on 9 Oct",
  );
  assert.equal(
    teamLine({
      from_date: "2026-10-14",
      from_key: "CHK-12",
      to_date: "2026-10-30",
      to_key: "CHK-20",
      moves: [],
    }),
    "14 Oct → 30 Oct (CHK-20)",
  );
  assert.equal(
    teamLine({
      from_date: "2026-12-04",
      from_key: "CHK-103",
      to_date: "2026-12-04",
      to_key: "CHK-103",
      moves: [],
    }),
    "4 Dec (CHK-103), unchanged",
  );
});

test("Overall's order lives in one place, and a section joins by its key", () => {
  assert.deepEqual(OVERALL_ORDER.slice(0, 3), ["answer", "timeline", "baseline"]);
  assert.ok(OVERALL_ORDER.indexOf("hygiene") > OVERALL_ORDER.indexOf("readiness"));
  assert.ok(OVERALL_ORDER.indexOf("hygiene") < OVERALL_ORDER.indexOf("risks"));
  const drawnKeys = overallSections({
    questions: "q",
    answer: "a",
    timeline: null,
    gates: "g",
    risks: false,
  }).map((section) => section.key);
  assert.deepEqual(drawnKeys, ["answer", "gates", "questions"]);
});
