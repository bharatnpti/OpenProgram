import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  blockerKey,
  blockerRows,
  buildCorrection,
  type BlockerRow,
  checkinHint,
  checkinNote,
  checkinProvenance,
  checkinState,
  confirmCaption,
  isOwnWords,
  MAX_BLOCKER_TEXT,
  isNoReplyPlaceholder,
  NO_REPLY_BLOCKER,
  noStatusWords,
  whyCheckinMatters,
} from "./checkin.ts";

const say = {
  day: (iso: string) => `day ${iso}`,
  time: (iso: string) => `time ${iso}`,
};
const TODAY = "2026-10-06";

describe("where a check-in came from", () => {
  test("an earlier day's status is carried forward, whatever its source", () => {
    // The seeded demo: today is 6 Oct and the latest row is from 29 Sep.
    const carried = checkinProvenance({
      source: "unknown",
      statusAsOf: "2026-09-29",
      today: TODAY,
      developerConfirmed: false,
    });
    assert.deepEqual(carried, {
      kind: "carried",
      from: "2026-09-29",
      confirmedToday: false,
      state: "missing",
    });
    // A status confirmed on the earlier day is not confirmed today: no green tick.
    const confirmedThen = checkinProvenance({
      source: "confirmed",
      statusAsOf: "2026-10-05",
      today: TODAY,
      developerConfirmed: true,
    });
    assert.equal(confirmedThen.kind, "carried");
    assert.equal(confirmedThen.confirmedToday, false);
    assert.equal(confirmedThen.state, "stale");
  });

  test("today's status is confirmed only when the person confirmed it here", () => {
    const here = checkinProvenance({
      source: "confirmed",
      statusAsOf: TODAY,
      today: TODAY,
      developerConfirmed: true,
    });
    assert.equal(here.kind, "today");
    assert.equal(here.confirmedToday, true);
    // Answered in chat: confirmed to the pod, but the console still offers Confirm.
    const chat = checkinProvenance({
      source: "confirmed",
      statusAsOf: TODAY,
      today: TODAY,
      developerConfirmed: false,
    });
    assert.equal(chat.confirmedToday, false);
    assert.equal(chat.state, "confirmed");
  });

  test("nothing on record is none, never carried", () => {
    assert.deepEqual(
      checkinProvenance({
        source: undefined,
        statusAsOf: null,
        today: TODAY,
        developerConfirmed: false,
      }),
      { kind: "none", from: null, confirmedToday: false, state: "missing" },
    );
  });

  test("the pod board's state: only confirmed or partial dated today counts", () => {
    assert.equal(checkinState("confirmed", TODAY, TODAY), "confirmed");
    assert.equal(checkinState("partial", TODAY, TODAY), "partial");
    assert.equal(checkinState("inferred", TODAY, TODAY), "stale");
    assert.equal(checkinState("stale", TODAY, TODAY), "stale");
    assert.equal(checkinState("confirmed", "2026-10-05", TODAY), "stale");
    assert.equal(checkinState("unknown", TODAY, TODAY), "missing");
    assert.equal(checkinState("confirmed", null, TODAY), "missing");
  });
});

