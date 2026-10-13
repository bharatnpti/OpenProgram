import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryImpactResponse } from "../../api/schema";

import { OVERALL_ORDER, overallSections } from "./sections.ts";
import {
  barNote,
  committedLine,
  driverLabel,
  endsLine,
  lateLine,
  movedWords,
  percentOf,
  shadedPeriod,
  signed,
  teamLine,
  waterfall,
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
  driver("pace", 3, 1, { note: "0 finished" }),
  driver("scope_added", 6, 4, { group: "no_label" }),
  driver("throughput", -1, 0.5),
];

test("the bars run worst first, each from where the one before ended, to the move", () => {
  const falls = waterfall(DRIVERS, "p85", "CR");

  assert.deepEqual(
    falls.bars.map((bar) => [bar.label, bar.from, bar.to]),
    [
      ["Scope added: without the CR label", 0, 6],
      ["Pace", 6, 9],
      ["Throughput", 9, 8],
    ],
  );
  assert.equal(falls.total, 8);
  assert.equal(falls.bars[1].note, "0 finished");
  // A note that only repeats the bar's name is left out.
  assert.equal(
    waterfall([driver("rounding", 1, 1, { note: "replay rounding" })], "p85", "CR").bars[0].note,
    "",
  );
  assert.equal(falls.max, 9);
  assert.equal(falls.min, 0);
  assert.equal(percentOf(falls, 9), 100);
});

test("a bar's note never says the bar's name twice", () => {
  assert.equal(barNote("Replay rounding", "replay rounding"), "");
  assert.equal(
    barNote(
      "Throughput",
      "throughput (5 days joined the window with 1 finished; 3 days left it with 0)",
    ),
    "5 days joined the window with 1 finished; 3 days left it with 0",
  );
  assert.equal(barNote("Throughput", "throughput"), "");
  assert.equal(barNote("Pace", "0 finished"), "0 finished");
  // Brackets that do not follow the name are the note's own.
  assert.equal(barNote("Pace", "slower finishing (CHK-4 late)"), "slower finishing (CHK-4 late)");
  // A note that ends with what the name says keeps the count before it.
  assert.equal(
    barNote("Scope added: without the CR label", "2 requirements added, without the CR label"),
    "2 requirements added",
  );
  assert.equal(
    barNote("Scope added: change requests", "1 requirement added, change request"),
    "1 requirement added",
  );
  assert.equal(
    barNote("Scope added: change requests", "1 requirement added as change requests"),
    "1 requirement added",
  );
  // One shared word is not the name said twice.
  assert.equal(barNote("Scope removed", "1 requirement removed"), "1 requirement removed");
  assert.equal(barNote("Pace", "3 finished; CHK-102 late"), "3 finished; CHK-102 late");
});

test("the 50% date reorders the same drivers by their own working days", () => {
  const falls = waterfall(DRIVERS, "p50", "CR");

  assert.deepEqual(
    falls.bars.map((bar) => bar.driver.kind),
    ["scope_added", "pace", "throughput"],
  );
  assert.equal(falls.total, 5.5);
});

test("a driver of no working days stays only when it lists requirements", () => {
  const falls = waterfall(
    [
      driver("rounding", 0, 0),
      driver("scope_removed", 0, 0, {
        items: [
          {
            key: "CHK-7",
            title: "Old flow",
            type: "Story",
            working_days: 0,
            day: "2026-10-07",
            group: null,
            change_label: null,
            added_by: null,
          },
        ],
      }),
    ],
    "p85",
    "CR",
  );

  assert.deepEqual(
    falls.bars.map((bar) => bar.driver.kind),
    ["scope_removed"],
  );
});

test("each driver is named in a leader's words", () => {
  assert.equal(
    driverLabel({ kind: "scope_added", group: "change_request" }, "CR"),
    "Scope added: change requests",
  );
  assert.equal(driverLabel({ kind: "scope_added", group: "bug" }, "CR"), "Scope added: bugs");
  assert.equal(driverLabel({ kind: "re_estimated", group: null }, "CR"), "Re-estimated");
  assert.equal(
    driverLabel({ kind: "not_attributed", group: null }, "CR"),
    "Days with no breakdown kept",
  );
  assert.equal(signed(3), "+3");
  assert.equal(signed(-2.5), "−2.5");
  assert.equal(signed(0.01), "0");
  assert.equal(movedWords(8), "Moved 8 working days later");
  assert.equal(movedWords(-1), "Moved 1 working day earlier");
  assert.equal(movedWords(0.2), "Did not move");
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

test("the ends, the shaded period and the two date lanes read as lines", () => {
  assert.equal(endsLine(impact, "p85"), "18 Nov → 2 Dec");
  assert.equal(endsLine(impact, "p50"), "10 Nov → 20 Nov");
  assert.deepEqual(shadedPeriod(impact), { from: "2026-10-01", to: "2026-10-12" });
  assert.equal(shadedPeriod(undefined), null);
  assert.equal(
    committedLine({
      from_date: "2026-11-04",
      to_date: "2026-11-18",
      on: "2026-10-09",
      by: "Mina Patel",
      note: "two changes approved",
      after_added: ["CHK-20", "CHK-21"],
    }),
    "4 Nov → 18 Nov (Mina Patel, 9 Oct: “two changes approved”), after CHK-20, CHK-21 were added",
  );
  assert.equal(
    teamLine({
      from_date: "2026-10-14",
      from_key: "CHK-12",
      to_date: "2026-10-30",
      to_key: "CHK-20",
      moves: [],
    }),
    "CHK-12, 14 Oct → CHK-20, 30 Oct",
  );
  assert.equal(
    lateLine(
      {
        key: "CHK-4",
        title: "Card vault",
        stage: "in_testing",
        date: "2026-10-02",
        date_kind: "due",
        working_days_late: 6,
      },
      () => "In testing",
    ),
    "CHK-4, due 2 Oct, 6 working days late, In testing",
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
