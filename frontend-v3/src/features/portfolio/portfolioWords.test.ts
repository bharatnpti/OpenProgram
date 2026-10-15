import assert from "node:assert/strict";
import { test } from "node:test";

import type { PortfolioProjectDto } from "../../api/schema";
import {
  asOfWords,
  barOf,
  barSummary,
  byProgramme,
  filtersOf,
  healthLine,
  lateWords,
  markWords,
  podDateWords,
  programmeLine,
  releaseChip,
  threeDates,
  toggledVerdict,
  verdictPill,
  verdictTiles,
} from "./portfolioWords.ts";

const counts = {
  on_track: 3,
  at_risk: 2,
  off_track: 1,
  done: 0,
  no_date: 4,
  not_enough_data: 0,
  none: 0,
};

function project(over: Partial<PortfolioProjectDto> = {}): PortfolioProjectDto {
  return {
    id: "p-atlas",
    name: "Atlas",
    program_id: "prog-north",
    program_name: "North Programme",
    verdict: "off_track",
    target: "2026-07-05",
    target_source: "committed",
    p50: "2026-07-15",
    p85: "2026-07-30",
    team_date: "2026-07-15",
    team_key: "ATL-2",
    days_late: 19,
    reason: "Off track because 3 requirements were added after planning (26 May).",
    health: "1 open blocker, 1 of 2 not replied",
    health_day: "2026-06-15",
    done: 2,
    total: 5,
    pods: [],
    releases: [],
    marks: [
      {
        day: "2026-06-13",
        kind: "scope_added",
        count: 2,
        working_days: 6,
        pushed: true,
        moved_to: null,
      },
      {
        day: "2026-06-07",
        kind: "committed_date_changed",
        count: 1,
        working_days: null,
        pushed: false,
        moved_to: "2026-07-05",
      },
    ],
    plan: null,
    start: "2026-05-26",
    kept_on: "2026-06-15",
    computed_at: "2026-06-15T08:30:00Z",
    reconstructed: false,
    ...over,
  };
}

test("the tiles are the four answers, each a filter, no committed date counted red", () => {
  const tiles = verdictTiles(counts);
  assert.deepEqual(
    tiles.map((tile) => [tile.filter, tile.label, tile.count, tile.tone]),
    [
      ["late", "Off track", 1, "danger"],
      ["at_risk", "At risk", 2, "warning"],
      ["no_date", "No committed date", 4, "danger"],
      ["on_track", "On track", 3, "success"],
    ],
  );
});

test("one pill: the date's verdict, or no forecast yet", () => {
  assert.deepEqual(verdictPill(project()), { label: "Off track", tone: "danger" });
  assert.deepEqual(verdictPill(project({ verdict: null })), {
    label: "No forecast yet",
    tone: "neutral",
  });
});

test("today's health is said, with the day when the rollup is older", () => {
  assert.equal(
    healthLine(project(), "2026-06-15", false),
    "Today: 1 open blocker, 1 of 2 not replied",
  );
  assert.equal(
    healthLine(project({ health_day: "2026-06-10" }), "2026-06-15", false),
    "On Wed 10 Jun: 1 open blocker, 1 of 2 not replied",
  );
  assert.equal(
    healthLine(project(), "2026-06-15", true),
    "That day: 1 open blocker, 1 of 2 not replied",
  );
});

test("the three dates and how late, in words", () => {
  assert.deepEqual(
    threeDates(project(), "2026-06-15").map((item) => [item.label, item.value]),
    [
      ["Committed", "Sun 5 Jul"],
      ["Likely (50–85%)", "Wed 15 Jul – Thu 30 Jul"],
      ["Team's date", "Wed 15 Jul"],
    ],
  );
  assert.equal(
    threeDates(project({ target: null, p50: null, p85: null }), "2026-06-15")[1].value,
    "Not forecast yet",
  );
  assert.equal(
    threeDates(project({ target_source: "jira_release" }), "2026-06-15")[0].label,
    "Jira release",
  );
  assert.equal(lateWords(project()), "85% date 19 working days after the committed date");
  assert.equal(
    lateWords(project({ days_late: -1 })),
    "85% date 1 working day before the committed date",
  );
  assert.equal(lateWords(project({ days_late: null })), null);
});

