import assert from "node:assert/strict";
import { test } from "node:test";

import { TAB_SECTIONS, projectAnchors, projectSections } from "./sections.ts";

test("each of a project's sections lives on one tab, in one order", () => {
  assert.deepEqual(TAB_SECTIONS, {
    date: ["answer", "timeline", "requirements", "work"],
    warnings: ["warnings", "questions"],
    scope: ["baseline", "gates", "readiness", "hygiene"],
  });
  const all = Object.values(TAB_SECTIONS).flat();
  assert.equal(new Set(all).size, all.length, "no section is said on two tabs");
});

test("a tab draws the sections it has an element for, in its order", () => {
  const drawn = projectSections("date", {
    work: "w",
    answer: "a",
    timeline: null,
    requirements: false,
    // Not the date tab's: left out.
    gates: "g",
  }).map((section) => section.key);
  assert.deepEqual(drawn, ["answer", "work"]);
  assert.deepEqual(
    projectSections("scope", { hygiene: "h", gates: "g", baseline: undefined }).map(
      (section) => section.key,
    ),
    ["gates", "hygiene"],
  );
});

test("the in-page index names the sections drawn, and is left out when it would be short", () => {
  assert.deepEqual(
    projectAnchors(["answer", "timeline", "requirements", "work"]).map((anchor) => anchor.id),
    ["timeline", "stages", "work"],
  );
  // An executive's or a scrum master's date tab without stages: no index for two.
  assert.deepEqual(projectAnchors(["answer", "timeline", "work"]), []);
  assert.deepEqual(
    projectAnchors(["baseline", "gates", "readiness", "hygiene"]).map((anchor) => anchor.label),
    ["Against the plan", "Gates", "Readiness", "Jira hygiene"],
  );
  // A developer's scope tab: the gates alone.
  assert.deepEqual(projectAnchors(["gates"]), []);
});
