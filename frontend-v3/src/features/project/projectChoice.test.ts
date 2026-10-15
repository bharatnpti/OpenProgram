import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RECENT_KEPT,
  defaultProject,
  readRecent,
  recentKey,
  remembered,
  switcherSections,
  writeRecent,
  type ProjectOption,
  type RecentStore,
} from "./projectChoice.ts";

const option = (id: string, rag: ProjectOption["rag"], extra: Partial<ProjectOption> = {}) => ({
  id,
  name: extra.name ?? id,
  rag,
  programIds: ["program-digital"],
  jiraKey: null,
  mine: false,
  ...extra,
});

const PROGRAMS = [
  { id: "program-digital", name: "Digital Platform Program" },
  { id: "program-data", name: "Data Program" },
];
const OPTIONS: ProjectOption[] = [
  option("project-checkout", "amber", { name: "Checkout Revamp", mine: true, jiraKey: "CHK" }),
  option("project-identity", "red", { name: "Identity Platform", mine: true, jiraKey: "IDP" }),
  option("project-insights", "red", {
    name: "Customer Insights",
    programIds: ["program-data"],
    jiraKey: "INS",
  }),
  option("project-mobile", "green", { name: "Mobile App" }),
  option("project-loose", "unknown", { name: "Loose Ends", programIds: [] }),
];

test("a bare /projects opens the worst of mine, else the last opened, else the worst", () => {
  assert.equal(defaultProject(OPTIONS, []), "project-identity");
  // A recent pick never beats the person's own: it is a convenience, not the default.
  assert.equal(defaultProject(OPTIONS, ["project-mobile"]), "project-identity");
  const notMine = OPTIONS.map((item) => ({ ...item, mine: false }));
  assert.equal(defaultProject(notMine, ["gone", "project-mobile"]), "project-mobile");
  assert.equal(defaultProject(notMine, []), "project-insights", "red, then by name");
  assert.equal(defaultProject([], ["project-mobile"]), null);
});

test("the switcher lists mine, then recent, then by programme, each project once", () => {
  const sections = switcherSections(OPTIONS, ["project-mobile", "project-checkout"], PROGRAMS);
  assert.deepEqual(
    sections.map((section) => [section.label, section.options.map((item) => item.id)]),
    [
      ["Yours", ["project-identity", "project-checkout"]],
      ["Recent", ["project-mobile"]],
      ["Data Program", ["project-insights"]],
      ["Other projects", ["project-loose"]],
    ],
  );
});

test("with nothing of mine and nothing recent, the programmes alone, worst first in each", () => {
  const notMine = OPTIONS.map((item) => ({ ...item, mine: false }));
  assert.deepEqual(
    switcherSections(notMine, [], PROGRAMS).map((section) => [
      section.label,
      section.options.map((item) => item.id),
    ]),
    [
      ["Digital Platform Program", ["project-identity", "project-checkout", "project-mobile"]],
      ["Data Program", ["project-insights"]],
      ["Other projects", ["project-loose"]],
    ],
  );
  // One project in no programme alone is simply "All projects".
  assert.equal(
    switcherSections([option("x", null, { programIds: [] })], [], [])[0].label,
    "All projects",
  );
});

test("a search finds a project by its name, its programme or its Jira key", () => {
  const ids = (query: string) =>
    switcherSections(OPTIONS, [], PROGRAMS, query).flatMap((section) =>
      section.options.map((item) => item.id),
    );
  assert.deepEqual(ids("check"), ["project-checkout"]);
  assert.deepEqual(ids("ins"), ["project-insights"]);
  assert.deepEqual(ids("data program"), ["project-insights"]);
  assert.deepEqual(ids("idp"), ["project-identity"]);
  assert.deepEqual(ids("nothing like it"), []);
  assert.equal(ids("").length, OPTIONS.length);
});

test("recent keeps the newest five, each once, per person, and survives blocked storage", () => {
  let list: string[] = [];
  for (const id of ["a", "b", "c", "a", "d", "e", "f"]) list = remembered(list, id);
  assert.deepEqual(list, ["f", "e", "d", "a", "c"]);
  assert.equal(list.length, RECENT_KEPT);

  const kept = new Map<string, string>();
  const store: RecentStore = {
    getItem: (key) => kept.get(key) ?? null,
    setItem: (key, value) => void kept.set(key, value),
  };
  const key = recentKey("U1003");
  assert.notEqual(key, recentKey("U1006"), "two people on one browser keep their own");
  writeRecent(() => store, key, list);
  assert.deepEqual(
    readRecent(() => store, key),
    list,
  );
  kept.set(key, "not json");
  assert.deepEqual(
    readRecent(() => store, key),
    [],
  );
  kept.set(key, JSON.stringify({ not: "a list" }));
  assert.deepEqual(
    readRecent(() => store, key),
    [],
  );
  const blocked = () => {
    throw new Error("blocked");
  };
  assert.deepEqual(readRecent(blocked, key), []);
  assert.doesNotThrow(() => writeRecent(blocked, key, list));
});
