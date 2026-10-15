import assert from "node:assert/strict";
import { test } from "node:test";

import { NAV } from "./nav.ts";
import { screenTitle } from "./titles.ts";

const NAMES: Record<string, string> = {
  "pod:pod-payments": "Payments Pod",
  "project:project-checkout": "Checkout Revamp",
};
const names = (kind: string, id: string) => NAMES[`${kind}:${id}`];

test("each page of the sidebar has its own tab title, not the app's one name", () => {
  assert.deepEqual(
    NAV.map((item) => screenTitle(item.to)),
    [
      "Today · OpenProgram",
      "Check-in · OpenProgram",
      "Requests · OpenProgram",
      "Projects · OpenProgram",
      "Review flow · OpenProgram",
      "Ask Ora · OpenProgram",
      "People & roles · OpenProgram",
      "Admin · OpenProgram",
    ],
  );
});

test("the hub names the place, then the question its tab answers", () => {
  assert.equal(
    screenTitle("/portfolio/date", names),
    "All projects · Will we make the date? · OpenProgram",
  );
  assert.equal(
    screenTitle("/projects/project-checkout/warnings", names),
    "Checkout Revamp · Early warnings · OpenProgram",
  );
  assert.equal(
    screenTitle("/projects/project-checkout/pods/pod-payments/scope", names),
    "Payments Pod · Checkout Revamp · Scope control · OpenProgram",
  );
  assert.equal(
    screenTitle("/projects/project-checkout/reports", names),
    "Checkout Revamp · Reports · OpenProgram",
  );
  // A place whose tab the guard has not placed yet is just Projects.
  assert.equal(
    screenTitle("/projects/project-checkout", names),
    "Checkout Revamp · Projects · OpenProgram",
  );
});

test("a node nobody has named yet leaves its name out, never shows an id", () => {
  assert.equal(
    screenTitle("/projects/project-unknown/date", names),
    "Will we make the date? · OpenProgram",
  );
  assert.equal(
    screenTitle("/projects/project-checkout/pods/pod-unknown/warnings", names),
    "Checkout Revamp · Early warnings · OpenProgram",
  );
});

test("an address no page owns falls back to the app's name, an old one too", () => {
  assert.equal(screenTitle("/"), "OpenProgram");
  assert.equal(screenTitle("/nowhere"), "OpenProgram");
  assert.equal(screenTitle("/constructor"), "OpenProgram");
  // The old pages only redirect now: they never show long enough to be named.
  for (const old of [
    "/delivery",
    "/signals",
    "/coordination",
    "/chat",
    "/pods",
    "/warnings",
    "/scope",
  ]) {
    assert.equal(screenTitle(old), "OpenProgram", old);
  }
});
