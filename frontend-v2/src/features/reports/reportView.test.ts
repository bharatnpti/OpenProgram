import assert from "node:assert/strict";
import { test } from "node:test";

import {
  askParts,
  formatReportDay,
  peopleLine,
  progressWidth,
  sendConfirmation,
} from "./reportView.ts";

test("the send confirmation names how many destinations receive it and where", () => {
  const report = {
    name: "Checkout: end of day",
    destination_count: 3,
    audience_summary: "3 people by direct message",
  };
  assert.deepEqual(sendConfirmation(report), {
    title: "Send Checkout: end of day now?",
    description:
      "3 destinations receive it right away: 3 people by direct message. " +
      "Today's scheduled send still goes out at its time.",
  });
  assert.match(
    sendConfirmation({ ...report, destination_count: 1, audience_summary: "1 chat channel" })
      .description,
    /^1 destination receives it right away: 1 chat channel\./,
  );
});

test("an ask's kind is split off only when it is one of the report's kinds", () => {
  assert.deepEqual(askParts("Review: Mina Patel asked for a review (3 days)."), {
    kind: "Review",
    text: "Mina Patel asked for a review (3 days).",
  });
  assert.deepEqual(askParts("Fix: Test environment is down"), {
    kind: "Fix",
    text: "Test environment is down",
  });
  assert.deepEqual(askParts("CHK-12: sign off 3 criteria"), {
    kind: null,
    text: "CHK-12: sign off 3 criteria",
  });
  assert.deepEqual(askParts("No colon here"), { kind: null, text: "No colon here" });
});

test("the progress bar stays between empty and full", () => {
  assert.equal(progressWidth(31.25), 31.25);
  assert.equal(progressWidth(null), 0);
  assert.equal(progressWidth(undefined), 0);
  assert.equal(progressWidth(140), 100);
  assert.equal(progressWidth(-3), 0);
});

test("a report day reads as a short date", () => {
  assert.equal(formatReportDay("2026-10-06"), "Tue 6 Oct");
});

test("the people a report goes to read as names, with anyone unnamed counted", () => {
  const person = (names: string[], count = names.length) => ({
    kind: "person" as const,
    count,
    names,
  });
  assert.equal(
    peopleLine([person(["Asha Rao", "Ira Novak", "Mina Patel"])]),
    "Asha Rao, Ira Novak and Mina Patel",
  );
  assert.equal(peopleLine([person(["Asha Rao"], 2)]), "Asha Rao and 1 more");
  assert.equal(peopleLine([person(["Asha Rao"])]), "Asha Rao");
  assert.equal(peopleLine([person([], 2)]), null);
  assert.equal(peopleLine([{ kind: "email", count: 2, names: [] }]), null);
});