describe("what the card says", () => {
  const note = (
    input: Parameters<typeof checkinProvenance>[0] & {
      confirmedAt?: string | null;
      summary?: string;
    },
  ) => {
    const provenance = checkinProvenance(input);
    return checkinNote(
      provenance,
      {
        source: input.source,
        confirmedAt: input.confirmedAt ?? null,
        developerConfirmed: input.developerConfirmed,
        summary: input.summary,
      },
      say,
    );
  };

  test("a carried-forward status names its day and says nobody replied today", () => {
    assert.equal(
      note({
        source: "unknown",
        statusAsOf: "2026-09-29",
        today: TODAY,
        developerConfirmed: false,
      }),
      "carried forward from day 2026-09-29 · no reply today",
    );
  });

  test("today's status says how it was given: replied, or confirmed", () => {
    assert.equal(
      note({
        source: "confirmed",
        statusAsOf: TODAY,
        today: TODAY,
        developerConfirmed: true,
        confirmedAt: "T1",
      }),
      "confirmed by you · time T1",
    );
    // Zoe: her chat reply is the day's status, and she has not confirmed it in the console.
    assert.equal(
      note({
        source: "confirmed",
        statusAsOf: TODAY,
        today: TODAY,
        developerConfirmed: false,
        confirmedAt: "T0",
      }),
      "replied in chat, not confirmed · time T0",
    );
    assert.equal(
      note({ source: "partial", statusAsOf: TODAY, today: TODAY, developerConfirmed: false }),
      "partly replied in chat",
    );
    assert.equal(
      note({ source: "inferred", statusAsOf: TODAY, today: TODAY, developerConfirmed: false }),
      "no reply today · inferred from delivery signals",
    );
    assert.equal(
      note({ source: "unknown", statusAsOf: TODAY, today: TODAY, developerConfirmed: false }),
      "no reply today",
    );
    assert.equal(
      note({ source: undefined, statusAsOf: null, today: TODAY, developerConfirmed: false }),
      "no check-in on record",
    );
  });

  test("a card for a past day says that day, not today", () => {
    const past = { ...say, today: "on Mon 5 Oct" };
    const provenance = checkinProvenance({
      source: "inferred",
      statusAsOf: TODAY,
      today: TODAY,
      developerConfirmed: false,
    });
    assert.equal(
      checkinNote(
        provenance,
        { source: "inferred", confirmedAt: null, developerConfirmed: false },
        past,
      ),
      "no reply on Mon 5 Oct · inferred from delivery signals",
    );
    const carried = checkinProvenance({
      source: "unknown",
      statusAsOf: "2026-09-29",
      today: TODAY,
      developerConfirmed: false,
    });
    assert.equal(
      checkinNote(
        carried,
        { source: "unknown", confirmedAt: null, developerConfirmed: false },
        past,
      ),
      "carried forward from day 2026-09-29 · no reply on Mon 5 Oct",
    );
  });

  test("a reply that said nothing about the work is not read as silence", () => {
    // Elena: replied "thanks, nothing from me"; the backend closes the day as unknown.
    assert.equal(
      note({
        source: "unknown",
        statusAsOf: TODAY,
        today: TODAY,
        developerConfirmed: false,
        summary: "Replied without a status update. Current status is unknown.",
      }),
      "replied without a status · current status unknown",
    );
    // The same words on a person who never replied say nothing of the kind.
    assert.equal(
      note({
        source: "unknown",
        statusAsOf: TODAY,
        today: TODAY,
        developerConfirmed: false,
        summary: "No confirmed check-in after a nudge. Current status is unknown.",
      }),
      "no reply today",
    );
  });

  test("confirming an earlier day's status says what it asserts", () => {
    const carried = checkinProvenance({
      source: "confirmed",
      statusAsOf: "2026-10-05",
      today: TODAY,
      developerConfirmed: true,
    });
    assert.equal(
      confirmCaption(carried, say.day),
      "Confirming says the update from day 2026-10-05 still holds today, blockers included.",
    );
    const today = checkinProvenance({
      source: "confirmed",
      statusAsOf: TODAY,
      today: TODAY,
      developerConfirmed: true,
    });
    assert.equal(confirmCaption(today, say.day), null);
  });

  const hintFor = (over: Partial<Parameters<typeof checkinHint>[0]> = {}) =>
    checkinHint({
      state: "stale",
      source: "unknown",
      statusAsOf: "2026-10-05",
      summary: "No confirmed check-in after a nudge.",
      today: TODAY,
      podNames: ["Payments Pod"],
      ...over,
    });

  test("the hint says how the pods read it, in the board's own word", () => {
    assert.equal(
      hintFor(),
      "Payments Pod shows your check-in as carried forward until you confirm or correct it.",
    );
    assert.equal(
      hintFor({ source: "inferred", statusAsOf: TODAY }),
      "Payments Pod shows your check-in as inferred until you confirm or correct it.",
    );
    assert.equal(
      hintFor({ state: "partial", source: "partial", statusAsOf: TODAY }),
      "Payments Pod shows your check-in as partly replied until you confirm or correct it.",
    );
    assert.equal(
      hintFor({
        state: "missing",
        statusAsOf: null,
        podNames: ["Payments Pod", "Storefront Pod"],
      }),
      "Payments Pod and Storefront Pod show no reply from you. Silence is never read as green.",
    );
    assert.equal(
      hintFor({
        state: "partial",
        source: "partial",
        statusAsOf: TODAY,
        podNames: ["A", "B", "C"],
      }),
      "Your 3 pods show your check-in as partly replied until you confirm or correct it.",
    );
  });

  test("a reply with no status is said as that, not as silence", () => {
    assert.equal(
      hintFor({
        state: "missing",
        statusAsOf: TODAY,
        summary: "Replied without a status update. Current status is unknown.",
      }),
      "Payments Pod shows that you replied without a status, so your status stays unknown until you confirm or correct it.",
    );
  });

  test("nothing to flag once replied, with no pod to reach, or on a past day", () => {
    assert.equal(hintFor({ state: "confirmed", source: "confirmed", statusAsOf: TODAY }), null);
    assert.equal(hintFor({ podNames: [] }), null);
    assert.equal(hintFor({ pastDay: true }), null);
  });

  test("a reply that counts says what Confirm adds, and nothing on a past day", () => {
    const replied = checkinProvenance({
      source: "confirmed",
      statusAsOf: TODAY,
      today: TODAY,
      developerConfirmed: false,
    });
    assert.equal(
      confirmCaption(replied, say.day, false, true),
      "Your reply counts as today's status. Confirm says what was recorded from it is right; correct it if not.",
    );
    assert.equal(confirmCaption(replied, say.day, true, true), null);
    assert.equal(confirmCaption(replied, say.day, false, false), null);
    const confirmed = checkinProvenance({
      source: "confirmed",
      statusAsOf: TODAY,
      today: TODAY,
      developerConfirmed: true,
    });
    assert.equal(confirmCaption(confirmed, say.day, false, false), null);
  });

  test("on a past day nothing says to confirm or correct: both buttons are off", () => {
    assert.equal(hintFor({ pastDay: true, state: "missing", statusAsOf: null }), null);
    const carried = checkinProvenance({
      source: "confirmed",
      statusAsOf: "2026-10-05",
      today: TODAY,
      developerConfirmed: true,
    });
    assert.equal(confirmCaption(carried, say.day, true), null);
    assert.doesNotMatch(whyCheckinMatters(true), /until you confirm or correct it/);
    assert.match(whyCheckinMatters(true), /fed every rollup/);
    assert.match(whyCheckinMatters(false), /until you confirm or correct it/);
  });
});

