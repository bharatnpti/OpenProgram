import assert from "node:assert/strict";
import { test } from "node:test";

import type { DeliveryImpactResponse } from "../../api/schema";
import { chipLabel, chipWords, whatChanged } from "./whatChanged.ts";

type Marker = DeliveryImpactResponse["markers"][number];

const marker = (day: string, kind: string, count: number, extra: Partial<Marker> = {}): Marker => ({
  day,
  kind,
  count,
  items: Array.from({ length: Math.min(count, 10) }, (_, index) => ({
    key: `CHK-${100 + index}`,
    title: `Item ${index}`,
    stage: null,
    group: null,
    added_by: null,
  })),
  change_label: null,
  working_days: null,
  p50_days: null,
  p85_days: null,
  taken_apart: null,
  reconstructed: false,
  ...extra,
});

const days = (value: number, extra: Partial<Marker> = {}): Partial<Marker> => ({
  working_days: value,
  p85_days: value,
  p50_days: value / 2,
  taken_apart: true,
  ...extra,
});

const impact = (
  markers: Marker[],
  moved: number | null,
  team: number,
  extra: Partial<DeliveryImpactResponse> = {},
): DeliveryImpactResponse =>
  ({
    scope_kind: "project",
    scope_id: "project-checkout",
    project_id: "project-checkout",
    name: "Checkout Revamp",
    project_name: "Checkout Revamp",
    period: "since_baseline",
    period_words: "since planning (5 Oct)",
    measure: "p85",
    detail: "full",
    change_label: "CR",
    baseline_day: "2026-10-05",
    start: null,
    end: null,
    moved_working_days: moved,
    unit: "requirements",
    summary: "",
    drivers: [
      {
        kind: "team_speed",
        group: null,
        working_days: team,
        p50: team,
        p85: team,
        items: [],
        more: 0,
        note: "",
      },
    ],
    scope_changes: {
      added: [],
      removed: [],
      reopened: [],
      added_count: 0,
      removed_count: 0,
      reopened_count: 0,
      finished_count: 0,
    },
    evidence: null,
    committed_changes: [],
    team_date: null,
    markers,
    link: "/projects/project-checkout?row=project:project-checkout#timeline",
    reconstructed_days: 0,
    not_known: [],
    ...extra,
  }) as DeliveryImpactResponse;

test("scope events carry their working days; a day's additions are one line", () => {
  const read = impact(
    [
      marker("2026-10-07", "scope_added", 2, { change_label: true, ...days(4) }),
      marker("2026-10-07", "scope_added", 1, { change_label: false, ...days(2) }),
      marker("2026-10-07", "bug", 1, days(2)),
      marker("2026-10-09", "scope_removed", 1, days(-3)),
    ],
    9,
    4,
  );
  const { lines } = whatChanged(read);
  assert.deepEqual(
    lines.map((line) => [
      line.dayLabel,
      line.text,
      line.chip?.kind === "days" ? line.chip.text : null,
    ]),
    [
      ["7 Oct", "3 requirements added, 1 without the CR label", "+6 working days"],
      ["7 Oct", "1 bug opened", "+2 working days"],
      ["9 Oct", "1 requirement removed", "−3 working days"],
    ],
  );
  assert.deepEqual(
    lines.map((line) => line.pushed),
    [true, true, false],
  );
  // Each key once, the rest counted.
  assert.equal(lines[0].keys, "CHK-100, CHK-101 and 1 more");
});

test("evidence has no chip, and a committed date's move is said once, under Why it moved", () => {
  const read = impact(
    [
      marker("2026-10-08", "blocker_opened", 1),
      marker("2026-10-08", "late", 2),
      marker("2026-10-08", "committed_date_changed", 1),
      marker("2026-10-10", "blocker_closed", 1),
    ],
    3,
    3,
  );
  const { lines, footer } = whatChanged(read);
  assert.deepEqual(
    lines.map((line) => [line.text, line.chip]),
    [
      ["1 blocker opened", null],
      ["2 requirements passed their date while open", null],
      ["1 blocker cleared", null],
    ],
  );
  assert.equal(footer?.adds, true, "team speed alone is the move");
});

test("the footer checks the chips and team speed against the waterfall's total", () => {
  const adds = whatChanged(
    impact(
      [
        marker("2026-10-07", "scope_added", 2, { change_label: false, ...days(10) }),
        marker("2026-10-09", "scope_removed", 1, days(-4)),
      ],
      15,
      9,
    ),
  ).footer;
  assert.ok(adds);
  assert.equal(adds.adds, true);
  assert.equal(adds.events, 6);
  assert.equal(
    adds.text,
    "Scope events +6 · team speed +9 = moved 15 working days later on the latest likely date",
  );
  // Re-estimates join a bar but no event: the footer names what the chips leave out.
  const short = whatChanged(
    impact([marker("2026-10-07", "scope_added", 1, { change_label: false, ...days(5) })], 12, 4),
  ).footer;
  assert.equal(short?.adds, false);
  assert.equal(short?.rest, 3);
  assert.match(short?.text ?? "", /re-estimated requirements \+3 = moved 12 working days later/);
  assert.match(
    whatChanged(impact([marker("2026-10-07", "scope_removed", 1, days(-2))], -80, -78)).footer
      ?.text ?? "",
    /= moved 80 working days earlier on the latest likely date$/,
  );
  // Rounding alone is not a gap.
  assert.equal(
    whatChanged(impact([marker("2026-10-07", "reopened", 1, days(1.98))], 4, 2)).footer?.adds,
    true,
  );
  // No move to take apart: the lines without a footer.
  assert.equal(whatChanged(impact([], null, 0)).footer, null);
});

test("a day that kept no breakdown is not known, and a rebuilt day is marked", () => {
  const { lines, footer } = whatChanged(
    impact(
      [
        marker("2026-10-07", "scope_added", 1, { change_label: false, taken_apart: false }),
        marker("2026-10-08", "scope_added", 1, {
          change_label: true,
          ...days(2, { reconstructed: true }),
        }),
        // A bug the forecast does not count: an event, with no chip at all.
        marker("2026-10-09", "bug", 1, { taken_apart: null }),
      ],
      5,
      3,
      { reconstructed_days: 1 },
    ),
  );
  assert.deepEqual(lines[0].chip, { kind: "unknown", text: "not known" });
  assert.equal(lines[1].chip?.kind === "days" && lines[1].chip.reconstructed, true);
  assert.equal(lines[2].chip, null);
  assert.deepEqual(footer?.notes, [
    "1 event fell on a day that kept no breakdown, so its working days are not known.",
    "1 day of this period was rebuilt afterwards, not kept on the day: its chip is marked reconstructed.",
  ]);
});

test("chips say working days, signed, and read aloud in words", () => {
  assert.equal(chipWords(11), "+11 working days");
  assert.equal(chipWords(-1), "−1 working day");
  assert.equal(chipWords(0.04), "0 working days");
  assert.equal(chipWords(2.46), "+2.5 working days");
  assert.equal(
    chipLabel({ kind: "days", value: -6, text: chipWords(-6), reconstructed: true }),
    "minus 6 working days, reconstructed",
  );
  assert.equal(chipLabel({ kind: "unknown", text: "not known" }), "working days not known");
});
