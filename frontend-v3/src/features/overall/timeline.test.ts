import assert from "node:assert/strict";
import { test } from "node:test";

import type { TimelineResponse } from "../../api/schema";

import {
  MIN_PX_PER_WEEK,
  axisLabels,
  changeEntry,
  changeList,
  dayNumber,
  drawn,
  foldReleases,
  isoOf,
  itemNote,
  likelyRange,
  moveFocus,
  openingScroll,
  rowDots,
  rowLine,
  rowShape,
  sentenceOf,
  tableRow,
  TEAM_DATE_MEANING,
  teamDateWords,
  timeScale,
  verdictChip,
  weekOf,
  xOf,
  type Marker,
  type Row,
} from "./timeline.ts";

type Item = Marker["items"][number];

const item = (key: string, extra: Partial<Item> = {}): Item => ({
  key,
  title: `${key} work`,
  stage: "raised",
  group: null,
  change_label: null,
  added_by: null,
  waits_on: null,
  hygiene: null,
  ...extra,
});

const marker = (day: string, kind: string, items: Item[], extra: Partial<Marker> = {}): Marker => ({
  day,
  kind,
  count: items.length || 1,
  items,
  change_label: null,
  ...extra,
});

function row(extra: Partial<Row> = {}): Row {
  return {
    row: "project:project-checkout",
    scope_kind: "project",
    scope_id: "project-checkout",
    project_id: "project-checkout",
    project_name: "Checkout Revamp",
    name: "Checkout Revamp",
    access: "panel",
    verdict: "at_risk",
    baseline: { day: "2026-10-01", reconstructed: false },
    start: "2026-10-01",
    committed: {
      date: "2026-11-18",
      history: [
        { date: "2026-11-04", on: "2026-09-20" },
        { date: "2026-11-18", on: "2026-10-09" },
      ],
    },
    jira_release_date: null,
    forecast: { p50: "2026-11-12", p85: "2026-12-02", on: "2026-10-12" },
    forecast_reason: null,
    team: { date: "2026-11-20", key: "CHK-12", undated: 2 },
    progress: { done: 8, total: 22, unit: "requirements" },
    scope: { at_baseline: 18, now: 22 },
    markers: [],
    reference_date: null,
    can_set_date: true,
    ...extra,
  };
}

const AXIS = { start: "2026-10-01", end: "2026-12-10", today: "2026-10-12" };

test("a calendar day is a day number and back, wherever the viewer is", () => {
  assert.equal(isoOf(dayNumber("2026-10-12")), "2026-10-12");
  assert.equal(dayNumber("2026-10-13") - dayNumber("2026-10-12"), 1);
  assert.equal(weekOf("2026-10-11"), "2026-10-05"); // a Sunday's week starts on Monday
  assert.equal(weekOf("2026-10-05"), "2026-10-05");
});

test("the axis fills the box, never narrower than 28 px a week, and scrolls past it", () => {
  const wide = timeScale(AXIS, 1000);
  const phone = timeScale(AXIS, 260);

  assert.equal(wide.width, 1000);
  assert.equal(phone.pxPerDay, MIN_PX_PER_WEEK / 7);
  assert.ok(phone.width > 260);
  assert.equal(xOf(wide, "2026-10-01"), 0);
  assert.equal(xOf(wide, "2030-01-01"), wide.width); // kept inside the drawing
  assert.equal(wide.weekly, false);
  assert.equal(timeScale({ ...AXIS, end: "2027-02-01" }, 800).weekly, true);
});

test("the axis says month starts only, and gives way to the day line's label", () => {
  const wide = axisLabels(timeScale(AXIS, 1000)).map((label) => label.label);
  assert.deepEqual(wide, ["1 Oct", "1 Nov", "1 Dec"]);
  const scale = timeScale(AXIS, 200);
  const near = axisLabels(scale, { x: xOf(scale, "2026-10-03"), label: "Today" });
  assert.deepEqual(
    near.map((label) => label.label),
    ["1 Nov", "1 Dec"],
  );
});

test("a row is a track from planning to its committed date, filled for what is done", () => {
  const scale = timeScale(AXIS, 700);
  const shape = rowShape(row(), scale, AXIS.today);

  assert.equal(shape.start, 0);
  assert.equal(shape.end, xOf(scale, "2026-11-18"));
  assert.equal(shape.open, false);
  assert.ok(shape.share !== null && Math.abs(shape.share - 8 / 22) < 1e-9);
  assert.ok(shape.fill !== null && Math.abs(shape.fill - (shape.end * 8) / 22) < 0.01);
  assert.equal(shape.committed, xOf(scale, "2026-11-18"));
});

