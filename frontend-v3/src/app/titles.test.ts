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
      "Portfolio · OpenProgram",
      "Projects · OpenProgram",
      "Pods · OpenProgram",
      "Early warnings · OpenProgram",
      "Scope control · OpenProgram",
      "Reports · OpenProgram",
      "Review flow · OpenProgram",
      "Ask Ora · OpenProgram",
      "People & roles · OpenProgram",
      "Admin · OpenProgram",
    ],
  );
});

test("a project's page, a pod's page and a day report name what they show", () => {
  assert.equal(
    screenTitle("/projects/project-checkout", names),
    "Checkout Revamp · Projects · OpenProgram",
  );
  assert.equal(screenTitle("/pods/pod-payments", names), "Payments Pod · Pods · OpenProgram");
  assert.equal(
    screenTitle("/reports/project-checkout/daily", names),
    "Daily report · Checkout Revamp · Reports · OpenProgram",
  );
  assert.equal(screenTitle("/reports/briefs", names), "Briefs · Reports · OpenProgram");
});

test("a node nobody has named yet leaves its name out, never shows an id", () => {
  assert.equal(screenTitle("/projects/project-unknown", names), "Projects · OpenProgram");
  assert.equal(screenTitle("/pods/pod-unknown", names), "Pods · OpenProgram");
  assert.equal(
    screenTitle("/reports/project-unknown/daily", names),
    "Daily report · Reports · OpenProgram",
  );
});

test("an address no page owns falls back to the app's name, an old one too", () => {
  assert.equal(screenTitle("/"), "OpenProgram");
  assert.equal(screenTitle("/nowhere"), "OpenProgram");
  assert.equal(screenTitle("/constructor"), "OpenProgram");
  // The old tabs only redirect now: they never show long enough to be named.
  for (const old of ["/delivery", "/signals", "/coordination", "/chat"]) {
    assert.equal(screenTitle(old), "OpenProgram", old);
  }
});
