import assert from "node:assert/strict";
import { test } from "node:test";

import { opens, rowHeight, treeRows, type TreeProject, type TreeRow } from "./switcherTree.ts";

const pod = (id: string, name: string, verdict: TreeProject["verdict"] = null) => ({
  id,
  name,
  verdict,
  total: verdict ? 3 : null,
  own: false,
});

const project = (
  id: string,
  name: string,
  programIds: string[],
  extra: Partial<TreeProject> = {},
): TreeProject => ({
  id,
  name,
  rag: "unknown",
  programIds,
  jiraKey: null,
  mine: false,
  verdict: null,
  pods: [],
  workstreams: [],
  ...extra,
});

const programs = [
  { id: "prog-a", name: "Digital Platform Program" },
  { id: "prog-b", name: "Data Program" },
];
const projects = [
  project("checkout", "Checkout Revamp", ["prog-a"], {
    rag: "red",
    jiraKey: "CHK",
    mine: true,
    verdict: "off_track",
    pods: [
      pod("payments", "Payments Pod", "at_risk"),
      pod("storefront", "Storefront Pod", "on_track"),
    ],
    workstreams: ["Payments API"],
  }),
  project("identity", "Identity Platform", ["prog-a"], { rag: "amber", verdict: "at_risk" }),
  project("insights", "Insights Hub", ["prog-b"], {
    verdict: "no_date",
    pods: [pod("data", "Data Pod")],
  }),
];
const state = {
  query: "",
  chips: [],
  toggledGroups: new Set<string>(),
  expanded: new Set<string>(),
  withAll: true,
};
const shape = (rows: TreeRow[]) =>
  rows.map((row) =>
    row.kind === "all"
      ? "all"
      : row.kind === "heading"
        ? `# ${row.label}${row.folded ? " (folded)" : ""}`
        : row.kind === "project"
          ? `${row.project.name}${row.expanded ? " ▾" : ""}`
          : `  ${row.pod.name}`,
  );

test("All projects, then Yours and Recent, then each programme; groups after the first are folded", () => {
  assert.deepEqual(shape(treeRows(projects, ["insights"], programs, state)), [
    "all",
    "# Yours",
    "Checkout Revamp",
    "# Recent",
    "Insights Hub",
    "# Digital Platform Program",
    "Identity Platform",
  ]);
  assert.deepEqual(shape(treeRows(projects, [], programs, state)), [
    "all",
    "# Yours",
    "Checkout Revamp",
    "# Digital Platform Program",
    "Identity Platform",
    "# Data Program (folded)",
  ]);
  // Unfolded by a pick; the developer's tree has no All row.
  assert.deepEqual(
    shape(
      treeRows(projects, [], programs, {
        ...state,
        withAll: false,
        toggledGroups: new Set(["program:prog-b"]),
      }),
    ),
    [
      "# Yours",
      "Checkout Revamp",
      "# Digital Platform Program",
      "Identity Platform",
      "# Data Program",
      "Insights Hub",
    ],
  );
});

test("a project opens to its pods; one with none has nothing to open", () => {
  const rows = treeRows(projects, [], programs, {
    ...state,
    expanded: new Set(["checkout", "identity"]),
  });
  assert.deepEqual(shape(rows).slice(0, 5), [
    "all",
    "# Yours",
    "Checkout Revamp ▾",
    "  Payments Pod",
    "  Storefront Pod",
  ]);
  assert.ok(shape(rows).includes("Identity Platform"), "no pods: not shown as open");
});

test("a search covers names, programmes and Jira keys, and a pod's name opens its project", () => {
  assert.deepEqual(shape(treeRows(projects, [], programs, { ...state, query: "chk" })), [
    "# Yours",
    "Checkout Revamp",
  ]);
  assert.deepEqual(shape(treeRows(projects, [], programs, { ...state, query: "data program" })), [
    "# Data Program",
    "Insights Hub",
  ]);
  // Found by its pod: open, with only the pods that matched.
  assert.deepEqual(shape(treeRows(projects, [], programs, { ...state, query: "store" })), [
    "# Yours",
    "Checkout Revamp ▾",
    "  Storefront Pod",
  ]);
  assert.deepEqual(treeRows(projects, [], programs, { ...state, query: "nothing like it" }), []);
});

test("the chips keep the projects with that answer, every group open", () => {
  assert.deepEqual(
    shape(treeRows(projects, [], programs, { ...state, chips: ["at_risk", "no_date"] })),
    ["# Digital Platform Program", "Identity Platform", "# Data Program", "Insights Hub"],
  );
  assert.deepEqual(shape(treeRows(projects, [], programs, { ...state, chips: ["off_track"] })), [
    "# Yours",
    "Checkout Revamp",
  ]);
});

test("the arrow keys stop on what opens, and each row has its height for windowing", () => {
  const rows = treeRows(projects, [], programs, { ...state, expanded: new Set(["checkout"]) });
  assert.deepEqual(
    rows.map((row) => opens(row)),
    rows.map((row) => row.kind !== "heading"),
  );
  assert.deepEqual(
    rows.slice(0, 4).map(rowHeight),
    [46, 34, 62, 42],
    "All, a heading, a project with workstreams, a pod",
  );
});