test("the likely finish is one segment, said with its dates when it runs late", () => {
  const scale = timeScale(AXIS, 700);
  const late = rowShape(row(), scale, AXIS.today).likely;

  assert.ok(late);
  assert.equal(late.from, xOf(scale, "2026-11-12"));
  assert.equal(late.to, xOf(scale, "2026-12-02"));
  assert.equal(late.late, true); // 2 Dec is after the committed 18 Nov
  assert.equal(late.tone, "warning"); // the 50% date makes it, the 85% does not
  assert.equal(late.label, "likely 12 Nov – 2 Dec");
  const early = rowShape(
    row({ forecast: { p50: "2026-11-01", p85: "2026-11-10", on: "2026-10-12" } }),
    scale,
    AXIS.today,
  ).likely;
  assert.ok(early && !early.late && early.tone === "success");
  assert.equal(likelyRange("2026-11-10", "2026-11-10"), "likely 10 Nov");
  assert.equal(rowShape(row({ forecast: null }), scale, AXIS.today).likely, null);
});

test("no committed date runs the track to the likely finish, dashed", () => {
  const scale = timeScale(AXIS, 700);
  const open = rowShape(
    row({ committed: { date: null, history: [] }, verdict: "no_date" }),
    scale,
    AXIS.today,
  );
  assert.equal(open.open, true);
  assert.equal(open.committed, null);
  assert.equal(open.end, xOf(scale, "2026-12-02"));
});

test("a row says itself in one plain line, the team's own date apart and self-explaining", () => {
  // The team's 20 Nov is twelve days from the likely 2 Dec, and 2 have no date: said.
  assert.deepEqual(rowLine(row()), {
    parts: ["Likely by 2 Dec", "committed 18 Nov", "8 of 22 done"],
    team: "team's own date: 20 Nov · 2 have no date",
  });
  // Four days off with every requirement dated: not said.
  assert.equal(rowLine(row({ team: { date: "2026-11-28", key: "CHK-9", undated: 0 } })).team, null);
  // A week off and all dated: the date alone.
  assert.equal(
    rowLine(row({ team: { date: "2026-12-20", key: "CHK-9", undated: 0 } })).team,
    "team's own date: 20 Dec",
  );
  assert.deepEqual(
    rowLine(
      row({
        forecast: null,
        committed: { date: null, history: [] },
        progress: null,
        team: { date: null, key: null, undated: 3 },
      }),
    ),
    {
      parts: ["No likely date yet", "no committed date"],
      team: "team's own date: none given · 3 have no date",
    },
  );
  assert.equal(
    teamDateWords({ date: "2026-12-04", undated: 1 }),
    "team's own date: 4 Dec · 1 has no date",
  );
  assert.equal(teamDateWords({ date: null, undated: 0 }), "");
  assert.equal(
    TEAM_DATE_MEANING,
    "The latest finish date the team gave (ETA or due date) across the open requirements",
  );
  assert.equal(verdictChip(null).label, "No likely date");
  assert.equal(verdictChip("off_track").tone, "danger");
});

test("changes on one day are one dot with a count, red when one pushed the date", () => {
  const scale = timeScale(AXIS, 700);
  const dots = rowDots(
    row({
      markers: [
        marker("2026-10-09", "scope_added", [item("CHK-20"), item("CHK-21")], {
          change_label: false,
        }),
        marker("2026-10-09", "blocker_closed", [item("CHK-3")]),
        marker("2026-10-06", "scope_added", [item("CHK-19")], { change_label: true }),
      ],
    }),
    scale,
    "CR",
  );

  assert.equal(dots.length, 2);
  const [labelled, busy] = dots;
  assert.equal(labelled.pushed, false); // an agreed change, with the label
  assert.equal(busy.count, 3);
  assert.equal(busy.pushed, true);
  assert.equal(
    busy.label,
    "9 Oct: 2 requirements added without the CR label, 1 blocker cleared. Pushed the date.",
  );
  assert.ok(busy.x - labelled.x >= 16);
});

