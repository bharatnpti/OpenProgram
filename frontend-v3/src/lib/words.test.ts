import assert from "node:assert/strict";
import { test } from "node:test";

import {
  checkinsLine,
  daysLabel,
  deliveryNote,
  plural,
  requestKindLabel,
  requestSentence,
  requestStatusLabel,
  signalAge,
  signalKindLabel,
  sourceLine,
  todayEyebrow,
} from "./words.ts";

test("a source reads in plain words, with confidence when the server gives one", () => {
  assert.equal(sourceLine("confirmed"), "confirmed");
  assert.equal(sourceLine("partial", 0.5), "partly answered · 50% confidence");
  // A person who never answered has no status: not "unknown".
  assert.equal(sourceLine("unknown"), "no status");
  assert.equal(sourceLine(null), "no status");
});

test("counts agree with their word", () => {
  assert.equal(plural(1, "watermelon", "watermelons"), "1 watermelon");
  assert.equal(plural(0, "drift finding", "drift findings"), "0 drift findings");
  assert.equal(plural(11, "person", "people"), "11 people");
});

test("a wait reads as days, or today", () => {
  assert.equal(daysLabel(0), "today");
  assert.equal(daysLabel(-1), "today");
  assert.equal(daysLabel(1), "1 day");
  assert.equal(daysLabel(16), "16 days");
});

test("the eyebrow names the program, every program, or none", () => {
  const day = todayEyebrow(null);
  assert.ok(!day.includes("·"));
  assert.equal(todayEyebrow("Digital Platform Program"), `${day} · Digital Platform Program`);
  assert.equal(todayEyebrow(["Platform", "Operations"]), `${day} · Platform · Operations`);
  assert.equal(todayEyebrow([]), day);
  assert.equal(todayEyebrow(undefined), day);
});

test("a request says what it asks of the person it waits on", () => {
  assert.equal(requestSentence("Sofia Bergmann", "review"), "Sofia Bergmann asks you for a review");
  assert.equal(requestSentence("Noah Weber", "input"), "Noah Weber asks for your input");
  assert.equal(requestSentence("Kai Thompson", "dependency"), "Kai Thompson depends on you");
  // A kind the backend adds later still reads as words, never as a snake_case key.
  assert.equal(requestSentence("Ira Novak", "access_grant"), "Ira Novak asks you for access grant");
});

test("request kinds and statuses are chips, not keys", () => {
  assert.equal(requestKindLabel("dependency"), "Dependency");
  assert.equal(requestKindLabel("sign_off"), "Sign off");
  assert.equal(requestStatusLabel("needs_resolution"), "Needs resolution");
  assert.equal(requestStatusLabel("acknowledged"), "Acknowledged");
});

test("a DM that did not arrive says so, and tells the requester what to do", () => {
  assert.deepEqual(deliveryNote("not_delivered", true), {
    text: "DM not delivered. Ask them directly.",
    bad: true,
  });
  assert.deepEqual(deliveryNote("not_delivered", false), { text: "DM not delivered", bad: true });
  assert.deepEqual(deliveryNote("retrying", false), {
    text: "DM not sent yet, retrying",
    bad: false,
  });
  assert.equal(deliveryNote(null, false), null);
});

test("a signal's kind is a category, with the rule or drift kind after it", () => {
  assert.equal(signalKindLabel("blocker"), "Blocker");
  assert.equal(signalKindLabel("unanswered"), "Check-in not answered");
  assert.equal(signalKindLabel("risk:stale_work_item"), "Risk · stale work item");
  assert.equal(
    signalKindLabel("drift:claimed_progress_no_activity"),
    "Drift · claimed progress no activity",
  );
  assert.equal(signalKindLabel("drift"), "Drift");
  assert.equal(signalKindLabel("something_new"), "Something new");
});

test("a signal's age is days open, or new on the day it started", () => {
  assert.equal(signalAge(3), "open 3d");
  assert.equal(signalAge(0), "new");
});

test("nobody asked yet is not 0 of 0 answered", () => {
  assert.equal(
    checkinsLine({ people: 13, asked: 0, answered: 0 }, null),
    "Check-ins today: none asked yet · 13 people in teams",
  );
  assert.equal(
    checkinsLine({ people: 1, asked: 0, answered: 0 }, null),
    "Check-ins today: none asked yet · 1 person in teams",
  );
  assert.equal(
    checkinsLine({ people: 0, asked: 0, answered: 0 }, null),
    "Check-ins today: nobody is in a team yet",
  );
  assert.equal(
    checkinsLine({ people: 11, asked: 11, answered: 9 }, "07:00"),
    "Check-ins today: 9 of 11 answered · asked from 07:00",
  );
  assert.equal(
    checkinsLine({ people: 11, asked: 11, answered: 9 }, null),
    "Check-ins today: 9 of 11 answered",
  );
});
