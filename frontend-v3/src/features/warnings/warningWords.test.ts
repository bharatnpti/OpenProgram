import assert from "node:assert/strict";
import { test } from "node:test";

import type { PortfolioWarningsResponse, WarningGroupDto, WarningKind } from "../../api/schema";
import { ageWords, countLine, emptyWords, ownerWords, shownGroups } from "./warningWords.ts";

function group(kind: WarningKind, count: number): WarningGroupDto {
  return { kind, title: kind, count, items: [], counts_only: false };
}

test("the four main groups always, the others only with something open", () => {
  const view: PortfolioWarningsResponse = {
    as_of: "2026-06-15",
    seen_since: null,
    total: 3,
    projects: 2,
    groups: [
      group("blockers", 0),
      group("tools_disagree", 1),
      group("done_in_code", 0),
      group("review_stuck", 2),
      group("no_movement", 0),
      group("no_code_yet", 0),
    ],
  };
  assert.deepEqual(
    shownGroups(view).map((item) => item.kind),
    ["blockers", "tools_disagree", "done_in_code", "review_stuck"],
  );
  assert.equal(countLine(view), "3 open across 2 projects.");
  assert.equal(
    countLine({ total: 0, projects: 4 }),
    "Nothing open: Jira and Git agree with what was reported.",
  );
});

test("an age is real, today, or seen since the first recording", () => {
  assert.equal(ageWords({ age_days: 6, age_known: true, since: "2026-06-09" }, null), "6 days");
  assert.equal(ageWords({ age_days: 1, age_known: true, since: "2026-06-14" }, null), "1 day");
  assert.equal(ageWords({ age_days: 0, age_known: true, since: "2026-06-15" }, null), "today");
  assert.equal(
    ageWords({ age_days: 5, age_known: false, since: "2026-06-10" }, "2026-06-10"),
    "seen since Wed 10 Jun",
  );
  assert.equal(
    ageWords({ age_days: 0, age_known: false, since: "2026-06-15" }, null),
    "seen today",
  );
});

test("the owner as the reader may see them, never an id", () => {
  const item = { owner: "Sam Ortiz", owner_team: "Atlas Core", owner_is_you: false };
  assert.equal(ownerWords(item, "blockers"), "Resolves it: Sam Ortiz · Atlas Core");
  assert.equal(ownerWords({ ...item, owner: null }, "tools_disagree"), "Owner: Atlas Core");
  assert.equal(ownerWords({ ...item, owner_is_you: true }, "review_stuck"), "Owner: you");
  assert.equal(
    ownerWords({ owner: null, owner_team: null, owner_is_you: false }, "no_movement"),
    "No owner named yet",
  );
  assert.equal(emptyWords("done_in_code"), "Nothing merged is still open.");
});