test("each change says itself, and which of them pushed the date", () => {
  const base = row({
    committed: {
      date: "2026-11-18",
      history: [
        { date: "2026-11-04", on: "2026-09-20" },
        { date: "2026-11-18", on: "2026-10-09" },
      ],
    },
  });
  const said = (kind: string, extra: Partial<Marker> = {}, count = 1) =>
    changeEntry(
      marker(
        "2026-10-09",
        kind,
        Array.from({ length: count }, (_, i) => item(`K-${i}`)),
        extra,
      ),
      base,
      "CR",
    );
  assert.deepEqual(
    [said("bug"), said("blocker_opened"), said("reopened"), said("late", {}, 2)].map((entry) => [
      entry.words,
      entry.pushed,
    ]),
    [
      ["1 bug opened", true],
      ["1 blocker opened", true],
      ["1 requirement reopened", true],
      ["2 requirements passed their date while open", false],
    ],
  );
  const moved = said("committed_date_changed");
  assert.equal(moved.words, "committed date moved 4 Nov → 18 Nov");
  assert.equal(moved.pushed, true);
  assert.equal(said("scope_removed").pushed, false);
});

test("on a long axis a row's changes merge per week", () => {
  const scale = timeScale({ ...AXIS, end: "2027-03-01" }, 600);
  const dots = rowDots(
    row({
      markers: [
        marker("2026-10-06", "bug", [item("CHK-1")]),
        marker("2026-10-08", "scope_removed", [item("CHK-2")]),
      ],
    }),
    scale,
    "CR",
  );
  assert.equal(dots.length, 1);
  assert.equal(dots[0].weekly, true);
  assert.ok(dots[0].label.startsWith("Week of 5 Oct: "));
  // One kind in one week is said once, its counts added.
  const twice = rowDots(
    row({
      markers: [
        marker("2026-10-06", "blocker_opened", [item("CHK-1")]),
        marker("2026-10-08", "blocker_opened", [item("CHK-2")]),
      ],
    }),
    scale,
    "CR",
  );
  assert.equal(twice[0].entries.length, 1);
  assert.equal(twice[0].label, "Week of 5 Oct: 2 blockers opened. Pushed the date.");
  assert.deepEqual(
    twice[0].entries[0].items.map((one) => one.key),
    ["CHK-1", "CHK-2"],
  );
});

test("what changed is a few lines a row, newest first, after the scope since planning", () => {
  const list = changeList(
    row({
      markers: [
        marker("2026-10-06", "scope_added", [item("CHK-19")], { change_label: true }),
        marker("2026-10-09", "scope_added", [item("CHK-20")], { change_label: false }),
        marker("2026-10-09", "scope_added", [item("CHK-21")], { change_label: true }),
        marker("2026-10-09", "bug", [item("CHK-22")]),
      ],
    }),
    "CR",
  );

  assert.equal(list.lead, "Since planning (1 Oct): 18 → 22 requirements");
  assert.deepEqual(list.lines, [
    {
      day: "2026-10-09",
      text: "9 Oct · 2 requirements added (1 without the CR label) · 1 bug opened",
      pushed: true,
    },
    { day: "2026-10-06", text: "6 Oct · 1 requirement added with the CR label", pushed: false },
  ]);
  const many = changeList(
    row({
      markers: [
        "2026-10-02",
        "2026-10-03",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-08",
      ].map((day) => marker(day, "bug", [item("CHK-9")])),
    }),
    "CR",
  );
  assert.equal(many.lines.length, 5);
  assert.equal(many.more, 1);
  assert.equal(many.lines[0].day, "2026-10-08");
  assert.equal(
    changeList(row({ baseline: null }), "CR").lead,
    "Planning is not marked complete yet",
  );
});

test("a change's item says its stage, label, who added it and what it waits on", () => {
  const words = (stage: string) => stage;
  assert.equal(
    itemNote(item("CHK-21", { added_by: "Asha Rao", hygiene: true }), "scope_added", "CR", words),
    "raised · no CR label · open in Jira hygiene · added by Asha Rao",
  );
  assert.equal(
    itemNote(item("CHK-3", { waits_on: "the sandbox keys" }), "blocker_opened", "CR", words),
    "raised · waits on: the sandbox keys",
  );
});

test("the legend names only what the rows draw, five at the most", () => {
  const scale = timeScale(AXIS, 700);
  const marks = drawn([row(), row({ forecast: null, markers: [] })], scale, AXIS.today);
  assert.deepEqual(marks, {
    done: true,
    toDo: true,
    likely: true,
    likelyTones: ["warning"],
    committed: true,
    change: false,
  });
});

