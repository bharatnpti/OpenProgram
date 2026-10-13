import assert from "node:assert/strict";
import { test } from "node:test";

import type { TimelineResponse } from "../../api/schema";

import {
  MIN_PX_PER_WEEK,
  axisLabels,
  dayNumber,
  drawn,
  foldReleases,
  glyphKind,
  glyphLabel,
  isoOf,
  itemNote,
  moveFocus,
  movesLabel,
  openingScroll,
  progressLabel,
  rowGlyphs,
  rowShape,
  scopeLabel,
  sentenceOf,
  tableRow,
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

test("the axis says month starts, and the 15th only when a fortnight has room", () => {
  const wide = axisLabels(timeScale(AXIS, 1000)).map((label) => label.label);
  const narrow = axisLabels(timeScale(AXIS, 200)).map((label) => label.label);

  assert.deepEqual(wide, ["1 Oct", "15 Oct", "1 Nov", "15 Nov", "1 Dec"]);
  assert.deepEqual(narrow, ["1 Oct", "1 Nov", "1 Dec"]);
  // A month label the day line's own label would touch gives way to it.
  const scale = timeScale(AXIS, 200);
  const near = axisLabels(scale, { x: xOf(scale, "2026-10-03"), label: "Today" });
  assert.deepEqual(
    near.map((label) => label.label),
    ["1 Nov", "1 Dec"],
  );
});

test("a row runs from planning to the committed date, filled for what is done", () => {
  const scale = timeScale(AXIS, 700);
  const shape = rowShape(row(), scale);

  assert.equal(shape.start, 0);
  assert.equal(shape.end, xOf(scale, "2026-11-18"));
  assert.equal(shape.open, false);
  assert.equal(shape.faded, false);
  assert.ok(shape.fill !== null && Math.abs(shape.fill - shape.end * (8 / 22)) < 0.5);
  assert.equal(shape.band?.tone, "warning"); // the 50% date meets it, the 85% does not
  assert.equal(shape.overhang, true);
  assert.deepEqual(
    shape.earlier.map((tick) => tick.date),
    ["2026-11-04"],
  );
  assert.deepEqual(shape.team, { x: xOf(scale, "2026-11-20"), hollow: true });
  assert.equal(shape.jira, null);
});

test("no committed date runs open to the edge; no baseline is faded; no band without a forecast", () => {
  const scale = timeScale(AXIS, 700);
  const shape = rowShape(
    row({
      baseline: null,
      committed: { date: null, history: [] },
      forecast: null,
      team: { date: null, key: null, undated: 3 },
    }),
    scale,
  );

  assert.equal(shape.open, true);
  assert.equal(shape.end, scale.width);
  assert.equal(shape.fill, null); // a fill of an open end would say nothing
  assert.equal(shape.faded, true);
  assert.equal(shape.band, null);
  assert.equal(shape.team, null);
});

test("a release's Jira date is a tick of its own when it is not the committed date", () => {
  const scale = timeScale(AXIS, 700);
  const release = row({ scope_kind: "release", jira_release_date: "2026-11-25" });

  assert.equal(rowShape(release, scale).jira, xOf(scale, "2026-11-25"));
  assert.equal(rowShape(row({ jira_release_date: "2026-11-18" }), scale).jira, null);
});

test("a row's labels: done of total with its share, short on a phone, the scope and moves", () => {
  assert.equal(progressLabel(row()), "8 of 22 · 36%");
  assert.equal(progressLabel(row(), true), "36%");
  assert.equal(
    progressLabel(row({ progress: { done: 13, total: 40, unit: "story points" } })),
    "13 of 40 pts · 33%",
  );
  assert.equal(progressLabel(row({ progress: null, access: "dates" })), "");
  assert.equal(scopeLabel(row()), "18 → 22 since planning");
  assert.equal(scopeLabel(row({ scope: null })), null);
  assert.equal(movesLabel(row()), "Moved once, +14 days");
  assert.equal(movesLabel(row({ committed: { date: "2026-11-18", history: [] } })), null);
  assert.deepEqual(verdictChip(null), { label: "No forecast kept", tone: "neutral" });
  assert.deepEqual(verdictChip("off_track"), { label: "Off track", tone: "danger" });
});

test("markers of one kind on one day are one glyph with a count, each with its own shape", () => {
  const scale = timeScale(AXIS, 900);
  const glyphs = rowGlyphs(
    row({
      markers: [
        marker("2026-10-06", "scope_added", [item("CHK-20", { change_label: true })], {
          change_label: true,
        }),
        marker(
          "2026-10-08",
          "scope_added",
          [item("CHK-21", { change_label: false, hygiene: true }), item("CHK-22")],
          { change_label: false },
        ),
        marker("2026-10-08", "bug", [item("CHK-30", { stage: null })]),
        marker("2026-10-09", "committed_date_changed", [], { count: 1 }),
      ],
    }),
    scale,
    "CR",
  );

  assert.deepEqual(
    glyphs.map((glyph) => [glyph.kind, glyph.count]),
    [
      ["added_label", 1],
      ["added", 2],
      ["bug", 1],
    ],
  );
  assert.equal(glyphs[1].ringed, true);
  assert.equal(glyphs[1].label, "8 Oct: 2 requirements added without the CR label");
  // Two glyphs on one day keep their room: the later one moves right.
  assert.ok(glyphs[2].x - glyphs[1].x >= 18);
});

test("on an axis longer than twelve weeks the markers merge per week", () => {
  const scale = timeScale({ start: "2026-08-01", end: "2026-12-31", today: "2026-10-12" }, 900);
  const glyphs = rowGlyphs(
    row({
      markers: [
        marker("2026-10-06", "late", [item("CHK-4")]),
        marker("2026-10-08", "late", [item("CHK-9")]),
      ],
    }),
    scale,
    "CR",
  );

  assert.equal(glyphs.length, 1);
  assert.equal(glyphs[0].count, 2);
  // On its week's first event, never ahead of what happened.
  assert.equal(glyphs[0].x, xOf(scale, "2026-10-06"));
  assert.equal(glyphs[0].label, "Week of 5 Oct: 2 requirements passed their date while open");
});

test("every glyph says what it marks, in words", () => {
  const words = (kind: Parameters<typeof glyphLabel>[0], count: number) =>
    glyphLabel(kind, count, "2026-10-09", false, "CR");

  assert.equal(words("added_label", 1), "9 Oct: 1 requirement added with the CR label");
  assert.equal(words("removed", 2), "9 Oct: 2 requirements left the scope");
  assert.equal(words("reopened", 1), "9 Oct: 1 requirement reopened");
  assert.equal(words("late", 1), "9 Oct: 1 requirement passed its date while open");
  assert.equal(words("bug", 3), "9 Oct: 3 bugs opened");
  assert.equal(words("blocker_opened", 1), "9 Oct: 1 blocker opened");
  assert.equal(words("blocker_closed", 2), "9 Oct: 2 blockers cleared");
  assert.equal(glyphKind({ kind: "committed_date_changed", change_label: null }), null);
});

test("a marker's item says its stage, label, who added it and what it waits on", () => {
  const stage = (value: string) => (value === "raised" ? "Raised" : value);

  assert.equal(
    itemNote(
      item("CHK-21", { change_label: false, hygiene: true, added_by: "Asha Rao" }),
      "added",
      "CR",
      stage,
    ),
    "Raised · no CR label · open in Jira hygiene · added by Asha Rao",
  );
  assert.equal(
    itemNote(item("CHK-2", { waits_on: "the sandbox keys" }), "blocker_opened", "CR", stage),
    "Raised · waits on: the sandbox keys",
  );
});

test("the legend names only what the rows draw", () => {
  const scale = timeScale(AXIS, 700);
  const dates = row({ access: "dates", progress: null, scope: null, markers: [] });
  const marks = drawn([dates], scale, "CR");

  assert.equal(marks.done, false); // a dates row has no progress
  assert.equal(marks.band, true);
  assert.deepEqual(marks.bandTones, ["warning"]);
  assert.equal(marks.glyphs.length, 0);
  assert.equal(marks.open, false);
});

test("the arrow keys move along a row's markers and between rows", () => {
  const counts = [2, 0, 3];

  assert.deepEqual(moveFocus({ row: 0, glyph: null }, "ArrowRight", counts), { row: 0, glyph: 0 });
  assert.deepEqual(moveFocus({ row: 0, glyph: 0 }, "ArrowRight", counts), { row: 0, glyph: 1 });
  assert.equal(moveFocus({ row: 0, glyph: 1 }, "ArrowRight", counts), null);
  assert.deepEqual(moveFocus({ row: 0, glyph: 0 }, "ArrowLeft", counts), { row: 0, glyph: null });
  assert.deepEqual(moveFocus({ row: 0, glyph: 1 }, "ArrowDown", counts), { row: 1, glyph: null });
  assert.deepEqual(moveFocus({ row: 2, glyph: 2 }, "ArrowUp", counts), { row: 1, glyph: null });
  assert.deepEqual(moveFocus({ row: 0, glyph: 1 }, "ArrowDown", [2, 1]), { row: 1, glyph: 0 });
  assert.deepEqual(moveFocus({ row: 2, glyph: null }, "End", counts), { row: 2, glyph: 2 });
  assert.equal(moveFocus({ row: 1, glyph: null }, "ArrowRight", counts), null);
  assert.equal(moveFocus({ row: 0, glyph: null }, "ArrowUp", counts), null);
  assert.equal(moveFocus({ row: 0, glyph: null }, "Enter", counts), null);
});

test("on a phone the box opens on today, never past either end", () => {
  // A month and a half before today, two months after: wider than a 240 px box.
  const scale = timeScale({ ...AXIS, start: "2026-09-01" }, 240);

  assert.equal(scale.width, 400);
  assert.equal(openingScroll(scale, "2026-10-12", 240), 104); // today a quarter in
  assert.equal(openingScroll(scale, "2026-09-02", 240), 0);
  assert.equal(openingScroll(scale, "2026-12-10", 240), 160); // never past the end
  assert.equal(openingScroll(timeScale(AXIS, 1000), "2026-10-12", 1000), 0);
});

test("on a phone the box opens on today and the nearest committed date ahead of it", () => {
  // 28 px a week from 5 Oct; a 204 px box shows about seven weeks.
  const scale = timeScale({ start: "2026-10-05", end: "2027-03-20", today: "2026-10-13" }, 204);

  // Today a quarter in already shows 20 Nov: it opens there, at the axis start.
  assert.equal(openingScroll(scale, "2026-10-13", 204, ["2026-12-15", "2026-11-20"]), 0);
  // 5 Dec would fall past the right edge: the box opens on further, 5 Dec just inside.
  const further = openingScroll(scale, "2026-10-27", 204, ["2026-12-05"]);
  assert.equal(further, 56);
  assert.ok(xOf(scale, "2026-12-05") - further <= 204 - 16);
  assert.ok(xOf(scale, "2026-10-27") - further >= 16);
  // Nothing ahead fits: today stays just inside the left edge, never scrolled past.
  const late = openingScroll(scale, "2026-11-02", 204, ["2027-02-01"]);
  assert.equal(late, xOf(scale, "2026-11-02") - 16);
  // A committed date already passed, or none: today a quarter in.
  assert.equal(
    openingScroll(scale, "2026-11-02", 204, ["2026-10-20", null]),
    Math.round(xOf(scale, "2026-11-02") - 51),
  );
});

test("the table lists the dates, the moves and the events by date", () => {
  const table = tableRow(
    row({
      markers: [
        marker("2026-10-09", "committed_date_changed", [], { count: 1 }),
        marker("2026-10-03", "late", [item("CHK-4")]),
      ],
    }),
    "CR",
  );

  assert.equal(table.start, "1 Oct (planning)");
  assert.equal(table.committed, "18 Nov");
  assert.equal(table.moves, "4 Nov → 18 Nov on 9 Oct");
  assert.equal(table.p85, "2 Dec");
  assert.equal(table.team, "20 Nov (CHK-12), 2 undated");
  assert.equal(table.done, "8 of 22 · 36%");
  assert.deepEqual(table.events, [
    "3 Oct: 1 requirement passed its date while open (CHK-4)",
    "9 Oct: committed date moved 4 Nov → 18 Nov",
  ]);
  const waiting = tableRow(
    row({ forecast: null, forecast_reason: "No forecast yet: 4 of 10 working days of history" }),
    "CR",
  );
  assert.equal(waiting.p85, "No forecast yet: 4 of 10 working days of history");
});

test("a row speaks the server's sentence", () => {
  const timeline: TimelineResponse = {
    axis: AXIS,
    rows: [row()],
    text: [
      {
        row: "project:project-checkout",
        scope_id: "project-checkout",
        project_id: "project-checkout",
        sentence: "Checkout Revamp: 8 of 22 done.",
      },
    ],
    change_label: "CR",
  };

  assert.equal(sentenceOf(timeline, timeline.rows[0]), "Checkout Revamp: 8 of 22 done.");
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
  // ?release= puts the release first: it heads the timeline, so nothing folds.
  assert.deepEqual(foldReleases([release, pod], false), { rows: [release, pod], releases: 0 });
});
