import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDay } from "../../lib/format.ts";
import {
  flowDrawable,
  flowFinding,
  flowGeometry,
  flowSpoken,
  historyDays,
  shortHistoryNote,
  splitFinding,
} from "./flow.ts";

const day = (offset: number) => new Date(Date.UTC(2026, 8, 10 + offset)).toISOString().slice(0, 10);

/** 30 days from Thu 10 Sep: in development widens 3 → 6 while production grows 0 → 5. */
function month() {
  return Array.from({ length: 30 }, (_, i) => {
    const t = i / 29;
    return {
      day: day(i),
      counts: {
        raised: Math.round(9 - 3 * t),
        groomed: Math.round(3 - 3 * t),
        in_development: Math.round(3 + 3 * t),
        in_testing: i < 20 ? 1 : 0,
        business_testing: i === 29 ? 1 : 0,
        production: Math.round(5 * t),
      },
    };
  });
}

test("the flow draws from ten working days of snapshots; before that it says how many", () => {
  const three = month().slice(27);
  // Wed 7, Thu 8 and Fri 9 Oct: two working days with the day before kept, as the
  // forecast counts them. A weekend snapshot does not count; Monday after it does.
  assert.equal(historyDays(three), 2);
  assert.equal(historyDays([...three, { day: "2026-10-10", counts: {} }]), 2);
  assert.equal(historyDays([...three, { day: "2026-10-12", counts: {} }]), 3);
  assert.equal(flowDrawable(three, 10), false);
  assert.equal(flowDrawable(month(), 10), true);
  assert.equal(
    shortHistoryNote(
      [
        { day: "2026-10-07", counts: {} },
        { day: "2026-10-08", counts: {} },
        { day: "2026-10-09", counts: {} },
      ],
      10,
    ),
    "Not enough history for the flow yet: 2 of 10 working days. Until then one bar shows today's split.",
  );
});

test("the flow waits for the tenant's minimum, the one the forecast waits for", () => {
  const days = historyDays(month());
  assert.equal(flowDrawable(month(), days), true);
  assert.equal(flowDrawable(month(), days + 1), false);
  assert.equal(flowDrawable(month().slice(27), 2), true, "a minimum of two is met by Wed to Fri");
  assert.equal(
    shortHistoryNote(month(), 25),
    `Not enough history for the flow yet: ${days} of 25 working days. Until then one bar shows today's split.`,
  );
  assert.match(shortHistoryNote(month().slice(27), 5), /: 2 of 5 working days\./);
});

test("today's split says where most of the open work is", () => {
  assert.equal(
    splitFinding({ raised: 6, in_development: 6, business_testing: 1, production: 5 }),
    "6 raised and 6 in development: 12 of the 13 open requirements.",
  );
  assert.equal(splitFinding({ raised: 8 }), "All 8 open requirements are raised.");
  assert.equal(
    splitFinding({ raised: 2, in_development: 7, in_testing: 1, production: 3 }),
    "7 of the 10 open requirements are in development.",
  );
  assert.equal(splitFinding({ production: 4 }), "All 4 requirements are in production.");
});

test("the finding names the stage that widened most and how production moved", () => {
  assert.equal(
    flowFinding(month()).text,
    "In development widened from 3 to 6 in 30 days, while production grew from 0 to 5.",
  );
  const still = month().map((p) => ({ ...p, counts: { raised: 4, production: 2 } }));
  assert.equal(
    flowFinding(still).text,
    "No stage widened in 30 days, and nothing more reached production.",
  );
});

test("the bands stack production at the bottom and the labels never overlap", () => {
  const g = flowGeometry(month());
  assert.deepEqual(
    g.bands.map((band) => band.stage),
    ["production", "business_testing", "in_testing", "in_development", "groomed", "raised"],
  );
  assert.equal(g.edges.length, 5);
  // Today's non-empty stages, top to bottom, with "was" lines for the two the finding names.
  assert.deepEqual(
    g.labels.map((label) => label.text),
    ["Raised 6", "In development 6", "Business testing 1", "Production 5"],
  );
  assert.deepEqual(
    g.labels.filter((label) => label.was).map((label) => label.stage),
    ["in_development", "production"],
  );
  for (let i = 1; i < g.labels.length; i++) {
    assert.ok(g.labels[i].y - g.labels[i - 1].y >= (g.labels[i - 1].was ? 28 : 15));
  }
  assert.deepEqual(
    g.yTicks.map((tick) => tick.value),
    [0, 5, 10, 15, 20],
  );
  assert.equal(g.callout?.text, "In development widened: 3 → 6");
});

test("the chart's label says its finding, the scope and today's counts", () => {
  assert.equal(
    flowSpoken(month()),
    `Cumulative flow over 30 days, ${formatDay("2026-09-10")} to today. In development widened from 3 to 6 in 30 days, while production grew from 0 to 5. Scope grew from 16 to 18. Today: Raised 6, In development 6, Business testing 1, Production 5.`,
  );
});

test("a tie for the widest stage goes to the one holding most today", () => {
  const timeline = [
    { day: "2026-10-01", counts: { in_development: 1, in_testing: 0, business_testing: 0 } },
    { day: "2026-10-09", counts: { in_development: 2, in_testing: 1, business_testing: 1 } },
  ];
  assert.equal(flowFinding(timeline).widest?.stage, "in_development");
});