test("the arrow keys move along a row's dots and between rows", () => {
  assert.deepEqual(moveFocus({ row: 0, glyph: null }, "ArrowRight", [2, 1]), { row: 0, glyph: 0 });
  assert.deepEqual(moveFocus({ row: 0, glyph: 1 }, "ArrowDown", [2, 1]), { row: 1, glyph: 0 });
  assert.deepEqual(moveFocus({ row: 0, glyph: 0 }, "ArrowLeft", [2, 1]), { row: 0, glyph: null });
  assert.equal(moveFocus({ row: 0, glyph: 1 }, "ArrowRight", [2, 1]), null);
  assert.deepEqual(moveFocus({ row: 1, glyph: null }, "End", [2, 3]), { row: 1, glyph: 2 });
});

test("on a phone the box opens on today, never past either end", () => {
  const scale = timeScale({ ...AXIS, start: "2026-09-01" }, 240);
  assert.equal(scale.width, 400);
  assert.equal(openingScroll(scale, "2026-10-12", 240), 104); // today a quarter in
  assert.equal(openingScroll(scale, "2026-09-02", 240), 0);
  assert.equal(openingScroll(scale, "2026-12-10", 240), 160); // never past the end
  assert.equal(openingScroll(timeScale(AXIS, 1000), "2026-10-12", 1000), 0);
});

test("on a phone the box opens on today and the nearest committed date ahead of it", () => {
  const scale = timeScale({ start: "2026-10-05", end: "2027-03-20", today: "2026-10-13" }, 204);
  assert.equal(openingScroll(scale, "2026-10-13", 204, ["2026-12-15", "2026-11-20"]), 0);
  const further = openingScroll(scale, "2026-10-27", 204, ["2026-12-05"]);
  assert.equal(further, 56);
  assert.ok(xOf(scale, "2026-12-05") - further <= 204 - 16);
  assert.ok(xOf(scale, "2026-10-27") - further >= 16);
});

test("the table lists the dates, the moves, the likely finish and what changed by date", () => {
  const listed = tableRow(
    row({
      markers: [marker("2026-10-09", "scope_added", [item("CHK-21")], { change_label: false })],
    }),
    "CR",
  );
  assert.equal(listed.start, "1 Oct (planning)");
  assert.equal(listed.committed, "18 Nov");
  assert.equal(listed.moves, "4 Nov → 18 Nov on 9 Oct");
  assert.equal(listed.likely, "12 Nov – 2 Dec");
  assert.equal(listed.team, "20 Nov (CHK-12) · 2 have no date");
  assert.equal(listed.done, "8 of 22 done");
  assert.deepEqual(listed.events, ["9 Oct: 1 requirement added without the CR label (CHK-21)"]);
  assert.equal(
    tableRow(row({ forecast: null, forecast_reason: "No likely date yet" }), "CR").likely,
    "No likely date yet",
  );
});

test("a row speaks the server's sentence", () => {
  const timeline: TimelineResponse = {
    axis: AXIS,
    change_label: "CR",
    rows: [row()],
    text: [
      {
        row: "project:project-checkout",
        scope_id: "project-checkout",
        project_id: "project-checkout",
        sentence: "Checkout Revamp: likely by 2 Dec.",
      },
    ],
  };
  assert.equal(sentenceOf(timeline, row()), "Checkout Revamp: likely by 2 Dec.");
  assert.equal(sentenceOf({ ...timeline, text: [] }, row()), "Checkout Revamp");
});

test("the project's releases sit folded under its row; a release the page picked is not", () => {
  const project = row();
  const release = row({ row: "release:rel-1", scope_kind: "release", scope_id: "rel-1" });
  const pod = row({ row: "pod:pod-payments", scope_kind: "pod", scope_id: "pod-payments" });

  const folded = foldReleases([project, release, pod], false);
  assert.deepEqual(
    folded.rows.map((one) => one.row),
    ["project:project-checkout", "pod:pod-payments"],
  );
  assert.equal(folded.releases, 1);
  assert.equal(foldReleases([project, release, pod], true).rows.length, 3);
  assert.deepEqual(foldReleases([release, pod], false), { rows: [release, pod], releases: 0 });
});

test("a day number survives the round trip", () => {
  assert.equal(isoOf(dayNumber("2027-01-01")), "2027-01-01");
});
