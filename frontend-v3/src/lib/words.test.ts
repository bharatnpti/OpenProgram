import assert from "node:assert/strict";
import { test } from "node:test";

import {
  checkinsLine,
  daysLabel,
  deliveryNote,
  firstName,
  greetingTitle,
  greetingWord,
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
  // A task's or blocker's owner reported it: "confirmed" is a person's own act on their check-in.
  assert.equal(sourceLine("confirmed"), "reported");
  assert.equal(sourceLine("partial", 0.5), "partly reported · 50% confidence");
  // An owner who never reported has no status: not "unknown".
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

// A fixed clock, so the year the eyebrow leaves out is the same whenever this runs.
const NOW = new Date(2026, 9, 7, 14, 0);

test("the eyebrow names the program, every program, or none", () => {
  const day = todayEyebrow(null, null, NOW);
  assert.ok(!day.includes("·"));
  assert.equal(
    todayEyebrow("Digital Platform Program", null, NOW),
    `${day} · Digital Platform Program`,
  );
  assert.equal(
    todayEyebrow(["Platform", "Operations"], null, NOW),
    `${day} · Platform · Operations`,
  );
  assert.equal(todayEyebrow([], null, NOW), day);
  assert.equal(todayEyebrow(undefined, null, NOW), day);
});

test("the eyebrow writes the day the way every other screen does, not in US order", () => {
  assert.equal(todayEyebrow(null, null, NOW), "Wed 7 Oct");
  assert.equal(todayEyebrow(null, "2026-09-28", NOW), "Mon 28 Sept");
  assert.equal(
    todayEyebrow("Digital Platform Program", "2026-10-07", NOW),
    "Wed 7 Oct · Digital Platform Program",
  );
  // A day of another year says which year.
  assert.equal(todayEyebrow(null, "2025-12-31", NOW), "Wed 31 Dec 2025");
  // The server may give a timestamp for the day; only the day is read.
  assert.equal(todayEyebrow(null, "2026-10-07T00:07:00+05:30", NOW), "Wed 7 Oct");
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
  assert.equal(signalKindLabel("unanswered"), "No reply to the check-in");
  assert.equal(signalKindLabel("partial"), "Check-in partly replied to");
  assert.equal(signalKindLabel("inferred"), "Status inferred, no reply");
  assert.equal(signalKindLabel("stale"), "Update carried forward");
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

test("nobody asked yet is not 0 of 0 replied", () => {
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
    "Check-ins today: 9 of 11 replied · asked from 07:00",
  );
  assert.equal(
    checkinsLine({ people: 11, asked: 11, answered: 9 }, null),
    "Check-ins today: 9 of 11 replied",
  );
});

test("the day's check-in count names the day shown, not always today", () => {
  const counts = { people: 10, asked: 10, answered: 0 };
  assert.equal(
    checkinsLine(counts, "13:16"),
    "Check-ins today: 0 of 10 replied · asked from 13:16",
  );
  assert.equal(
    checkinsLine(counts, "13:16", "on Mon 5 Oct"),
    "Check-ins on Mon 5 Oct: 0 of 10 replied · asked from 13:16",
  );
  assert.equal(
    checkinsLine({ people: 10, asked: 0, answered: 0 }, null, "on Mon 5 Oct"),
    "Check-ins on Mon 5 Oct: none asked yet · 10 people in teams",
  );
});

test("a person is greeted by their first name, from the name the header shows", () => {
  assert.equal(firstName("Liam Chen"), "Liam");
  assert.equal(firstName("  Mina   Patel "), "Mina");
  assert.equal(firstName("Elena"), "Elena");
  // Some directories write the surname first.
  assert.equal(firstName("Chen, Liam"), "Liam");
  assert.equal(firstName("Chen,"), "Chen");
});

test("a name that is no name gives no first name, never a guess", () => {
  assert.equal(firstName(null), null);
  assert.equal(firstName(undefined), null);
  assert.equal(firstName("   "), null);
  assert.equal(firstName("liam.chen@example.com"), null);
});

test("the greeting names the person, else the role they are viewing as", () => {
  const morning = new Date(2026, 9, 7, 9, 30);
  const evening = new Date(2026, 9, 7, 19, 30);
  assert.equal(greetingWord(morning), "Good morning");
  assert.equal(greetingWord(new Date(2026, 9, 7, 12, 0)), "Good afternoon");
  assert.equal(greetingWord(evening), "Good evening");
  assert.equal(greetingTitle("Liam", "Developer", morning), "Good morning, Liam");
  assert.equal(greetingTitle(null, "Scrum Master", morning), "Good morning, Scrum Master");
  assert.equal(greetingTitle("", "Admin", evening), "Good evening, Admin");
});