test("a date in another year says its year", () => {
  assert.equal(
    threeDates(project({ p50: "2027-01-26", p85: "2027-03-10" }), "2026-10-13")[1].value,
    "Tue 26 Jan 2027 – Wed 10 Mar 2027",
  );
  assert.deepEqual(verdictPill(project({ verdict: "no_date" })), {
    label: "No committed date",
    tone: "danger",
  });
});

test("a release chip says the first and how many more", () => {
  assert.equal(releaseChip([]), null);
  const release = { id: "r1", name: "R1", verdict: null, target: null, p85: null };
  assert.equal(releaseChip([release, { ...release, id: "r2", name: "R2" }]), "R1 +1");
});

test("each mark on the bar in words, with its working days", () => {
  const [added, moved] = project().marks;
  assert.equal(markWords(added), "Sat 13 Jun: 2 requirements added, +6 working days");
  assert.equal(markWords(moved), "Sun 7 Jun: committed date moved to Sun 5 Jul");
  assert.equal(
    markWords({ ...added, count: 1, working_days: null }),
    "Sat 13 Jun: 1 requirement added, days not known",
  );
  assert.match(asOfWords("2026-06-15T08:30:00Z"), /^Forecast as of Mon 15 Jun/);
});

test("the bar places every date between planning and the latest one", () => {
  const bar = barOf(project(), "2026-06-15");
  assert.equal(bar.start, "2026-05-26");
  assert.equal(bar.end, "2026-07-30");
  for (const value of [bar.today, bar.committed, bar.likelyFrom, bar.likelyTo, bar.team]) {
    assert.ok(value !== null && value >= 0 && value <= 1);
  }
  assert.ok(bar.committed! < bar.likelyTo!);
  assert.equal(bar.done, 0.4);
  assert.deepEqual(
    bar.marks.map((mark) => mark.pushed),
    [true, false],
  );
  assert.equal(barOf(project({ total: 0, done: 0 }), "2026-06-15").done, null);
  assert.equal(
    barSummary(project()),
    "Atlas: 2 of 5 requirements done, committed Sun 5 Jul, likely by Thu 30 Jul, 1 change pushed the date, 1 other change.",
  );
});

test("grouped by programme where its worst project first appears", () => {
  const north = project();
  const south = project({
    id: "p-comet",
    name: "Comet",
    program_id: "prog-south",
    program_name: "South",
  });
  const loose = project({ id: "p-x", name: "Loose", program_id: null, program_name: null });
  const groups = byProgramme(
    [loose, south, north, project({ id: "p-b", name: "Beacon" })],
    [{ id: "prog-north", name: "North Programme", counts }],
  );
  assert.deepEqual(
    groups.map((group) => [group.name, group.projects.map((item) => item.name)]),
    [
      ["South", ["Comet"]],
      ["North Programme", ["Atlas", "Beacon"]],
      ["In no programme", ["Loose"]],
    ],
  );
  assert.equal(groups[1].counts, counts);
  assert.equal(programmeLine(counts), "1 off track · 2 at risk · 4 no committed date · 3 on track");
  assert.equal(programmeLine(null), null);
});

test("the filters live in the URL and toggle one at a time", () => {
  const search = new URLSearchParams(
    "asOf=2026-06-10&verdict=late&verdict=bogus&q=atl&program=prog-north",
  );
  assert.deepEqual(filtersOf(search), { q: "atl", verdicts: ["late"], program: "prog-north" });
  const on = toggledVerdict(search, "at_risk");
  assert.deepEqual(on.getAll("verdict"), ["late", "bogus", "at_risk"]);
  assert.equal(on.get("asOf"), "2026-06-10");
  assert.deepEqual(toggledVerdict(on, "late").getAll("verdict"), ["bogus", "at_risk"]);
});

test("a pod with no requirements counted is nothing in scope, never done", () => {
  assert.equal(podDateWords({ total: 0, verdict: "done" }), "Nothing in scope");
  assert.equal(podDateWords({ total: 5, verdict: "at_risk" }), "At risk");
  assert.equal(podDateWords({ total: 5, verdict: null }), "No forecast yet");
});
