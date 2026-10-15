import assert from "node:assert/strict";
import { test } from "node:test";

import type { PortfolioWarningsResponse, WarningGroupDto, WarningKind } from "../../api/schema";
import {
  ageWords,
  blockersAboveWords,
  countLine,
  emptyWords,
  itemLine,
  ownerSays,
  ownerWords,
  podNames,
  shownGroups,
} from "./warningWords.ts";

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

test("a line says the item's name once, beside it", () => {
  assert.equal(
    itemLine({
      key: "3-D Secure step-up",
      line: "3-D Secure step-up has had no state change in 21 days.",
    }),
    "has had no state change in 21 days.",
  );
  assert.equal(
    itemLine({
      key: "Passkey enrolment",
      line: "Pull request 'IDP-301' has been open for 20 days.",
    }),
    "Pull request 'IDP-301' has been open for 20 days.",
  );
});

test("at a project's or a pod's level the count line leaves the projects out", () => {
  assert.equal(countLine({ total: 8, projects: 1 }, true), "8 open.");
  assert.equal(
    countLine({ total: 0, projects: 1 }, true),
    "Nothing open: Jira and Git agree with what was reported.",
  );
});

test("an item names its pods in a list", () => {
  const pod = (name: string) => ({ id: name.toLowerCase(), name });
  assert.equal(podNames({ pods: [] }), "");
  assert.equal(podNames({ pods: [pod("Atlas Core")] }), "Atlas Core");
  assert.equal(
    podNames({ pods: [pod("Atlas Core"), pod("Atlas Edge"), pod("Beacon")] }),
    "Atlas Core, Atlas Edge and Beacon",
  );
});

test("what the owner says folds under the item, only where the owner is named", () => {
  const says = {
    text: "On track, pairing on the last screen.",
    source: "confirmed",
    on: "2026-06-15",
  } as const;
  assert.deepEqual(ownerSays({ owner: "Ben Marsh", owner_is_you: false, owner_says: says }), {
    title: "What Ben says",
    text: "On track, pairing on the last screen.",
    meta: "in their check-in, Mon 15 Jun",
  });
  assert.equal(
    ownerSays({ owner: null, owner_is_you: true, owner_says: { ...says, source: "inferred" } })
      ?.title,
    "What you said",
  );
  assert.equal(
    ownerSays({ owner: null, owner_is_you: true, owner_says: { ...says, source: "inferred" } })
      ?.meta,
    "inferred from Jira and Git, not said, Mon 15 Jun",
  );
  // The team stands for the owner: none of their words.
  assert.equal(ownerSays({ owner: null, owner_is_you: false, owner_says: says }), null);
  assert.equal(ownerSays({ owner: "Ben Marsh", owner_is_you: false, owner_says: null }), null);
});

test("a pod's warnings say how many of them are the blockers its morning lists", () => {
  assert.equal(blockersAboveWords(1), "1 of them is the blocker above.");
  assert.equal(blockersAboveWords(4), "4 of them are the blockers above.");
});
