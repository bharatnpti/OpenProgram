import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  REPLY_WORDS,
  boardMeta,
  boardRag,
  boardWord,
  ownerSourceWords,
  repliedCount,
  repliedWithoutStatus,
  statedSourceWords,
  type BoardRead,
} from "./checkinWords.ts";

const TODAY = "2026-10-07";
const say = { day: (iso: string) => `day ${iso}` };
const NON_STATUS = "Replied without a status update. Current status is unknown.";

const person = (over: Partial<BoardRead>): BoardRead => ({
  state: "confirmed",
  source: "confirmed",
  status_as_of: TODAY,
  summary: "Merged the handler",
  ...over,
});

describe("one word for each way a check-in can stand", () => {
  test("a reply is 'replied', whoever pressed what: the board cannot tell chat from the console", () => {
    // Zoe answered in chat and has not confirmed; Liam confirmed in the console. Both are 'replied' to a board.
    assert.deepEqual(boardWord(person({}), TODAY), { word: "replied", tone: "success" });
    assert.equal(boardWord(person({}), TODAY).word, REPLY_WORDS.replied);
    assert.notEqual(boardWord(person({}), TODAY).word, REPLY_WORDS.confirmed);
  });

  test("a part reply, silence, inference and an earlier day's status each have their own word", () => {
    assert.deepEqual(boardWord(person({ state: "partial", source: "partial" }), TODAY), {
      word: "partly replied",
      tone: "warning",
    });
    assert.deepEqual(
      boardWord(person({ state: "missing", source: "unknown", status_as_of: null }), TODAY),
      { word: "no reply", tone: "neutral" },
    );
    assert.deepEqual(boardWord(person({ state: "stale", source: "inferred" }), TODAY), {
      word: "inferred",
      tone: "warning",
    });
    assert.deepEqual(
      boardWord(person({ state: "stale", source: "confirmed", status_as_of: "2026-10-05" }), TODAY),
      { word: "carried forward", tone: "warning" },
    );
    assert.deepEqual(
      boardWord(person({ state: "stale", source: "unknown", status_as_of: TODAY }), TODAY),
      { word: "no reply", tone: "warning" },
    );
  });

  test("a reply that said nothing about the work is told apart from silence by the backend's summary", () => {
    const elena = person({
      state: "missing",
      source: "unknown",
      status_as_of: TODAY,
      summary: NON_STATUS,
    });
    assert.equal(repliedWithoutStatus(elena.summary, elena.source), true);
    assert.deepEqual(boardWord(elena, TODAY), {
      word: "replied without a status",
      tone: "warning",
    });
    assert.equal(boardMeta(elena, TODAY, say), "replied without a status");
    assert.equal(boardRag(elena), "amber");
    // The same state with the non-response wording is silence.
    const silent = person({
      state: "missing",
      source: "unknown",
      summary: "No confirmed check-in after a nudge. Current status is unknown.",
    });
    assert.equal(repliedWithoutStatus(silent.summary, silent.source), false);
    assert.equal(boardWord(silent, TODAY).word, "no reply");
    assert.equal(boardRag(silent), "unknown");
  });

  test("the marker counts only on a status that is unknown", () => {
    assert.equal(repliedWithoutStatus(NON_STATUS, "unknown"), true);
    assert.equal(repliedWithoutStatus(NON_STATUS, undefined), true);
    assert.equal(repliedWithoutStatus(NON_STATUS, "confirmed"), false);
    assert.equal(repliedWithoutStatus("Merged the handler", "unknown"), false);
    assert.equal(repliedWithoutStatus(null, "unknown"), false);
  });
});

describe("a board's row", () => {
  test("is green for a reply, grey for silence and amber for the rest", () => {
    assert.equal(boardRag(person({})), "green");
    assert.equal(boardRag(person({ state: "partial" })), "amber");
    assert.equal(boardRag(person({ state: "stale" })), "amber");
    assert.equal(boardRag(person({ state: "missing", source: "unknown" })), "unknown");
  });

  test("says today for today's reply, and names the day of an earlier one", () => {
    assert.equal(boardMeta(person({}), TODAY, say), "replied today · Merged the handler");
    assert.equal(
      boardMeta(person({ state: "partial", source: "partial" }), TODAY, say),
      "partly replied today · Merged the handler",
    );
    assert.equal(
      boardMeta(person({ state: "stale", status_as_of: "2026-10-02" }), TODAY, say),
      "last replied day 2026-10-02, nothing today · Merged the handler",
    );
    assert.equal(
      boardMeta(
        person({ state: "stale", source: "unknown", status_as_of: "2026-09-29", summary: "" }),
        TODAY,
        say,
      ),
      "no reply since day 2026-09-29",
    );
  });

  test("says what stands in for a reply, and that a new person has no status", () => {
    assert.equal(
      boardMeta(person({ state: "stale", source: "inferred", summary: "" }), TODAY, say),
      "no reply · inferred from delivery signals",
    );
    assert.equal(
      boardMeta(
        person({ state: "stale", source: "inferred", status_as_of: "2026-10-01", summary: "" }),
        TODAY,
        say,
      ),
      "inferred day 2026-10-01, nothing today",
    );
    assert.equal(
      boardMeta(
        person({
          state: "missing",
          source: "unknown",
          status_as_of: null,
          summary: "No check-in status is available.",
        }),
        TODAY,
        say,
      ),
      "no status yet",
    );
  });
});

describe("the count of who replied", () => {
  test("counts full replies, with the part ones beside it", () => {
    assert.equal(repliedCount({ confirmed: 5, partial: 0, total: 6 }), "5 of 6 replied");
    assert.equal(repliedCount({ confirmed: 4, partial: 1, total: 6 }), "4 of 6 replied · 1 partly");
    assert.equal(repliedCount({ confirmed: 0, partial: 0, total: 0 }), "0 of 0 replied");
  });
});

describe("what Signals says an owner has said", () => {
  test("a person's status source, in the board's words", () => {
    assert.equal(ownerSourceWords("confirmed"), "replied");
    assert.equal(ownerSourceWords("partial"), "partly replied");
    assert.equal(ownerSourceWords("inferred"), "inferred");
    assert.equal(ownerSourceWords("stale"), "carried forward");
    assert.equal(ownerSourceWords("unknown"), "no status");
    assert.equal(ownerSourceWords(null), "no status");
  });

  test("a drift finding says what the owner stated, never 'stated confirmed'", () => {
    assert.equal(statedSourceWords("confirmed"), "stated in a reply");
    assert.equal(statedSourceWords("partial"), "stated in a partial reply");
    assert.equal(statedSourceWords("inferred"), "inferred, not stated");
    assert.equal(statedSourceWords("unknown"), "no status stated");
  });
});
