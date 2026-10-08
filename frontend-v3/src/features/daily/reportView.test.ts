import assert from "node:assert/strict";
import { test } from "node:test";

import {
  askParts,
  dayReportCountWords,
  localDay,
  noDayReportWords,
  outcomeLine,
  outcomeText,
  scheduleTime,
  sendConfirmation,
  todaysNote,
} from "./reportView.ts";

test("an ask line splits into its kind and its text", () => {
  assert.deepEqual(askParts("Fix: Sandbox credentials for 3-D Secure"), {
    kind: "Fix",
    text: "Sandbox credentials for 3-D Secure",
  });
  assert.deepEqual(askParts("Waited 9 days: escalated"), {
    kind: null,
    text: "Waited 9 days: escalated",
  });
});

test("the send confirmation names how many destinations receive it and where", () => {
  const { title, description } = sendConfirmation({
    name: "Checkout: end of day",
    destination_count: 3,
    audience_summary: "1 chat channel and 2 people by direct message",
  });
  assert.equal(title, "Send Checkout: end of day now?");
  assert.match(description, /^3 destinations receive it right away: 1 chat channel/);
});

test("a run says how many destinations it reached", () => {
  assert.equal(
    outcomeLine({
      outcomes: [
        { kind: "email", target: "a", label: "a", ok: true, detail: "" },
        { kind: "teams", target: "b", label: "b", ok: false, detail: "" },
      ],
    }),
    "1 of 2 delivered",
  );
  assert.equal(outcomeLine({ outcomes: [] }), "no destinations");
});

test("yesterday's note does not prefill today's", () => {
  const note = {
    report_id: "r",
    report_date: "2026-10-05",
    text: "Old",
    author: "u",
    updated_at: "2026-10-05T17:00:00Z",
  };
  assert.equal(todaysNote({ note }, "2026-10-06"), "");
  assert.equal(todaysNote({ note }, "2026-10-05"), "Old");
  assert.equal(todaysNote({ note: null }, "2026-10-06"), "");
});

test("today is the report's own day in its time zone, not the UTC date", () => {
  // 20:00 UTC on 6 Oct is already 7 Oct in India and still 6 Oct in Berlin.
  const evening = new Date("2026-10-06T20:00:00Z");
  assert.equal(localDay("Asia/Kolkata", evening), "2026-10-07");
  assert.equal(localDay("Europe/Berlin", evening), "2026-10-06");
  assert.equal(localDay("Not/AZone", evening), "2026-10-06");
});

test("the schedule's time reads without the seconds the server keeps", () => {
  assert.equal(scheduleTime("17:30:00"), "17:30");
  assert.equal(scheduleTime("09:05"), "09:05");
});

test("a send names every destination and what happened to it", () => {
  assert.equal(
    outcomeText({ label: "Ira Novak", ok: true, detail: "Sent as a direct message." }),
    "Ira Novak: Sent as a direct message.",
  );
  assert.equal(
    outcomeText({
      label: "checkout-leads@acme.example",
      ok: false,
      detail: " The mail server refused the message (550). ",
    }),
    "checkout-leads@acme.example: not delivered. The mail server refused the message (550).",
  );
  assert.equal(outcomeText({ label: "#checkout", ok: true, detail: "" }), "#checkout: delivered");
  assert.equal(outcomeText({ label: "Teams", ok: false, detail: "" }), "Teams: not delivered");
});

test("a project's day reports are counted only once they are read", () => {
  assert.equal(dayReportCountWords(undefined, false), "Loading day reports…");
  assert.equal(dayReportCountWords(undefined, true), "Day reports could not be read");
  assert.equal(dayReportCountWords(0, false), "No day report set up yet");
  assert.equal(dayReportCountWords(1, false), "1 day report");
  assert.equal(dayReportCountWords(3, false), "3 day reports");
});

test("an empty Daily page offers set-up only where this reader may, and names no other role", () => {
  // A role that never sets reports up is told nothing more, and needs no second read.
  assert.equal(noDayReportWords(false, "loading", false), "");
  // Waiting for where this reader may set one up: nothing is promised yet.
  assert.equal(noDayReportWords(true, "loading", false), "");
  assert.equal(
    noDayReportWords(true, "ready", true),
    "Set one up to send it at the end of each day.",
  );
  assert.equal(noDayReportWords(true, "ready", false), "");
  assert.equal(noDayReportWords(true, "failed", true), "");
});