describe("a correction restates every open blocker", () => {
  const status = {
    blockers: ["3-D Secure sandbox credentials still not provisioned"],
    blocker_details: [
      {
        blocker_id: "b1",
        description: "3-D Secure sandbox credentials still not provisioned",
        work_item_id: "CHK-103",
        work_item_name: "3-D Secure step-up",
        pod_id: "pod-payments",
        unattributed: false,
        first_seen_on: "2026-09-16",
        age_days: 20,
      },
    ],
  };

  test("structured details carry their ids", () => {
    const rows = blockerRows(status);
    assert.deepEqual(rows, [
      {
        key: "b1",
        blocker_id: "b1",
        description: "3-D Secure sandbox credentials still not provisioned",
        work_item_id: "CHK-103",
        pod_id: "pod-payments",
        age_days: 20,
      },
    ]);
  });

  test("with no details the flat list stands in, so saving never closes a blocker unseen", () => {
    const rows = blockerRows({
      blockers: ["Waiting on a key", NO_REPLY_BLOCKER],
      blocker_details: [],
    });
    assert.deepEqual(
      rows.map((row) => [row.blocker_id, row.description]),
      [[null, "Waiting on a key"]],
    );
  });

  test("a valid correction marks resolved rows and appends the new blocker", () => {
    const built = buildCorrection(blockerRows(status), {
      summary: "  Done with the sandbox setup  ",
      eta: "2",
      resolved: ["b1"],
      added: " Waiting on the key rotation ",
    });
    assert.ok(built.ok);
    assert.deepEqual(built.body, {
      summary: "Done with the sandbox setup",
      eta_change_days: 2,
      blocker_items: [
        {
          description: "3-D Secure sandbox credentials still not provisioned",
          resolved: true,
          blocker_id: "b1",
        },
        { description: "Waiting on the key rotation", resolved: false },
      ],
    });
  });

  test("a restated blocker carries its id, so the server keeps this one and not another", () => {
    const built = buildCorrection(blockerRows(status), {
      summary: "x",
      eta: "",
      resolved: [],
      added: "",
    });
    assert.ok(built.ok);
    for (const item of built.body.blocker_items ?? []) {
      // The id the status named, never the work item or pod the server inferred.
      assert.deepEqual(Object.keys(item).sort(), ["blocker_id", "description", "resolved"]);
    }
    assert.equal(built.body.blocker_items?.[0]?.blocker_id, "b1");
  });

  test("a blocker the flat list stands in for has no id to send, and a new one never has", () => {
    const rows = blockerRows({ blockers: ["Waiting on a key"], blocker_details: [] });
    const built = buildCorrection(rows, {
      summary: "x",
      eta: "",
      resolved: [],
      added: "Another one",
    });
    assert.ok(built.ok);
    assert.deepEqual(built.body.blocker_items, [
      { description: "Waiting on a key", resolved: false },
      { description: "Another one", resolved: false },
    ]);
  });

  test("two open blockers worded alike are two, told apart by their ids", () => {
    const twin = (key: string, description: string): BlockerRow => ({
      key,
      blocker_id: key,
      description,
      work_item_id: "CHK-14",
      pod_id: null,
      age_days: 3,
    });
    const rows = [twin("a", "Waiting on CHK-14 review"), twin("b", "waiting on CHK-14 review.")];
    const draft = { summary: "x", eta: "", resolved: ["a"], added: "" };
    // One kept, one closed: the server can now do exactly that, by id.
    const kept = buildCorrection(rows, draft);
    assert.ok(kept.ok);
    assert.deepEqual(kept.body.blocker_items, [
      { description: "Waiting on CHK-14 review", resolved: true, blocker_id: "a" },
      { description: "waiting on CHK-14 review.", resolved: false, blocker_id: "b" },
    ]);
    assert.ok(buildCorrection(rows, { ...draft, resolved: [] }).ok);
    assert.ok(buildCorrection(rows, { ...draft, resolved: ["a", "b"] }).ok);
  });

  test("without ids two open blockers worded alike still cannot be told apart", () => {
    const flat = blockerRows({
      blockers: ["Waiting on CHK-14 review", "waiting on CHK-14 review."],
      blocker_details: [],
    });
    const draft = { summary: "x", eta: "", resolved: [flat[0].key], added: "" };
    const refused = buildCorrection(flat, draft);
    assert.equal(refused.ok, false);
    assert.match(refused.ok ? "" : refused.message, /read the same/);
    assert.equal(buildCorrection(flat, { ...draft, resolved: [] }).ok, false);
    assert.ok(buildCorrection(flat, { ...draft, resolved: [flat[0].key, flat[1].key] }).ok);
  });

  test("an empty ETA is no change, and a blank summary is refused in words", () => {
    const ok = buildCorrection([], { summary: "On track", eta: "", resolved: [], added: "" });
    assert.ok(ok.ok);
    assert.equal(ok.body.eta_change_days, null);
    assert.deepEqual(buildCorrection([], { summary: "  ", eta: "", resolved: [], added: "" }), {
      ok: false,
      message: "Say what you did and what's next.",
    });
  });

  test("the server's limits are said before the round trip", () => {
    assert.deepEqual(buildCorrection([], { summary: "x", eta: "1.5", resolved: [], added: "" }), {
      ok: false,
      message: "The ETA change is a whole number of days, like 2 or -1.",
    });
    assert.deepEqual(buildCorrection([], { summary: "x", eta: "soon", resolved: [], added: "" }), {
      ok: false,
      message: "The ETA change is a whole number of days, like 2 or -1.",
    });
    assert.deepEqual(
      buildCorrection([], {
        summary: "x",
        eta: "",
        resolved: [],
        added: "y".repeat(MAX_BLOCKER_TEXT + 1),
      }),
      { ok: false, message: `A blocker is at most ${MAX_BLOCKER_TEXT} characters.` },
    );
    const twenty = Array.from({ length: 20 }, (_, i) => ({
      key: `b${i}`,
      blocker_id: `b${i}`,
      description: `blocker ${i}`,
      work_item_id: null,
      pod_id: null,
      age_days: 1,
    }));
    assert.deepEqual(
      buildCorrection(twenty, { summary: "x", eta: "", resolved: [], added: "one more" }),
      {
        ok: false,
        message: "At most 20 blockers can be listed at once.",
      },
    );
  });

  test("a new blocker worded like one kept open is refused: it would be a second copy", () => {
    const rows = blockerRows(status);
    const draft = { summary: "x", eta: "", resolved: [] as string[], added: "" };
    // Same words, other case, trailing full stop, extra spaces.
    const reworded = "  3-D  secure sandbox credentials still not provisioned. ";
    assert.deepEqual(buildCorrection(rows, { ...draft, added: reworded }), {
      ok: false,
      message: "That blocker is already on your list.",
    });
    assert.equal(blockerKey(reworded), blockerKey(status.blockers[0]));
    assert.ok(buildCorrection(rows, { ...draft, added: "A different blocker" }).ok);
  });

  test("one with an id that is marked resolved can be raised again: the server keeps them apart", () => {
    const rows = blockerRows(status);
    const built = buildCorrection(rows, {
      summary: "x",
      eta: "",
      resolved: ["b1"],
      added: "3-D secure sandbox credentials still not provisioned",
    });
    assert.ok(built.ok);
    assert.deepEqual(built.body.blocker_items, [
      {
        description: "3-D Secure sandbox credentials still not provisioned",
        resolved: true,
        blocker_id: "b1",
      },
      { description: "3-D secure sandbox credentials still not provisioned", resolved: false },
    ]);
  });

  test("without an id the server would count a new blocker as the listed one, so it is refused", () => {
    const flat = blockerRows({ blockers: ["Waiting on a key"], blocker_details: [] });
    const draft = { summary: "x", eta: "", resolved: [] as string[], added: "waiting on a key." };
    assert.deepEqual(buildCorrection(flat, draft), {
      ok: false,
      message: "That blocker is already on your list.",
    });
    assert.deepEqual(buildCorrection(flat, { ...draft, resolved: [flat[0].key] }), {
      ok: false,
      message: "That is the blocker you marked resolved. Untick it to keep it open.",
    });
  });

  test("only a person's own words are prefilled", () => {
    assert.equal(isOwnWords("confirmed"), true);
    assert.equal(isOwnWords("partial"), true);
    assert.equal(isOwnWords("unknown"), false);
    assert.equal(isOwnWords("stale"), false);
    assert.equal(isOwnWords("inferred"), false);
  });
});

test("the no-reply placeholder is recognised however the server words it, and nothing else is", () => {
  assert.equal(isNoReplyPlaceholder(NO_REPLY_BLOCKER), true);
  assert.equal(isNoReplyPlaceholder("  No confirmed reply. "), true);
  assert.equal(isNoReplyPlaceholder("Waiting on a key"), false);
  assert.equal(isNoReplyPlaceholder("no confirmed reply from the vendor"), false);
});

test("an empty check-in card is told by the 404's own detail, not a second request", () => {
  assert.match(
    noStatusWords("status is not available: no member record for this person"),
    /^You have no member record yet.*An admin adds you under Admin → Directory\.$/,
  );
  assert.equal(
    noStatusWords("status is not available: no status on record yet for this member"),
    "No check-in yet. Your first one comes in chat at your check-in time.",
  );
  // A server that has not been rebuilt answers the plain old detail, for both cases:
  // words that are true of both, and a way to be added if you are no member.
  const older = noStatusWords("status is not available");
  assert.match(older, /^No check-in to show\./);
  assert.match(older, /If you are no member yet, an admin adds you/);
  assert.equal(noStatusWords(null), older);
});
