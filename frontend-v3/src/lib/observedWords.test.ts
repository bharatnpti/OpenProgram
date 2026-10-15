import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { coverageParts, coverageWords, jiraTodayLine } from "./observedWords.ts";

const say = {
  today: "2026-10-06",
  day: (iso: string) => `day(${iso.slice(0, 10)})`,
  time: (iso: string) => iso.slice(11, 16),
  localDay: (iso: string) => iso.slice(0, 10),
};

describe("jiraTodayLine", () => {
  test("a move today reads by its time, with who moved it and the other changes", () => {
    assert.equal(
      jiraTodayLine(
        {
          status: "In Review",
          stage: "in_development",
          since: "2026-10-06T14:02:00Z",
          moved_by: "Liam",
          changes: ["due date moved Oct 10 → Oct 14"],
          quiet_days: null,
        },
        say,
      ),
      "Jira today: In Review since 14:02 · moved by Liam · due date moved Oct 10 → Oct 14",
    );
  });

  test("an earlier move reads by its day, and a quiet task says how long", () => {
    assert.equal(
      jiraTodayLine(
        {
          status: "In Progress",
          stage: "in_development",
          since: "2026-09-28T09:00:00Z",
          changes: [],
          quiet_days: 8,
        },
        say,
      ),
      "Jira today: In Progress since day(2026-09-28) · no movement for 8 days",
    );
  });

  test("nothing from the tracker is no line", () => {
    assert.equal(jiraTodayLine(null, say), null);
    assert.equal(jiraTodayLine({ status: null, stage: null, since: null, changes: [] }, say), null);
  });
});

describe("coverageWords", () => {
  test("names the one cause of no data", () => {
    assert.equal(
      coverageWords({
        replied: 6,
        inferred: 3,
        no_data: 1,
        waiting: 0,
        causes: [{ cause: "no_jira_link", count: 1 }],
      }),
      " · 3 from Jira · 1 no data (no Jira link)",
    );
  });

  test("counts several causes, and says nothing when every day was replied", () => {
    assert.equal(
      coverageWords({
        replied: 2,
        inferred: 0,
        no_data: 2,
        waiting: 1,
        causes: [
          { cause: "no_jira_link", count: 1 },
          { cause: "nothing_moved", count: 1 },
        ],
      }),
      " · 2 no data (1 no Jira link, 1 nothing moved in Jira)",
    );
    assert.equal(
      coverageWords({ replied: 4, inferred: 0, no_data: 0, waiting: 0, causes: [] }),
      "",
    );
    assert.equal(coverageWords(undefined), "");
  });
});

describe("coverageParts", () => {
  test("Directory puts a Jira link or chat id right; nothing moving it does not", () => {
    const base = { replied: 1, inferred: 2, waiting: 0 };
    assert.deepEqual(
      coverageParts({ ...base, no_data: 1, causes: [{ cause: "no_chat_id", count: 1 }] }),
      { fromJira: "2 from Jira", noData: "1 no data (no chat id)", fixedInDirectory: true },
    );
    assert.deepEqual(
      coverageParts({ ...base, no_data: 1, causes: [{ cause: "nothing_moved", count: 1 }] }),
      {
        fromJira: "2 from Jira",
        noData: "1 no data (nothing moved in Jira)",
        fixedInDirectory: false,
      },
    );
    assert.deepEqual(coverageParts(null), { fromJira: "", noData: "", fixedInDirectory: false });
  });
});
