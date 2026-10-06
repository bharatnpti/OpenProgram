import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  blockerKey,
  blockerRows,
  buildCorrection,
  checkinHint,
  checkinNote,
  checkinProvenance,
  checkinState,
  confirmCaption,
  isOwnWords,
  MAX_BLOCKER_TEXT,
  NO_REPLY_BLOCKER,
  podCheckinMeta,
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
    input: Parameters<typeof checkinProvenance>[0] & { confirmedAt?: string | null },
  ) => {
    const provenance = checkinProvenance(input);
    return checkinNote(
      provenance,
      {
        source: input.source,
        confirmedAt: input.confirmedAt ?? null,
        developerConfirmed: input.developerConfirmed,
      },
      say,
    );
  };

  test("a carried-forward status names its day and says it is not confirmed today", () => {
    assert.equal(
      note({
        source: "unknown",
        statusAsOf: "2026-09-29",
        today: TODAY,
        developerConfirmed: false,
      }),
      "carried forward from day 2026-09-29 · not confirmed today",
    );
  });

  test("today's status says how it was given", () => {
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
    assert.equal(
      note({ source: "confirmed", statusAsOf: TODAY, today: TODAY, developerConfirmed: false }),
      "answered in chat",
    );
    assert.equal(
      note({ source: "partial", statusAsOf: TODAY, today: TODAY, developerConfirmed: false }),
      "partly answered",
    );
    assert.equal(
      note({ source: "inferred", statusAsOf: TODAY, today: TODAY, developerConfirmed: false }),
      "inferred from delivery signals",
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

  test("the hint says how the pods read it, and nothing when it is fine or has no pod", () => {
    assert.equal(
      checkinHint("stale", ["Payments Pod"]),
      "Payments Pod shows your check-in as stale until you confirm or correct it.",
    );
    assert.equal(
      checkinHint("missing", ["Payments Pod", "Storefront Pod"]),
      "Payments Pod and Storefront Pod show your check-in as missing. Silence is never read as green.",
    );
    assert.equal(
      checkinHint("partial", ["A", "B", "C"]),
      "Your 3 pods show your check-in as partial until you confirm or correct it.",
    );
    assert.equal(checkinHint("confirmed", ["Payments Pod"]), null);
    assert.equal(checkinHint("stale", []), null);
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
          blocker_id: "b1",
          description: "3-D Secure sandbox credentials still not provisioned",
          work_item_id: "CHK-103",
          pod_id: "pod-payments",
          resolved: true,
        },
        { description: "Waiting on the key rotation", resolved: false },
      ],
    });
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

  test("a new blocker worded like a listed one is refused: the server would count it as the same", () => {
    const rows = blockerRows(status);
    const draft = { summary: "x", eta: "", resolved: [] as string[], added: "" };
    // Same words, other case, trailing full stop, extra spaces.
    const reworded = "  3-D  secure sandbox credentials still not provisioned. ";
    assert.deepEqual(buildCorrection(rows, { ...draft, added: reworded }), {
      ok: false,
      message: "That blocker is already on your list.",
    });
    assert.deepEqual(buildCorrection(rows, { ...draft, resolved: ["b1"], added: reworded }), {
      ok: false,
      message: "That is the blocker you marked resolved. Untick it to keep it open.",
    });
    assert.equal(blockerKey(reworded), blockerKey(status.blockers[0]));
    assert.ok(buildCorrection(rows, { ...draft, added: "A different blocker" }).ok);
  });

  test("only a person's own words are prefilled", () => {
    assert.equal(isOwnWords("confirmed"), true);
    assert.equal(isOwnWords("partial"), true);
    assert.equal(isOwnWords("unknown"), false);
    assert.equal(isOwnWords("stale"), false);
    assert.equal(isOwnWords("inferred"), false);
  });
});

describe("the scrum master's line under a person", () => {
  const dev = (over: Partial<Parameters<typeof podCheckinMeta>[0]>) => ({
    state: "confirmed" as const,
    source: "confirmed" as const,
    status_as_of: TODAY,
    summary: "Merged the handler",
    ...over,
  });

  test("today's answers say today", () => {
    assert.equal(podCheckinMeta(dev({}), TODAY, say), "confirmed today · Merged the handler");
    assert.equal(
      podCheckinMeta(dev({ state: "partial", source: "partial" }), TODAY, say),
      "partly answered today · Merged the handler",
    );
  });

  test("an earlier day's status names its day, so confirmed never reads as answered today", () => {
    assert.equal(
      podCheckinMeta(dev({ state: "stale", status_as_of: "2026-10-02" }), TODAY, say),
      "confirmed day 2026-10-02, nothing today · Merged the handler",
    );
    assert.equal(
      podCheckinMeta(
        dev({ state: "stale", source: "unknown", status_as_of: "2026-09-29", summary: "" }),
        TODAY,
        say,
      ),
      "no reply since day 2026-09-29",
    );
  });

  test("a status inferred today, and a person with none", () => {
    assert.equal(
      podCheckinMeta(dev({ state: "stale", source: "inferred", summary: "" }), TODAY, say),
      "inferred from delivery signals",
    );
    assert.equal(
      podCheckinMeta(
        dev({
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
